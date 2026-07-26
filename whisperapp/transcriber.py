"""Локальная расшифровка через mlx-whisper (Apple GPU/Neural Engine)."""

import logging
import re
import threading
from functools import lru_cache

import numpy as np

from .recorder import SAMPLE_RATE

log = logging.getLogger("transcriber")

# Whisper любит зацикливаться на тишине — эти фразы он выдумывает чаще всего.
_HALLUCINATIONS = {
    "субтитры создавал dimatorzok",
    "субтитры сделал dimatorzok",
    "продолжение следует...",
    "продолжение следует",
    "спасибо за просмотр!",
    "спасибо за просмотр",
    "спасибо за внимание!",
    "спасибо за внимание",
    "редактор субтитров а.синецкая корректор а.егорова",
    "thank you for watching!",
    "thanks for watching!",
    "you",
    ".",
}


def _ensure_model_cache():
    """mlx_whisper заново читает веса с диска на каждый вызов — кэшируем сами.

    Обращаемся к модулю через sys.modules: атрибут mlx_whisper.transcribe —
    это одноимённая функция, которая затеняет модуль.
    """
    import importlib
    import sys

    mod = sys.modules.get("mlx_whisper.transcribe") or importlib.import_module(
        "mlx_whisper.transcribe"
    )
    if not hasattr(mod.load_model, "cache_info"):
        mod.load_model = lru_cache(maxsize=2)(mod.load_model)


def clean(text, cfg):
    text = (text or "").strip()
    if cfg["cleanup"].get("collapse_whitespace", True):
        text = re.sub(r"\s+", " ", text)
    text = apply_replacements(text, cfg.get("replacements"))
    if text.lower().strip(" .!?") in _HALLUCINATIONS or text.lower() in _HALLUCINATIONS:
        log.info("Отбросил галлюцинацию модели: %r", text)
        return ""
    if cfg["cleanup"].get("strip_trailing_period") and text.endswith("."):
        text = text[:-1]
    return text


