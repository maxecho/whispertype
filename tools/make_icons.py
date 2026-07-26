"""Генератор иконок WhisperType.

Рисует всё из кода, чтобы айдентику можно было переделать одной командой:

    .venv/bin/python tools/make_icons.py

Создаёт:
  whisperapp/assets/AppIcon.icns   — иконка приложения (Dock, Finder, настройки)
  whisperapp/assets/menubar.png    — значок в строке меню (шаблонный, ч/б)
  whisperapp/assets/menubar-rec.png— он же во время записи (красный)
  whisperapp/assets/icon-512.png   — для окон «О программе» и README
"""

import pathlib
import subprocess

import numpy as np
from PIL import Image, ImageDraw

ROOT = pathlib.Path(__file__).resolve().parent.parent
ASSETS = ROOT / "whisperapp" / "assets"

# Айдентика: спокойный индиго-фиолетовый градиент, белая звуковая волна.
GRADIENT_TOP = (99, 102, 241)     # индиго
GRADIENT_BOTTOM = (139, 92, 246)  # фиолетовый
RECORDING_RED = (255, 69, 58)     # системный красный macOS

# Профиль волны: симметричный, читается как голос.
BARS = (0.34, 0.66, 1.00, 0.66, 0.34)
# На мелких размерах пять столбиков сливаются в пятно — упрощаем до трёх.
BARS_SMALL = (0.55, 1.00, 0.55)

SS = 4  # суперсэмплинг для гладких краёв


def superellipse_mask(size, exponent=5.0):
    """Скруглённый квадрат в стиле macOS (суперэллипс, а не просто радиус)."""
    axis = np.linspace(-1.0, 1.0, size)
    x, y = np.meshgrid(axis, axis)
    inside = np.abs(x) ** exponent + np.abs(y) ** exponent <= 1.0
    return Image.fromarray((inside * 255).astype(np.uint8), mode="L")


def vertical_gradient(size, top, bottom):
    ramp = np.linspace(0.0, 1.0, size)[:, None]
    rgb = np.stack(
        [np.full((size, size), top[i]) * (1 - ramp) + np.full((size, size), bottom[i]) * ramp
         for i in range(3)],
        axis=-1,
    )
    return Image.fromarray(rgb.astype(np.uint8), mode="RGB")


def draw_wave(draw, box, color, bars=BARS, gap_ratio=0.62):
    """Волна из скруглённых столбиков внутри прямоугольника box."""
    left, top, right, bottom = box
    width, height = right - left, bottom - top
    # ширина столбика подбирается так, чтобы вся группа заняла box по горизонтали
    unit = width / (len(bars) + (len(bars) - 1) * gap_ratio)
    gap = unit * gap_ratio
    centre_y = top + height / 2
    for index, ratio in enumerate(bars):
        x0 = left + index * (unit + gap)
        bar_height = max(height * ratio, unit)  # не тоньше, чем скругление
        draw.rounded_rectangle(
            [x0, centre_y - bar_height / 2, x0 + unit, centre_y + bar_height / 2],
            radius=unit / 2,
            fill=color,
        )


