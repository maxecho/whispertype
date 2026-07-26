"""Вставка расшифровки в активное поле ввода.

Через буфер обмена + синтетический ⌘V: единственный способ, который надёжно
работает с кириллицей в любой раскладке и в любом приложении.
"""

import logging
import pathlib
import sys
import threading
import time

import Quartz
from AppKit import NSPasteboard, NSPasteboardTypeString

log = logging.getLogger("inserter")

_KEYCODE_V = 9
_CMD = Quartz.kCGEventFlagMaskCommand

SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"


# --- разрешения ---

def parent_app_path():
    """Что именно macOS показывает в списке «Универсальный доступ».

    Из собранного WhisperType.app это сам бандл. При запуске из исходников
    разрешение достаётся фреймворковому Python.app: venv-питон при старте
    перезапускается в него, а не остаётся .venv/bin/python.
    """
    try:
        from Foundation import NSBundle

        bundle = NSBundle.mainBundle().bundlePath()
        if bundle and str(bundle).endswith(".app"):
            return str(bundle)
    except Exception:  # noqa: BLE001
        log.debug("NSBundle недоступен", exc_info=True)

    candidate = pathlib.Path(sys.base_prefix) / "Resources" / "Python.app"
    return str(candidate if candidate.exists() else pathlib.Path(sys.executable))

def accessibility_ok():
    try:
        from ApplicationServices import AXIsProcessTrusted

        return bool(AXIsProcessTrusted())
    except Exception:  # noqa: BLE001
        log.debug("AXIsProcessTrusted недоступен", exc_info=True)
        return True  # не блокируем работу, если проверить не смогли


def request_accessibility():
    """Показывает системный запрос «Универсальный доступ»."""
    try:
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )

        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}))
    except Exception:  # noqa: BLE001
        log.exception("Не смог запросить Универсальный доступ")
        return False


# --- буфер обмена ---

def get_clipboard():
    return NSPasteboard.generalPasteboard().stringForType_(NSPasteboardTypeString)


def set_clipboard(text):
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(text, NSPasteboardTypeString)


# --- синтетические нажатия ---

def _post(keycode, down, flags):
    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    event = Quartz.CGEventCreateKeyboardEvent(source, keycode, down)
    Quartz.CGEventSetFlags(event, flags)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def press_cmd_v():
    _post(_KEYCODE_V, True, _CMD)
    time.sleep(0.02)
    _post(_KEYCODE_V, False, _CMD)


def insert_text(text, cfg):
    """Кладёт текст в буфер и, если разрешено, вставляет в активное поле.

    Возвращает True, если сделали ⌘V; False — если текст только в буфере.
    """
    insert_cfg = cfg["insert"]
    previous = get_clipboard() if insert_cfg.get("restore_clipboard", True) else None
    set_clipboard(text)

    if insert_cfg.get("mode") != "paste":
        return False

    if not accessibility_ok():
        log.warning("Нет Универсального доступа — текст оставил в буфере обмена")
        return False

    time.sleep(0.06)  # даём буферу осесть до нажатия
    press_cmd_v()

    if previous is not None:
        delay = float(insert_cfg.get("restore_delay", 0.5))

        def restore():
            time.sleep(delay)
            # не затираем, если пользователь успел скопировать что-то своё
            if get_clipboard() == text:
                set_clipboard(previous)

        threading.Thread(target=restore, daemon=True).start()
    return True


# --- мониторинг ввода -----------------------------------------------------
# Слушать клавиатуру и нажимать за пользователя — это ДВА разных разрешения.
# Универсальный доступ (выше) даёт право нажимать; чтобы получать чужие
# нажатия, с macOS 10.15 нужен ещё «Мониторинг ввода». Проверяется он не через
# AX, а через IOKit, поэтому AXIsProcessTrusted может отвечать «да», пока
# перехват молчит.

SETTINGS_URL_INPUT = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
)

_LISTEN_EVENT = 1  # kIOHIDRequestTypeListenEvent
GRANTED, DENIED, UNKNOWN = 0, 1, 2  # значения IOHIDCheckAccess


def _iokit():
    import ctypes
    import ctypes.util

    lib = ctypes.CDLL(ctypes.util.find_library("IOKit"))
    lib.IOHIDCheckAccess.restype = ctypes.c_int
    lib.IOHIDCheckAccess.argtypes = [ctypes.c_uint32]
    lib.IOHIDRequestAccess.restype = ctypes.c_bool
    lib.IOHIDRequestAccess.argtypes = [ctypes.c_uint32]
    return lib


def input_monitoring_state():
    """GRANTED / DENIED / UNKNOWN — разрешено ли слушать клавиатуру."""
    try:
        return int(_iokit().IOHIDCheckAccess(_LISTEN_EVENT))
    except Exception:  # noqa: BLE001
        log.debug("IOHIDCheckAccess недоступен", exc_info=True)
        return UNKNOWN


def input_monitoring_ok():
    return input_monitoring_state() == GRANTED


def request_input_monitoring():
    """Показывает системный запрос «Мониторинг ввода» (только если ещё не спрашивали)."""
    try:
        return bool(_iokit().IOHIDRequestAccess(_LISTEN_EVENT))
    except Exception:  # noqa: BLE001
        log.exception("Не смог запросить мониторинг ввода")
        return False
