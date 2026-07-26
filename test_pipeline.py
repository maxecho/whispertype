"""Проверка распознавания без микрофона.

Синтезируем речь системным голосом и прогоняем через тот же код, что и живую
диктовку. Микрофон и разрешения не нужны.
"""

import os
import subprocess
import sys
import time
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from whisperapp.config import load_config  # noqa: E402
from whisperapp.recorder import SAMPLE_RATE  # noqa: E402
from whisperapp.transcriber import Transcriber, clean  # noqa: E402

PHRASES = [
    "Привет, это тест локального распознавания голоса на макбуке.",
    "Отправь медиаплан по заказу Хайб на следующую неделю, маржа примерно двадцать процентов.",
]
WAV = "/tmp/whisper_probe.wav"


def synth(text):
    """Голосом Милены в 16 кГц моно — ровно тот формат, что даёт микрофон."""
    subprocess.run(
        ["say", "-v", "Milena", "-o", WAV, "--file-format=WAVE",
         f"--data-format=LEI16@{SAMPLE_RATE}", text],
        check=True,
    )
    with wave.open(WAV, "rb") as source:
        assert source.getframerate() == SAMPLE_RATE, source.getframerate()
        raw = source.readframes(source.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


cfg = load_config()
print(f"распознавание: {cfg['model']} | язык: {cfg['language']}")

transcriber = Transcriber(cfg)
started = time.monotonic()
if not transcriber.warmup():
    print("Не загрузилось:", transcriber.error)
    sys.exit(1)
print(f"загрузка: {time.monotonic() - started:.1f} с")

for phrase in PHRASES:
    audio = synth(phrase)
    started = time.monotonic()
    text = transcriber.transcribe(audio)
    print(f"\n  сказано ({audio.size / SAMPLE_RATE:.1f} с): {phrase}")
    print(f"  распознано ({time.monotonic() - started:.2f} с): {text}")

# на тишине распознавание не должно ничего выдумывать
started = time.monotonic()
silence = transcriber.transcribe(np.zeros(SAMPLE_RATE * 2, dtype=np.float32))
print(f"\n  тишина 2 с → {silence!r} ({time.monotonic() - started:.2f} с)")
print("  отсев выдумок:", repr(clean("Продолжение следует...", cfg)))

if silence:
    print("\n✗ на тишине появился текст")
    sys.exit(1)
print("\nПровалено: ничего")