def app_icon(size=1024, bars=BARS):
    """Иконка приложения: градиентный суперэллипс с белой волной."""
    big = size * SS
    shape = int(big * 0.805)  # поля по канону macOS

    mask = superellipse_mask(shape)
    tile = vertical_gradient(shape, GRADIENT_TOP, GRADIENT_BOTTOM).convert("RGBA")
    tile.putalpha(mask)

    # Блик сверху: плавное затухание без единой чёткой границы, иначе на
    # большом размере через иконку идёт заметная дуга.
    falloff = np.clip(1.0 - np.linspace(0.0, 1.0, shape) / 0.62, 0.0, 1.0) ** 2
    gloss_alpha = (falloff[:, None] * np.ones((1, shape)) * 46) * (np.array(mask) / 255)
    gloss = Image.new("RGBA", (shape, shape), (255, 255, 255, 0))
    gloss.putalpha(Image.fromarray(gloss_alpha.astype(np.uint8), mode="L"))
    tile = Image.alpha_composite(tile, gloss)

    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    canvas.paste(tile, ((big - shape) // 2, (big - shape) // 2), tile)

    draw = ImageDraw.Draw(canvas)
    wave_w, wave_h = shape * 0.52, shape * 0.42
    if bars is BARS_SMALL:
        wave_w = shape * 0.38  # три столбика не должны разъезжаться на всю ширину
    cx = cy = big / 2
    draw_wave(
        draw,
        (cx - wave_w / 2, cy - wave_h / 2, cx + wave_w / 2, cy + wave_h / 2),
        (255, 255, 255, 255),
        bars=bars,
    )
    return canvas.resize((size, size), Image.LANCZOS)


def menubar_icon(color, size=44):
    """Значок для строки меню. rumps рисует его в квадрате 20×20 пунктов."""
    big = size * SS
    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    wave_w, wave_h = big * 0.86, big * 0.62
    draw_wave(
        draw,
        ((big - wave_w) / 2, (big - wave_h) / 2, (big + wave_w) / 2, (big + wave_h) / 2),
        color,
    )
    return canvas.resize((size, size), Image.LANCZOS)


def hero_banner(width=1280, height=380):
    """Баннер для README: иконка, имя и слоган на тёмном градиенте."""
    from PIL import ImageFont

    ss = 2
    W, H = width * ss, height * ss
    ramp = np.linspace(0.0, 1.0, W)[None, :]
    top = np.array((30, 27, 63))       # глубокий индиго
    bottom = np.array((76, 46, 131))   # фиолетовый
    rgb = np.stack(
        [top[i] * (1 - ramp) + bottom[i] * ramp for i in range(3)], axis=-1
    ) * np.ones((H, 1, 1))
    banner = Image.fromarray(rgb.astype(np.uint8), mode="RGB").convert("RGBA")

    icon = app_icon(size=H - 96 * ss)
    banner.paste(icon, (110 * ss, (H - icon.size[1]) // 2), icon)

    def font(size, bold=True):
        for path, index in (
            ("/System/Library/Fonts/HelveticaNeue.ttc", 1 if bold else 0),
            ("/System/Library/Fonts/Helvetica.ttc", 1 if bold else 0),
        ):
            try:
                return ImageFont.truetype(path, size, index=index)
            except Exception:  # noqa: BLE001
                continue
        return ImageFont.load_default(size)

    draw = ImageDraw.Draw(banner)
    text_x = 110 * ss + icon.size[0] + 90 * ss
    draw.text((text_x, H * 0.30), "WhisperType", font=font(86 * ss),
              fill=(255, 255, 255, 255), anchor="lm")
    draw.text((text_x, H * 0.30 + 92 * ss), "Dictation that never leaves your Mac",
              font=font(34 * ss, bold=False), fill=(255, 255, 255, 200), anchor="lm")
    return banner.resize((width, height), Image.LANCZOS).convert("RGB")


def build_icns(master, small, out):
    """Собирает .icns из набора размеров через системный iconutil.

    До 32 px включительно берём упрощённую волну из трёх столбиков.
    """
    iconset = ASSETS / "AppIcon.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    for base in (16, 32, 128, 256, 512):
        for scale, pixels in ((1, base), (2, base * 2)):
            source = small if pixels <= 32 else master
            suffix = "" if scale == 1 else "@2x"
            source.resize((pixels, pixels), Image.LANCZOS).save(
                iconset / f"icon_{base}x{base}{suffix}.png"
            )
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)
    for leftover in iconset.iterdir():
        leftover.unlink()
    iconset.rmdir()


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)

    master = app_icon()
    master.save(ASSETS / "icon-512.png")
    build_icns(master, app_icon(bars=BARS_SMALL), ASSETS / "AppIcon.icns")

    # чёрный + альфа: macOS сам перекрасит под светлую и тёмную строку меню
    menubar_icon((0, 0, 0, 255)).save(ASSETS / "menubar.png")
    menubar_icon(RECORDING_RED + (255,)).save(ASSETS / "menubar-rec.png")

    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    hero_banner().save(docs / "hero.png")

    for item in sorted(ASSETS.iterdir()):
        print(f"  {item.name:20} {item.stat().st_size / 1024:6.1f} КБ")


if __name__ == "__main__":
    main()
