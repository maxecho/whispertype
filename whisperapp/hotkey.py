"""Перехват горячей клавиши на уровне системы.

Слушаем CGEventTap напрямую через Quartz и работаем только с сырыми кодами
клавиш. Раскладку не запрашиваем сознательно: HIToolbox с macOS 26 требует
главный поток и роняет процесс, если спросить его из фонового (именно на этом
падала pynput). Коды клавиш от раскладки не зависят, так что нам это и не нужно.

Событий мы не съедаем (listen-only), поэтому ⌘ и остальные клавиши продолжают
работать как обычно.
"""

import logging
import threading

import Quartz

log = logging.getLogger("hotkey")

# Виртуальные коды клавиш macOS.
KEYCODES = {
    "cmd_l": 55, "cmd_r": 54,
    "shift_l": 56, "shift_r": 60,
    "alt_l": 58, "alt_r": 61,
    "ctrl_l": 59, "ctrl_r": 62,
    "esc": 53, "space": 49,
    "f13": 105, "f14": 107, "f15": 113, "f16": 106,
    "f17": 64, "f18": 79, "f19": 80, "f20": 90,
    "a": 0, "b": 11, "c": 8, "d": 2, "e": 14, "f": 3, "g": 5, "h": 4,
    "i": 34, "j": 38, "k": 40, "l": 37, "m": 46, "n": 45, "o": 31, "p": 35,
    "q": 12, "r": 15, "s": 1, "t": 17, "u": 32, "v": 9, "w": 13, "x": 7,
    "y": 16, "z": 6,
}
BY_KEYCODE = {code: name for name, code in KEYCODES.items()}

# Маски, различающие левый и правый модификатор (IOKit, NX_DEVICE*KEYMASK).
# Обычные CGEventFlags этого не умеют: правый ⌘ и левый ⌘ дают один и тот же бит.
DEVICE_MASKS = {
    "ctrl_l": 0x00000001, "ctrl_r": 0x00002000,
    "shift_l": 0x00000002, "shift_r": 0x00000004,
    "cmd_l": 0x00000008, "cmd_r": 0x00000010,
    "alt_l": 0x00000020, "alt_r": 0x00000040,
}

MODIFIER_FLAGS = {
    "<ctrl>": Quartz.kCGEventFlagMaskControl,
    "<alt>": Quartz.kCGEventFlagMaskAlternate,
    "<shift>": Quartz.kCGEventFlagMaskShift,
    "<cmd>": Quartz.kCGEventFlagMaskCommand,
}
ALL_MODIFIER_FLAGS = 0
for _flag in MODIFIER_FLAGS.values():
    ALL_MODIFIER_FLAGS |= _flag

HUMAN_NAMES = {
    "alt_r": "⌥ справа", "alt_l": "⌥ слева",
    "cmd_r": "⌘ справа", "cmd_l": "⌘ слева",
    "ctrl_r": "⌃ справа", "ctrl_l": "⌃ слева",
    "shift_r": "⇧ справа", "shift_l": "⇧ слева",
}

_COMBO_GLYPHS = {"<ctrl>": "⌃", "<alt>": "⌥", "<shift>": "⇧", "<cmd>": "⌘", "<space>": "Пробел"}

DEFAULT_KEY = "cmd_r"


def describe(cfg):
    """Название клавиши так, как его прочитает человек."""
    hotkey = cfg["hotkey"]
    if hotkey["mode"] == "double_tap":
        return f"двойное нажатие {HUMAN_NAMES.get(hotkey['key'], hotkey['key'])}"
    return "".join(
        _COMBO_GLYPHS.get(part, part.upper()) for part in hotkey["combo"].split("+")
    )


def parse_combo(combo):
    """'<ctrl>+<alt>+d' -> (маска модификаторов, код клавиши). None, если не разобрали."""
    flags, keycode = 0, None
    for part in combo.split("+"):
        if part in MODIFIER_FLAGS:
            flags |= MODIFIER_FLAGS[part]
            continue
        name = part.strip("<>")
        if name not in KEYCODES:
            return None
        if keycode is not None:
            return None  # две обычные клавиши в комбинации не поддерживаем
        keycode = KEYCODES[name]
    if keycode is None or not flags:
        return None
    return flags, keycode


