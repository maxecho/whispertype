"""Тест расшифровки кусками во время записи.

Проверяем три вещи:
1) склеенный из кусков текст совпадает по смыслу с расшифровкой целиком;
2) ожидание после остановки — это только хвост, а не вся диктовка;
3) фоновая расшифровка не мешает микрофону (запись не теряет сэмплы).
"""

import os
import subprocess
import sys
import time
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from whisperapp.config import load_config  # noqa: E402
from whisperapp.recorder import SAMPLE_RATE, Recorder  # noqa: E402
from whisperapp.transcriber import Transcriber, quietest_split  # noqa: E402

WAV = "/tmp/whisper_stream_test.wav"
PHRASES = [
    "Привет, это тест локального распознавания голоса на макбуке.",
    "Отправь медиаплан по заказу на следующую неделю, маржа примерно двадцать процентов.",
    "Завтра в одиннадцать созвон с командой, нужно подготовить сводку по выручке за квартал.",
    "Не забудь добавить в презентацию слайд про динамику продаж и два примера кампаний.",
]

fails = []


def check(name, condition, detail=""):
    if not condition:
        fails.append(name)
    print(f"  {'✓' if condition else '✗'} {name}{': ' + detail if detail else ''}")


def synth(text):
    subprocess.run(
        ["say", "-v", "Milena", "-o", WAV, "--file-format=WAVE",
         f"--data-format=LEI16@{SAMPLE_RATE}", text],
        check=True,
    )
    with wave.open(WAV, "rb") as source:
        raw = source.readframes(source.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def long_audio(seconds=60):
    pause = np.zeros(int(SAMPLE_RATE * 0.6), dtype=np.float32)
    pieces, index = [], 0
    while sum(p.size for p in pieces) < seconds * SAMPLE_RATE:
        pieces.append(synth(PHRASES[index % len(PHRASES)]))
        pieces.append(pause)
        index += 1
    return np.concatenate(pieces)[: seconds * SAMPLE_RATE]


def words(text):
    return set(text.lower().replace(",", "").replace(".", "").split())


# --- разрез по тишине ------------------------------------------------------

audio = np.ones(SAMPLE_RATE * 10, dtype=np.float32) * 0.5
audio[SAMPLE_RATE * 4 : SAMPLE_RATE * 5] = 0.0  # секунда тишины в середине
cut = quietest_split(audio, SAMPLE_RATE * 2, SAMPLE_RATE * 8)
check(
    "разрез попадает в тишину",
    SAMPLE_RATE * 4 <= cut <= SAMPLE_RATE * 5,
    f"позиция {cut / SAMPLE_RATE:.2f} с",
)

# --- корректность и задержка ----------------------------------------------

cfg = load_config()
transcriber = Transcriber(cfg)
print("\nЗагружаю распознавание…")
if not transcriber.warmup():
    print("Не загрузилось:", transcriber.error)
    sys.exit(1)

print("Готовлю минуту синтетической речи…")
recording = long_audio(60)

# эталон: вся минута целиком, как было до ускорения
started = time.monotonic()
reference = transcriber.transcribe(recording)
whole_s = time.monotonic() - started

# имитация живой записи: отдаём звук по секунде, между кусками даём воркеру
# дорасшифровать (в жизни это время скрыто самой записью)
session = transcriber.start_session()
for second in range(1, 61):
    session.feed(recording[: second * SAMPLE_RATE])
    for future in list(session._futures):
        future.result()

started = time.monotonic()
stitched = session.finish(recording)
tail_s = time.monotonic() - started

print(f"\n  целиком: {whole_s:.1f} с ожидания")
print(f"  кусками: {tail_s:.1f} с ожидания (кусков в фоне: {session.chunks_started})")
print(f"  эталон,  первые 100: {reference[:100]}")
print(f"  кусками, первые 100: {stitched[:100]}")

check("куски реально резались", session.chunks_started >= 2, str(session.chunks_started))
check("ожидание сократилось минимум вдвое", tail_s < whole_s / 2, f"{whole_s:.1f} → {tail_s:.1f} с")

missing = words(reference) - words(stitched)
check(
    "текст не потерял слова",
    len(missing) <= max(2, len(words(reference)) // 20),
    f"расхождение: {sorted(missing)[:5] or 'нет'}",
)

# --- фоновая расшифровка не мешает микрофону ------------------------------

print("\nПишу с микрофона 15 с, параллельно гоняя распознавание…")
recorder = Recorder(cfg.get("input_device"))
recorder.start()
wall = time.monotonic()
busy_until = wall + 15
while time.monotonic() < busy_until:
    transcriber.transcribe(recording[: SAMPLE_RATE * 25])  # нагрузка как от куска
captured = recorder.stop()
wall = time.monotonic() - wall

expected = wall * SAMPLE_RATE
check(
    "запись не потеряла сэмплы",
    abs(captured.size - expected) < SAMPLE_RATE * 0.5 and recorder.overflows == 0,
    f"записано {captured.size / SAMPLE_RATE:.2f} с за {wall:.2f} с, переполнений: {recorder.overflows}",
)

print("\nПровалено:", fails or "ничего")
sys.exit(1 if fails else 0)
