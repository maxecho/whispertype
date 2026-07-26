"""Замер скорости распознавания: обычная модель против q4, целиком против кусков.

    .venv/bin/python tools/bench.py
"""

import os
import pathlib
import subprocess
import sys
import time
import wave

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from whisperapp.config import load_config  # noqa: E402
from whisperapp.recorder import SAMPLE_RATE  # noqa: E402
from whisperapp.transcriber import Transcriber  # noqa: E402

WAV = "/tmp/whisper_bench.wav"

PHRASES = [
    "Привет, это тест локального распознавания голоса на макбуке.",
    "Отправь медиаплан по заказу на следующую неделю, маржа примерно двадцать процентов.",
    "Завтра в одиннадцать созвон с командой, нужно подготовить сводку по выручке за квартал.",
    "Не забудь добавить в презентацию слайд про динамику продаж и два примера кампаний.",
]


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
    """Склеиваем фразы с паузами до нужной длины — имитация живой диктовки."""
    pause = np.zeros(int(SAMPLE_RATE * 0.6), dtype=np.float32)
    pieces, index = [], 0
    while sum(p.size for p in pieces) < seconds * SAMPLE_RATE:
        pieces.append(synth(PHRASES[index % len(PHRASES)]))
        pieces.append(pause)
        index += 1
    return np.concatenate(pieces)[: seconds * SAMPLE_RATE]


def run(model, short, long):
    cfg = load_config()
    cfg["model"] = model
    transcriber = Transcriber(cfg)

    started = time.monotonic()
    if not transcriber.warmup():
        print(f"  {model}: не загрузилась — {transcriber.error}")
        return None
    load_s = time.monotonic() - started

    # прогретый повтор, чтобы мерить чистую работу
    transcriber.transcribe(short)

    t0 = time.monotonic()
    text_short = transcriber.transcribe(short)
    short_s = time.monotonic() - t0

    t0 = time.monotonic()
    text_long = transcriber.transcribe(long)
    long_s = time.monotonic() - t0

    print(f"\n=== {model} ===")
    print(f"  загрузка: {load_s:.1f} с")
    print(f"  короткая ({short.size / SAMPLE_RATE:.1f} с): {short_s:.2f} с")
    print(f"  длинная  ({long.size / SAMPLE_RATE:.0f} с): {long_s:.2f} с")
    print(f"  короткая → {text_short}")
    print(f"  длинная, первые 120 знаков → {text_long[:120]}")
    return {"short": short_s, "long": long_s, "text_long": text_long}


def main():
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    print("Готовлю звук…")
    short = synth(PHRASES[1])
    long = long_audio(60)

    results = {}
    for model in (
        "mlx-community/whisper-large-v3-turbo",
        "mlx-community/whisper-large-v3-turbo-q4",
    ):
        results[model] = run(model, short, long)

    return results


if __name__ == "__main__":
    main()
