"""Тесты подсветки: сглаживание громкости и обрезка живого текста.

Окна не создаём — проверяем чистую логику, которая и определяет, как контур
выглядит на глаз.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from whisperapp.recorder import LEVEL_FLOOR, normalized_level  # noqa: E402
from whisperapp.overlay import (  # noqa: E402
    CAPTION_MAX_CHARS,
    GLOW_WIDTH_CALM,
    GLOW_WIDTH_LOUD,
    smooth_level,
)

fails = []


def check(name, condition, detail=""):
    if not condition:
        fails.append(name)
    print(f"  {'✓' if condition else '✗'} {name}{': ' + detail if detail else ''}")


def run(start, targets):
    value, trace = start, []
    for target in targets:
        value = smooth_level(value, target)
        trace.append(round(value, 3))
    return trace


# --- сглаживание -----------------------------------------------------------

attack = run(0.0, [1.0] * 10)
check("подъём быстрый", attack[5] > 0.8, f"за 6 шагов {attack[5]}")
check("подъём не мгновенный", attack[0] < 0.5, f"первый шаг {attack[0]}")
check("подъём монотонный", all(b >= a for a, b in zip(attack, attack[1:])))

release = run(1.0, [0.0] * 20)
check("спад заметно медленнее подъёма", release[5] > 0.5, f"за 6 шагов {release[5]}")
check("спад доходит до нуля", release[-1] < 0.25, f"за 20 шагов {release[-1]}")
check("спад монотонный", all(b <= a for a, b in zip(release, release[1:])))

# короткая пауза между слогами не должна гасить контур
speech = run(0.9, [0.0, 0.0, 0.9, 0.0, 0.0, 0.9])
check("пауза в речи не гасит контур", min(speech) > 0.55, f"минимум {min(speech)}")

check("значение не выходит за единицу", max(run(0.0, [5.0] * 30)) <= 1.0)
check("значение не уходит ниже нуля", min(run(1.0, [-5.0] * 60)) >= 0.0)
check("цель достигается", abs(run(0.0, [0.5] * 200)[-1] - 0.5) < 0.001)

# --- размеры ---------------------------------------------------------------

check("громкая рамка заметно толще тихой", GLOW_WIDTH_LOUD > GLOW_WIDTH_CALM * 2)
check("живой текст ограничен", CAPTION_MAX_CHARS <= 200)

# --- нормировка громкости --------------------------------------------------
# Живой микрофон не выдаёт пиков около единицы, поэтому индикатор считает
# громкость относительно недавнего максимума.

check("речь на своём максимуме даёт полный контур", normalized_level(0.25, 0.25) == 1.0)
check(
    "тихий микрофон тоже раскачивает контур",
    normalized_level(0.06, 0.07) > 0.7,
    f"{normalized_level(0.06, 0.07):.2f}",
)
check(
    "громкий микрофон не зашкаливает",
    normalized_level(0.9, 0.9) == 1.0,
)
check(
    "пауза гасит контур",
    normalized_level(0.01, 0.3) < 0.1,
    f"{normalized_level(0.01, 0.3):.2f}",
)
check(
    "тишина не раздувается до максимума",
    normalized_level(0.002, LEVEL_FLOOR) < 0.15,
    f"{normalized_level(0.002, LEVEL_FLOOR):.2f}",
)
check("порог не даёт делить на ноль", normalized_level(0.5, 0.0) == 1.0)
check("отрицательный пик не ломает", normalized_level(-1.0, 0.2) == 0.0)

print("\nПровалено:", fails or "ничего")
sys.exit(1 if fails else 0)
