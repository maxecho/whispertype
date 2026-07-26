"""Тесты текстов интерфейса.

Главное правило: в окнах программы не должно быть путей вида /Users/имя — только
~/… Иначе интерфейс показывает имя пользователя и выглядит как отладочный вывод.
"""

import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from whisperapp import branding as b  # noqa: E402
from whisperapp.config import LOG_PATH  # noqa: E402

HOME = str(pathlib.Path.home())
fails = []


def check(name, condition, detail=""):
    if not condition:
        fails.append(name)
    print(f"  {'✓' if condition else '✗'} {name}{': ' + detail if detail else ''}")


# --- сокращение путей ------------------------------------------------------

check(
    "домашний путь сокращается",
    b.pretty_path(f"{HOME}/Library/Logs/WhisperType/x.log")
    == "~/Library/Logs/WhisperType/x.log",
    b.pretty_path(f"{HOME}/Library/Logs/WhisperType/x.log"),
)
check("сам дом превращается в ~", b.pretty_path(HOME) == "~")
check(
    "чужие пути не трогаем",
    b.pretty_path("/Applications/WhisperType.app") == "/Applications/WhisperType.app",
)
check(
    "похожий, но другой путь не режется",
    b.pretty_path(HOME + "-backup/x") == HOME + "-backup/x",
)
check("объект Path тоже принимается", b.pretty_path(pathlib.Path(HOME) / "a") == "~/a")

# --- ни в одном тексте интерфейса нет /Users/... ---------------------------

texts = {
    "welcome": b.welcome_text("двойное нажатие ⌘ справа"),
    "help": b.help_text("двойное нажатие ⌘ справа"),
    "about": b.about_text(),
    "no_access": b.no_access_text(f"{HOME}/Applications/WhisperType.app"),
}
for access in (True, False):
    for mic in (True, False):
        for model in (True, False):
            key = f"trouble(access={access},mic={mic},model={model})"
            texts[key] = b.trouble_text(
                "двойное нажатие ⌘ справа", access, mic, model, LOG_PATH,
                log_button=access,
            )

leaky = [name for name, text in texts.items() if re.search(r"/Users/|" + re.escape(HOME), text)]
check("ни в одном окне нет личного пути", not leaky, ", ".join(leaky) or "проверено окон: %d" % len(texts))

# --- текст не обещает кнопку, которой нет ---------------------------------

with_button = b.trouble_text("x", True, True, True, LOG_PATH, log_button=True)
without_button = b.trouble_text("x", False, True, True, LOG_PATH, log_button=False)
check("с кнопкой — упоминаем её", "Показать журнал" in with_button)
check("без кнопки — не упоминаем", "Показать журнал" not in without_button)
check("путь к журналу есть в обоих", all("~/Library/Logs" in t for t in (with_button, without_button)))

# --- ни одно окно не пустое и все на русском ------------------------------

check("все окна непустые", all(len(t.strip()) > 40 for t in texts.values()))
check(
    "везде новое имя программы",
    all("WhisperType" in t or b.APP_NAME in t for t in (texts["welcome"], texts["about"], texts["no_access"])),
)

print("\nПровалено:", fails or "ничего")
sys.exit(1 if fails else 0)
