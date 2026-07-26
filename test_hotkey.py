"""Тесты распознавания горячей клавиши.

Клавиатура и разрешения не нужны: подаём события напрямую и подменяем часы,
поэтому тесты мгновенные и не зависят от скорости машины.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from whisperapp.config import DEFAULTS, _merge  # noqa: E402
from whisperapp.hotkey import HotkeyListener, describe, parse_combo  # noqa: E402

TARGET = "cmd_r"
OTHER = "cmd_l"
CTRL, ALT, SHIFT = 0x40000, 0x80000, 0x20000

fails = []


class Clock:
    """Часы, которыми управляем вручную."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make(cfg=None):
    clock = Clock()
    fired, cancels = [], []
    listener = HotkeyListener(
        cfg or DEFAULTS,
        lambda: fired.append(1),
        lambda: cancels.append(1),
        clock=clock,
    )
    return listener, fired, cancels, clock


def tap(listener, clock, key, hold=0.05):
    listener._key_down(key)
    clock.advance(hold)
    listener._key_up(key)


def check(name, got, want):
    if got != want:
        fails.append(name)
    print(f"  {'✓' if got == want else '✗'} {name}: получено {got}, ожидалось {want}")


# --- двойное нажатие -------------------------------------------------------

listener, fired, _, clock = make()
tap(listener, clock, TARGET); clock.advance(0.1); tap(listener, clock, TARGET)
check("двойное нажатие срабатывает", len(fired), 1)

listener, fired, _, clock = make()
tap(listener, clock, TARGET)
check("одиночное нажатие молчит", len(fired), 0)

listener, fired, _, clock = make()
tap(listener, clock, TARGET); clock.advance(1.0); tap(listener, clock, TARGET)
check("медленные нажатия молчат", len(fired), 0)

listener, fired, _, clock = make()
for _ in range(3):
    tap(listener, clock, TARGET); clock.advance(0.1)
check("тройное нажатие = одно срабатывание", len(fired), 1)

listener, fired, _, clock = make()
for _ in range(2):
    tap(listener, clock, TARGET); clock.advance(0.1); tap(listener, clock, TARGET)
    clock.advance(1.0)
check("две серии = два срабатывания", len(fired), 2)

# ⌘ в роли модификатора: ⌘C дважды подряд не должно запускать диктовку
listener, fired, _, clock = make()
for _ in range(2):
    listener._key_down(TARGET)
    listener._key_down("c", flags=0, keycode=8)
    clock.advance(0.05)
    listener._key_up(TARGET)
    clock.advance(0.1)
check("⌘+буква не срабатывает", len(fired), 0)

listener, fired, _, clock = make()
tap(listener, clock, TARGET, hold=0.9); clock.advance(0.1)
tap(listener, clock, TARGET, hold=0.9)
check("удержание не считается нажатием", len(fired), 0)

listener, fired, _, clock = make()
tap(listener, clock, OTHER); clock.advance(0.1); tap(listener, clock, OTHER)
check("левый ⌘ игнорируется", len(fired), 0)

listener, fired, _, clock = make()
tap(listener, clock, TARGET); clock.advance(0.1)
listener._key_down(TARGET); listener._key_down(TARGET); clock.advance(0.05)
listener._key_up(TARGET)
check("автоповтор не мешает", len(fired), 1)

listener, _, cancels, clock = make()
listener._key_down("esc")
check("Esc отменяет запись", len(cancels), 1)

# --- комбинация ------------------------------------------------------------

combo_cfg = _merge(DEFAULTS, {"hotkey": {"mode": "combo", "combo": "<ctrl>+<alt>+d"}})

listener, fired, _, clock = make(combo_cfg)
check("комбинация распознана", listener.mode, "combo")
listener._key_down("d", flags=CTRL | ALT, keycode=2)
check("⌃⌥D срабатывает", len(fired), 1)

listener, fired, _, clock = make(combo_cfg)
listener._key_down("d", flags=CTRL, keycode=2)
check("⌃D без ⌥ не срабатывает", len(fired), 0)

