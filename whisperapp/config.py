"""Настройки Whisper.

Файл лежит в ~/Library/Application Support/Whisper/settings.json, но человеку
туда лезть не нужно: всё, что стоит менять, есть в меню программы.
"""

import copy
import json
import logging
import logging.handlers
import pathlib

from .branding import APP_NAME

SUPPORT_DIR = pathlib.Path.home() / "Library" / "Application Support" / APP_NAME
CONFIG_PATH = SUPPORT_DIR / "settings.json"
LOG_DIR = pathlib.Path.home() / "Library" / "Logs" / APP_NAME
LOG_PATH = LOG_DIR / "whisper.log"
FIRST_RUN_MARKER = SUPPORT_DIR / ".welcomed"

# Готовые варианты горячей клавиши для меню. Первый — по умолчанию.
# Двойное нажатие ⌥ намеренно не предлагаем: его занимает Claude.
HOTKEY_PRESETS = [
    ("Двойное нажатие ⌘ справа", {"mode": "double_tap", "key": "cmd_r"}),
    ("Двойное нажатие ⇧ справа", {"mode": "double_tap", "key": "shift_r"}),
    ("Двойное нажатие ⌃ слева", {"mode": "double_tap", "key": "ctrl_l"}),
    ("⌃⌥D", {"mode": "combo", "combo": "<ctrl>+<alt>+d"}),
    ("⌃⇧Пробел", {"mode": "combo", "combo": "<ctrl>+<shift>+<space>"}),
]

LANGUAGES = [("Русский", "ru"), ("English", "en"), ("Определять самому", None)]

DEFAULTS = {
    "hotkey": {
        "mode": "double_tap",
        "key": "cmd_r",
        "combo": "<ctrl>+<alt>+d",
        "double_tap_window": 0.45,
        "cancel_on_escape": True,
    },
    # q4: по скорости равна полной, но в 3.5 раза меньше в памяти — на машинах
    # с 8 ГБ это главное: полную модель macOS выгружает в своп, и каждая
    # диктовка после паузы начинается с многосекундной подгрузки весов с диска
    "model": "mlx-community/whisper-large-v3-turbo-q4",
    "language": "ru",
    # Подсказка распознаванию: имена и термины, которые оно обычно путает.
    "initial_prompt": "",
    "max_seconds": 300,
    "min_seconds": 0.4,
    "sounds": True,
    "insert": {
        # "paste" — вставлять сразу, "clipboard_only" — только копировать
        "mode": "paste",
        "restore_clipboard": True,
        "restore_delay": 0.5,
    },
    "cleanup": {
        "collapse_whitespace": True,
        "strip_trailing_period": False,
    },
    "input_device": None,
}


def _merge(base, override):
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config():
    SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        save_config(DEFAULTS)
        return copy.deepcopy(DEFAULTS)
    try:
        user = json.loads(CONFIG_PATH.read_text("utf-8"))
    except Exception:
        logging.exception("Настройки повреждены, беру значения по умолчанию")
        return copy.deepcopy(DEFAULTS)

    # миграция: старый вариант по умолчанию тихо меняем на новый;
    # если модель выбирали руками — не трогаем
    if user.get("model") == "mlx-community/whisper-large-v3-turbo":
        user["model"] = DEFAULTS["model"]
        cfg = _merge(DEFAULTS, user)
        save_config(cfg)
        return cfg
    return _merge(DEFAULTS, user)


def save_config(cfg):
    SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", "utf-8")
    tmp.replace(CONFIG_PATH)


def matching_preset(cfg):
    """Какой пресет сейчас выбран — чтобы поставить галочку в меню."""
    hotkey = cfg["hotkey"]
    for label, preset in HOTKEY_PRESETS:
        if preset["mode"] != hotkey["mode"]:
            continue
        field = "key" if preset["mode"] == "double_tap" else "combo"
        if preset[field] == hotkey.get(field):
            return label
    return None


def setup_logging(level=logging.INFO):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=512_000, backupCount=2, encoding="utf-8"
    )
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)-12s %(message)s")
    handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [handler]
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    # библиотеки шумят на каждой расшифровке — оставляем только их предупреждения
    for noisy in ("httpx", "httpcore", "urllib3", "filelock", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return LOG_PATH