class Transcriber:
    def __init__(self, cfg):
        self.cfg = cfg
        self._lock = threading.Lock()
        self.ready = False
        self.error = None

    @property
    def model(self):
        return self.cfg["model"]

    def _run(self, audio, extra_prompt=None):
        import mlx_whisper

        _ensure_model_cache()
        language = self.cfg.get("language") or None
        prompt = (self.cfg.get("initial_prompt") or "").strip()
        if extra_prompt:
            # хвост уже расшифрованного — модель продолжает мысль, а не начинает заново
            prompt = f"{prompt} {extra_prompt}".strip()
        return mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=self.model,
            language=language,
            initial_prompt=prompt or None,
            # именно None: при False mlx_whisper рисует прогресс-бар в stderr
            verbose=None,
            # для короткой диктовки контекст прошлых окон только вредит
            condition_on_previous_text=False,
            temperature=0.0,
        )

    def _pin_memory(self):
        """Прибивает веса модели в памяти, чтобы macOS не выгружала их в своп.

        Без этого на машинах с небольшой памятью каждая диктовка после паузы
        начинается с подгрузки весов с диска — и «быстрая» модель ощущается
        медленной. Прибиваем ровно столько, сколько занято после прогрева.
        """
        try:
            import mlx.core as mx

            active = mx.get_active_memory()
            mx.set_wired_limit(active + 256 * 1024 * 1024)
            log.info("Веса прибиты в памяти: %.0f МБ", active / 2**20)
        except Exception:  # noqa: BLE001
            log.warning("Не смог прибить веса в памяти — продолжаю без этого", exc_info=True)

    def warmup(self):
        """Прогревает модель на 0.5 с тишины, чтобы первая реальная диктовка не тормозила."""
        try:
            with self._lock:
                self._run(np.zeros(SAMPLE_RATE // 2, dtype=np.float32))
            self._pin_memory()
            self.ready = True
            self.error = None
            log.info("Модель готова: %s", self.model)
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            log.exception("Не смог загрузить модель %s", self.model)
        return self.ready

    def transcribe(self, audio, extra_prompt=None):
        if audio.size == 0:
            return ""
        with self._lock:
            result = self._run(audio, extra_prompt=extra_prompt)
        text = clean(result.get("text", ""), self.cfg)
        log.info(
            "Расшифровано %.1f с, язык=%s, символов=%d",
            audio.size / SAMPLE_RATE,
            result.get("language"),
            len(text),
        )
        return text

    def start_session(self):
        return DictationSession(self)


def quietest_split(audio, lo, hi, window=0.2):
    """Точка тишины внутри audio[lo:hi] — чтобы резать между словами, а не по слову.

    Ищем 200-миллисекундный участок с минимальной громкостью и возвращаем его центр.
    """
    span = audio[lo:hi]
    win = max(1, int(SAMPLE_RATE * window))
    if span.size <= win:
        return hi
    # среднеквадратичная громкость скользящим окном через кумулятивную сумму
    squares = np.concatenate(([0.0], np.cumsum(span.astype(np.float64) ** 2)))
    energy = squares[win:] - squares[:-win]
    return lo + int(np.argmin(energy)) + win // 2


class DictationSession:
    """Расшифровка длинной диктовки кусками, пока запись ещё идёт.

    Пока человек говорит, готовые куски (режем по паузам, примерно раз в
    CHUNK_SECONDS) уходят в фоновый поток. После остановки остаётся только
    хвост — то есть ожидание почти не зависит от длины диктовки.
    """

    FIRST_CHUNK_SECONDS = 8     # первый кусок режем рано: чтобы живой текст
                                # появился через секунды, а не через полминуты
    CHUNK_SECONDS = 20          # дальше копим больше — меньше швов в тексте
    SEARCH_SECONDS = 6          # в этом окне в конце куска ищем паузу для разреза
    PROMPT_TAIL_CHARS = 150     # сколько хвоста предыдущего текста дать модели

    def __init__(self, transcriber):
        import concurrent.futures

        self.transcriber = transcriber
        self._consumed = 0          # столько сэмплов уже отправлено в работу
        self._texts = []            # готовые куски, заполняет воркер по порядку
        self._futures = []
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="dictation"
        )
        self._cancelled = False

    @property
    def chunks_started(self):
        return len(self._futures)

    def feed(self, audio):
        """Вызывается по ходу записи со всем накопленным звуком."""
        if self._cancelled:
            return
        while True:
            seconds = self.FIRST_CHUNK_SECONDS if not self._futures else self.CHUNK_SECONDS
            chunk_len = int(seconds * SAMPLE_RATE)
            if audio.size - self._consumed < chunk_len:
                break
            hi = self._consumed + chunk_len
            lo = hi - int(min(self.SEARCH_SECONDS, seconds / 3) * SAMPLE_RATE)
            cut = quietest_split(audio, max(lo, self._consumed), hi)
            chunk = np.array(audio[self._consumed:cut])  # копия: буфер ещё растёт
            self._consumed = cut
            self._futures.append(self._pool.submit(self._work, chunk))

    def text_so_far(self):
        """Что расшифровано на данный момент — для живого показа во время речи."""
        return " ".join(self._texts).strip()

    def _work(self, chunk):
        if self._cancelled:
            return
        prompt = " ".join(self._texts)[-self.PROMPT_TAIL_CHARS:] or None
        text = self.transcriber.transcribe(chunk, extra_prompt=prompt)
        if text:
            self._texts.append(text)

    def finish(self, audio):
        """Запись остановлена: дожидаемся кусков, расшифровываем хвост, склеиваем."""
        for future in self._futures:
            future.result()
        tail = audio[self._consumed:]
        prompt = " ".join(self._texts)[-self.PROMPT_TAIL_CHARS:] or None
        tail_text = self.transcriber.transcribe(tail, extra_prompt=prompt)
        self._pool.shutdown(wait=False)
        pieces = self._texts + ([tail_text] if tail_text else [])
        return " ".join(pieces).strip()

    def cancel(self):
        self._cancelled = True
        self._pool.shutdown(wait=False, cancel_futures=True)


def apply_replacements(text, replacements):
    """Подставляет «как писать» вместо «как слышится».

    Один проход общим выражением, а не замена за заменой: иначе результат
    одной замены попадал бы под следующую («а→б», затем «б→в» давало «в»).
    Длинные варианты стоят первыми — регулярное выражение выбирает первую
    подходящую ветку, иначе «медиа план» перехватил бы короткий «план».
    Регистр первой буквы сохраняется: услышанное в начале предложения слово
    и заменится с заглавной.
    """
    if not replacements:
        return text
    lookup = {heard.lower(): written for heard, written in replacements.items() if heard.strip()}
    if not lookup:
        return text

    pattern = re.compile(
        "|".join(
            rf"(?<!\w){re.escape(heard)}(?!\w)"
            for heard in sorted(lookup, key=len, reverse=True)
        ),
        re.IGNORECASE,
    )

    def substitute(match):
        found = match.group(0)
        written = lookup.get(found.lower(), found)
        if found[:1].isupper() and written[:1].islower():
            return written[:1].upper() + written[1:]
        return written

    return pattern.sub(substitute, text)


def parse_replacements(raw):
    """Разбирает строки вида «слышится = пишется» в словарь."""
    result = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        heard, written = line.split("=", 1)
        heard, written = heard.strip(), written.strip()
        if heard:
            result[heard] = written
    return result


def format_replacements(replacements):
    """Обратно в текст — то, что человек увидит в окне редактирования."""
    return "\n".join(f"{heard} = {written}" for heard, written in sorted(replacements.items()))