listener, fired, _, clock = make(combo_cfg)
listener._key_down("d", flags=CTRL | ALT | SHIFT, keycode=2)
check("лишний ⇧ не срабатывает", len(fired), 0)

listener, fired, _, clock = make(combo_cfg)
listener._key_down("f", flags=CTRL | ALT, keycode=3)
check("другая буква не срабатывает", len(fired), 0)

# --- разбор и подписи ------------------------------------------------------

check("разбор ⌃⌥D", parse_combo("<ctrl>+<alt>+d"), (CTRL | ALT, 2))
check("разбор ⌃⇧Пробел", parse_combo("<ctrl>+<shift>+<space>"), (CTRL | SHIFT, 49))
check("мусор не разбирается", parse_combo("не+комбинация"), None)
check("подпись комбинации", describe(combo_cfg), "⌃⌥D")
check("подпись двойного нажатия", describe(DEFAULTS), "двойное нажатие ⌘ справа")

bad_cfg = _merge(DEFAULTS, {"hotkey": {"mode": "combo", "combo": "не+комбинация"}})
check("битая комбинация откатывается", HotkeyListener(bad_cfg, lambda: None).mode, "double_tap")

unknown_cfg = _merge(DEFAULTS, {"hotkey": {"key": "неведомая"}})
check(
    "неизвестная клавиша откатывается",
    HotkeyListener(unknown_cfg, lambda: None).target,
    "cmd_r",
)

# --- режим удержания -------------------------------------------------------

hold_cfg = _merge(DEFAULTS, {"hotkey": {"mode": "hold", "key": "cmd_r"}})


def make_hold():
    clock = Clock()
    started, stopped = [], []
    listener = HotkeyListener(hold_cfg, lambda: started.append(1), clock=clock)
    listener.on_release = lambda: stopped.append(1)
    return listener, started, stopped, clock


# короткое нажатие — это модификатор, не диктовка
listener, started, stopped, clock = make_hold()
listener._key_down("cmd_r"); clock.advance(0.1); listener.poll_hold()
listener._key_up("cmd_r")
check("короткое нажатие не начинает запись", (len(started), len(stopped)), (0, 0))

# удержание дольше порога — запись, отпускание — конец
listener, started, stopped, clock = make_hold()
listener._key_down("cmd_r"); clock.advance(0.5); listener.poll_hold()
check("удержание начинает запись", len(started), 1)
listener._key_up("cmd_r")
check("отпускание заканчивает запись", len(stopped), 1)

# ⌘C во время удержания — это шорткат, а не диктовка
listener, started, stopped, clock = make_hold()
listener._key_down("cmd_r")
listener._key_down("c", flags=0, keycode=8)
clock.advance(0.5); listener.poll_hold()
listener._key_up("cmd_r")
check("⌘+буква не начинает запись", (len(started), len(stopped)), (0, 0))

# опрос не должен запускать запись дважды
listener, started, stopped, clock = make_hold()
listener._key_down("cmd_r"); clock.advance(0.5)
for _ in range(5):
    listener.poll_hold()
check("повторный опрос не дублирует", len(started), 1)

# печать во время диктовки её не обрывает
listener, started, stopped, clock = make_hold()
listener._key_down("cmd_r"); clock.advance(0.5); listener.poll_hold()
listener._key_down("a", flags=0, keycode=0)
listener._key_up("cmd_r")
check("печать во время диктовки не мешает", (len(started), len(stopped)), (1, 1))

# другая клавиша удержания игнорируется
listener, started, stopped, clock = make_hold()
listener._key_down("cmd_l"); clock.advance(0.5); listener.poll_hold(); listener._key_up("cmd_l")
check("левый ⌘ не запускает удержание", len(started), 0)

check("подпись удержания", describe(hold_cfg), "удержание ⌘ справа")

print("\nПровалено:", fails or "ничего")
sys.exit(1 if fails else 0)
