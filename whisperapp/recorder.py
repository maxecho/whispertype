"""Запись с микрофона в память: 16 кГц моно float32 — ровно то, что ест Whisper."""

import logging
import threading

import numpy as np
import sounddevice as sd

log = logging.getLogger("recorder")

SAMPLE_RATE = 16_000

# Живой микрофон почти никогда не выдаёт пики около единицы: обычная речь это
# 0.05–0.3 в зависимости от усиления. Поэтому громкость для индикатора считаем
# относительно недавнего максимума — тогда контур реагирует на голос одинаково
# и на встроенном микрофоне, и на гарнитуре.
LEVEL_FLOOR = 0.035   # тише этого считаем тишиной и не раздуваем шум
LEVEL_FORGET = 0.994  # за сколько забываем прежний максимум (на блок в 64 мс)


def normalized_level(peak, reference, floor=LEVEL_FLOOR):
    """Громкость 0..1 относительно недавнего максимума."""
    return min(1.0, max(0.0, peak) / max(reference, floor))


class Recorder:
    def __init__(self, device=None):
        self.device = device
        self._frames = []
        self._stream = None
        self._lock = threading.Lock()
        self.level = 0.0  # громкость 0..1 для индикатора, уже нормированная
        self.peak = 0.0  # сырой пик последнего блока
        self.overflows = 0  # сколько раз система не успела отдать звук
        self._reference = LEVEL_FLOOR  # недавний максимум

    @property
    def active(self):
        return self._stream is not None

    def _callback(self, indata, frames, time_info, status):
        if status:
            log.debug("Статус аудиопотока: %s", status)
            if status.input_overflow:
                self.overflows += 1
        block = indata.copy()
        peak = float(np.abs(block).max()) if block.size else 0.0
        with self._lock:
            self._frames.append(block)
            self.peak = peak
            # максимум забываем медленно: громкое слово поднимает планку,
            # а тихая пауза не сбивает её мгновенно
            self._reference = max(peak, self._reference * LEVEL_FORGET)
            self.level = normalized_level(peak, self._reference)

    def snapshot(self):
        """Всё записанное на данный момент, не останавливая запись."""
        with self._lock:
            frames = list(self._frames)
        if not frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(frames, axis=0).reshape(-1).astype(np.float32)

    def start(self):
        if self._stream is not None:
            return
        with self._lock:
            self._frames = []
            self.level = 0.0
            self.peak = 0.0
            self._reference = LEVEL_FLOOR
            self.overflows = 0
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=1024,
            device=self.device,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self):
        """Останавливает поток и возвращает моно-сигнал как 1-D np.float32."""
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()
        with self._lock:
            frames, self._frames = self._frames, []
            self.level = 0.0
            self.peak = 0.0
        if not frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(frames, axis=0).reshape(-1).astype(np.float32)

    def discard(self):
        self.stop()


def probe_microphone():
    """Открывает поток на мгновение — так macOS покажет запрос доступа к микрофону."""
    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32")
    stream.start()
    stream.stop()
    stream.close()


def list_input_devices():
    out = []
    for idx, dev in enumerate(sd.query_devices()):
        if dev.get("max_input_channels", 0) > 0:
            out.append((idx, dev["name"]))
    return out
