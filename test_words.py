"""Тесты словаря замен: разбор, применение, границы слов и регистр."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from whisperapp.config import DEFAULTS, _merge  # noqa: E402
from whisperapp.transcriber import (  # noqa: E402
    apply_replacements,
    clean,
    format_replacements,
    parse_replacements,
)

fails = []


def check(name, got, want):
    if got != want:
        fails.append(name)
    print(f"  {'✓' if got == want else '✗'} {name}\n      получено: {got!r}\n      ожидалось: {want!r}")


WORDS = {"хайб": "Hybe", "би ди эм": "BDM", "медиа план": "медиаплан", "жоржи": "Георгий"}

# --- применение ------------------------------------------------------------

check("простая замена", apply_replacements("заказ от хайб", WORDS), "заказ от Hybe")
check("замена из нескольких слов", apply_replacements("отчёт би ди эм", WORDS), "отчёт BDM")
check(
    "длинная замена побеждает короткую",
    apply_replacements("сделай медиа план", {"план": "ПЛАН", "медиа план": "медиаплан"}),
    "сделай медиаплан",
)
check("регистр не мешает найти", apply_replacements("Хайб растёт", WORDS), "Hybe растёт")
check(
    "заглавная в начале предложения сохраняется",
    apply_replacements("жоржи звонил", WORDS),
    "Георгий звонил",
)
check(
    "внутри слова не срабатывает",
    apply_replacements("хайбер и прихайб", WORDS),
    "хайбер и прихайб",
)
check(
    "знаки препинания не мешают",
    apply_replacements("это хайб, точно хайб.", WORDS),
    "это Hybe, точно Hybe.",
)
check("пустой словарь ничего не делает", apply_replacements("текст", {}), "текст")
check("без словаря не падает", apply_replacements("текст", None), "текст")
check(
    "замена не запускает цепочку",
    apply_replacements("а", {"а": "б", "б": "в"}),
    "б",
)

# --- разбор и обратно ------------------------------------------------------

raw = """
# комментарий
хайб = Hybe
би ди эм=BDM

мусор без равно
  пробелы  =  Обрезаются
"""
parsed = parse_replacements(raw)
check(
    "разбор строк",
    parsed,
    {"хайб": "Hybe", "би ди эм": "BDM", "пробелы": "Обрезаются"},
)
check("пустой ввод", parse_replacements(""), {})
check("None не ломает", parse_replacements(None), {})
check(
    "обратно в текст и снова в словарь",
    parse_replacements(format_replacements(WORDS)),
    WORDS,
)

# --- в связке с общей чисткой ---------------------------------------------

cfg = _merge(DEFAULTS, {"replacements": WORDS})
check(
    "clean применяет словарь",
    clean("  заказ   от хайб  ", cfg),
    "заказ от Hybe",
)
check(
    "галлюцинация отбрасывается и после замен",
    clean("Продолжение следует...", cfg),
    "",
)

print("\nПровалено:", fails or "ничего")
sys.exit(1 if fails else 0)
