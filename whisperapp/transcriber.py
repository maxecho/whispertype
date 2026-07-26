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

    def _run(self, audio):
        import mlx_whisper

        _ensure_model_cache()
        language = self.cfg.get("language") or None
        prompt = (self.cfg.get("initial_prompt") or "").strip() or None
        return mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=self.model,
            language=language,
            initial_prompt=prompt,
            # именно None: при False mlx_whisper рисует прогресс-бар в stderr
            verbose=None,
            # для короткой диктовки контекст прошлых окон только вредит
            condition_on_previous_text=False,
            temperature=0.0,
        )

    def warmup(self):
        """Прогревает модель на 0.5 с тишины, чтобы первая реальная диктовка не тормозила."""
        try:
            with self._lock:
                self._run(np.zeros(SAMPLE_RATE // 2, dtype=np.float32))
            self.ready = True
            self.error = None
            log.info("Модель готова: %s", self.model)
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            log.exception("Не смог загрузить модель %s", self.model)
        return self.ready

    def transcribe(self, audio):
        if audio.size == 0:
            return ""
        with self._lock:
            result = self._run(audio)
        text = clean(result.get("text", ""), self.cfg)
        log.info(
            "Расшифровано %.1f с, язык=%s, символов=%d",
            audio.size / SAMPLE_RATE,
            result.get("language"),
            len(text),
        )
        return text