class HotkeyListener:
    """Дёргает on_trigger при срабатывании клавиши, on_cancel — при Esc."""

    def __init__(self, cfg, on_trigger, on_cancel=None, clock=None):
        import time as _time

        self.cfg = cfg
        self.on_trigger = on_trigger
        self.on_cancel = on_cancel or (lambda: None)
        self._clock = clock or _time.monotonic

        hotkey = cfg["hotkey"]
        self.mode = hotkey["mode"]
        self.window = float(hotkey["double_tap_window"])
        self.cancel_on_escape = bool(hotkey.get("cancel_on_escape", True))

        self.target = hotkey.get("key")
        if self.mode == "double_tap" and self.target not in DEVICE_MASKS:
            log.warning("Не знаю клавишу %r, беру ⌘ справа", self.target)
            self.target = DEFAULT_KEY

        self.combo = None
        if self.mode == "combo":
            self.combo = parse_combo(hotkey["combo"])
            if self.combo is None:
                log.error("Не разобрал комбинацию %r, беру двойное нажатие ⌘", hotkey["combo"])
                self.mode = "double_tap"
                self.target = DEFAULT_KEY

        # состояние детектора двойного нажатия
        self._held = False
        self._press_at = 0.0
        self._last_tap_at = 0.0
        self._series_broken = False

        self._tap = None
        self._runloop = None
        self._thread = None
        self.events_seen = 0  # для диагностики: доходят ли события вообще

    # ------------------------------------------------------------ запуск

    def start(self):
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, args=(ready,), daemon=True)
        self._thread.start()
        ready.wait(timeout=3.0)
        if self._tap is None:
            log.warning("Перехват клавиш не включился — скорее всего нет разрешения")
        else:
            log.info("Слушаю: %s", describe(self.cfg))

    def _run_loop(self, ready):
        try:
            mask = Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged) | Quartz.CGEventMaskBit(
                Quartz.kCGEventKeyDown
            )
            tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap,
                Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionListenOnly,  # события не съедаем
                mask,
                self._on_event,
                None,
            )
            if tap is None:
                ready.set()
                return
            source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
            self._runloop = Quartz.CFRunLoopGetCurrent()
            Quartz.CFRunLoopAddSource(self._runloop, source, Quartz.kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(tap, True)
            self._tap = tap
        finally:
            ready.set()
        if self._tap is not None:
            Quartz.CFRunLoopRun()

    def stop(self):
        if self._tap is not None:
            Quartz.CGEventTapEnable(self._tap, False)
            self._tap = None
        if self._runloop is not None:
            Quartz.CFRunLoopStop(self._runloop)
            self._runloop = None

    @property
    def alive(self):
        return self._tap is not None

    # ------------------------------------------------------------ события

    def _on_event(self, proxy, event_type, event, refcon):
        self.events_seen += 1
        try:
            if event_type in (
                Quartz.kCGEventTapDisabledByTimeout,
                Quartz.kCGEventTapDisabledByUserInput,
            ):
                # система иногда отключает перехват сама — включаем обратно
                if self._tap is not None:
                    Quartz.CGEventTapEnable(self._tap, True)
                return event

            keycode = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode
            )
            flags = Quartz.CGEventGetFlags(event)
            name = BY_KEYCODE.get(int(keycode))

            if event_type == Quartz.kCGEventFlagsChanged:
                if name in DEVICE_MASKS:
                    if flags & DEVICE_MASKS[name]:
                        self._key_down(name)
                    else:
                        self._key_up(name)
            elif event_type == Quartz.kCGEventKeyDown:
                self._key_down(name, flags, int(keycode))
        except Exception:  # noqa: BLE001
            log.exception("Сбой при разборе нажатия")
        return event

    # ----------------------------------------------- разбор нажатий (тестируемо)

    def _fire(self):
        try:
            self.on_trigger()
        except Exception:  # noqa: BLE001
            log.exception("Ошибка в обработчике")

    def _key_down(self, name, flags=0, keycode=None):
        if name == "esc" and self.cancel_on_escape:
            try:
                self.on_cancel()
            except Exception:  # noqa: BLE001
                log.exception("Ошибка в обработчике отмены")
            return

        if self.mode == "combo":
            if keycode is None:
                keycode = KEYCODES.get(name)
            wanted_flags, wanted_key = self.combo
            if keycode == wanted_key and (flags & ALL_MODIFIER_FLAGS) == wanted_flags:
                self._fire()
            return

        if name == self.target:
            if self._held:  # автоповтор удержания — игнорируем
                return
            self._held = True
            self._press_at = self._clock()
        else:
            # нажали что-то ещё: значит модификатор держали как модификатор
            self._series_broken = True
            self._last_tap_at = 0.0

    def _key_up(self, name):
        if self.mode == "combo" or name != self.target:
            return

        self._held = False
        now = self._clock()
        duration = now - self._press_at
        broken, self._series_broken = self._series_broken, False

        # долгое удержание нажатием не считаем
        if broken or duration > self.window:
            self._last_tap_at = 0.0
            return

        if self._last_tap_at and (now - self._last_tap_at) <= self.window:
            self._last_tap_at = 0.0  # третье нажатие не должно сработать повторно
            self._fire()
        else:
            self._last_tap_at = now
