"""WhisperType — локальная голосовая диктовка для macOS на чипах Apple."""

import os

# Ставим до импорта huggingface_hub/mlx_whisper, иначе они засоряют журнал
# прогресс-барами и предупреждением про отсутствие токена.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

__version__ = "1.0"
