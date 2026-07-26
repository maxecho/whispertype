"""Подсветка по краям экрана во время диктовки.

Прозрачная рамка поверх всего, дышащая в такт голосу: видно, что вас слышат,
не отводя глаз от текста. Окно не перехватывает мышь и живёт на всех рабочих
столах, включая полноэкранные приложения.

Рамка рисуется один раз в картинку и больше не перерисовывается — громкость
меняет только прозрачность окна. Иначе пришлось бы двадцать раз в секунду
перерисовывать площадь всего экрана.
"""

import logging

from AppKit import (
    NSAnimationContext,
    NSAttributedString,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSEvenOddWindingRule,
    NSFont,
    NSGradient,
    NSGraphicsContext,
    NSImage,
    NSImageScaleAxesIndependently,
    NSImageView,
    NSInsetRect,
    NSFontAttributeName,
    NSLineBreakByWordWrapping,
    NSMakeRect,
    NSMakeSize,
    NSScreen,
    NSScreenSaverWindowLevel,
    NSTextField,
    NSStringDrawingUsesLineFragmentOrigin,
    NSView,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialHUDWindow,
    NSVisualEffectStateActive,
    NSVisualEffectView,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
)

log = logging.getLogger("overlay")

# Цвета айдентики: индиго → фиолетовый, как на иконке.
GLOW_START = (0.39, 0.40, 0.95)
GLOW_END = (0.55, 0.36, 0.96)
GLOW_ANGLE = 35.0     # наклон градиента, чтобы рамка переливалась по диагонали

# Две рамки: тонкая горит всегда, широкая проступает на громких местах.
# Так контур «растёт» от голоса, а перерисовывать ничего не надо — меняется
# только прозрачность двух готовых картинок.
GLOW_WIDTH_CALM = 15.0
GLOW_WIDTH_LOUD = 40.0
GLOW_STEPS = 22       # из скольких колец собираем мягкий край
CORNER_RADIUS = 14.0

# Картинку рисуем в половинном разрешении: свечение — размытое пятно без
# мелких деталей, увеличение вдвое на нём не видно, а памяти вчетверо меньше.
IMAGE_SCALE = 0.5

# Сглаживание громкости: вверх идём быстро, вниз медленно — так контур дышит,
# а не дёргается на каждом слоге.
ATTACK = 0.30
RELEASE = 0.08

# Плашка живёт как пузырёк в мессенджере: подгоняется под текст по ширине,
# дальше переносит строки и растёт в высоту, но не больше трёх строк —
# читать простыню во время речи всё равно невозможно.
CAPTION_MAX_CHARS = 300
CAPTION_MAX_LINES = 3
CAPTION_MIN_WIDTH = 170.0
CAPTION_FONT_SIZE = 18.0
CAPTION_PADDING_X = 22.0
CAPTION_PADDING_Y = 11.0
CAPTION_BOTTOM = GLOW_WIDTH_LOUD + 14
CAPTION_GROW = 0.22   # за сколько секунд плашка меняет размер


def fit_to_lines(text, measure, max_height):
    """Отрезает начало по словам, пока текст не уложится в отведённую высоту.

    Обрезаем именно начало: во время диктовки интересны последние сказанные
    слова. Отброшенное обозначаем многоточием, чтобы было видно, что выше
    что-то осталось.
    """
    text = (text or "").strip()
    if not text or measure(text) <= max_height:
        return text
    words = text.split(" ")
    for start in range(1, len(words)):
        candidate = "… " + " ".join(words[start:])
        if measure(candidate) <= max_height:
            return candidate
    return "… " + words[-1]


def smooth_level(current, target, attack=ATTACK, release=RELEASE):
    """Плавно ведёт значение к цели: вверх быстро, вниз медленно.

    Разные скорости не для красоты: с одинаковыми контур мерцает на паузах
    между слогами, а с медленным подъёмом отстаёт от голоса.
    """
    target = max(0.0, min(1.0, float(target)))
    rate = attack if target > current else release
    return current + (target - current) * rate


def _color(rgb, alpha):
    return NSColor.colorWithSRGBRed_green_blue_alpha_(rgb[0], rgb[1], rgb[2], alpha)


def _glow_image(width, height, glow_width):
    """Рамка заданной толщины: вложенные кольца, гаснущие внутрь экрана."""
    width, height = width * IMAGE_SCALE, height * IMAGE_SCALE
    glow_width = glow_width * IMAGE_SCALE
    image = NSImage.alloc().initWithSize_((width, height))
    image.lockFocus()

    full = NSMakeRect(0, 0, width, height)
    step = glow_width / GLOW_STEPS
    context = NSGraphicsContext.currentContext()

    for index in range(GLOW_STEPS):
        outer = NSInsetRect(full, index * step, index * step)
        inner = NSInsetRect(full, (index + 1) * step, (index + 1) * step)
        if inner.size.width <= 0 or inner.size.height <= 0:
            break

        # кольцо = внешний контур минус внутренний, чётно-нечётное заполнение
        radius = CORNER_RADIUS * IMAGE_SCALE
        ring = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(outer, radius, radius)
        ring.appendBezierPath_(
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(inner, radius, radius)
        )
        ring.setWindingRule_(NSEvenOddWindingRule)

        # к центру экрана свечение гаснет квадратично — так край мягче
        fade = (1.0 - index / GLOW_STEPS) ** 2.6
        context.saveGraphicsState()
        ring.addClip()
        NSGradient.alloc().initWithStartingColor_endingColor_(
            _color(GLOW_START, fade), _color(GLOW_END, fade)
        ).drawInRect_angle_(full, GLOW_ANGLE)
        context.restoreGraphicsState()

    image.unlockFocus()
    return image


class ScreenGlow:
    """Рамка на всех экранах. Все методы зовутся только из главного потока."""

    def __init__(self):
        self._windows = []
        self._calm_views = []   # тонкая рамка, горит всегда
        self._loud_views = []   # широкая, проступает на громких местах
        self._pills = []        # подложка живого текста
        self._captions = []
        self._visible = False
        self._caption_text = None
        self._screens = 0
        self._level = 0.0       # сглаженная громкость

    # -------------------------------------------------------------- окна

    def _build(self):
        self._teardown()
        for screen in NSScreen.screens():
            frame = screen.frame()
            width, height = frame.size.width, frame.size.height
            window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_screen_(
                frame, NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False, screen
            )
            window.setOpaque_(False)
            window.setBackgroundColor_(NSColor.clearColor())
            window.setHasShadow_(False)
            window.setIgnoresMouseEvents_(True)  # сквозь рамку можно кликать
            window.setLevel_(NSScreenSaverWindowLevel)
            window.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorStationary
                | NSWindowCollectionBehaviorFullScreenAuxiliary
            )
            window.setAlphaValue_(0.0)

            # координаты внутри окна всегда от нуля, даже у второго монитора
            local = NSMakeRect(0, 0, width, height)
            container = NSView.alloc().initWithFrame_(local)

            calm = self._make_glow(local, width, height, GLOW_WIDTH_CALM)
            loud = self._make_glow(local, width, height, GLOW_WIDTH_LOUD)
            container.addSubview_(loud)
            container.addSubview_(calm)

            pill, caption, max_width, screen_width = self._make_caption(width)
            container.addSubview_(pill)

            window.setContentView_(container)
            # окно всегда непрозрачно: дышат отдельные слои, иначе вместе с
            # рамкой мигал бы и текст — читать его было невозможно
            window.setAlphaValue_(1.0)

            self._windows.append(window)
            self._calm_views.append(calm)
            self._loud_views.append(loud)
            self._pills.append((pill, max_width, screen_width))
            self._captions.append(caption)
        self._screens = len(self._windows)

    def _make_glow(self, local, width, height, glow_width):
        view = NSImageView.alloc().initWithFrame_(local)
        view.setImage_(_glow_image(width, height, glow_width))
        view.setImageScaling_(NSImageScaleAxesIndependently)
        view.setAlphaValue_(0.0)
        return view

    def _make_caption(self, screen_width):
        """Плашка с размытым фоном, как системные HUD-подсказки.

        Белый текст с тенью поверх произвольного фона не читается — под ним
        может оказаться что угодно. Поэтому подложка с размытием.
        """
        max_width = min(screen_width * 0.55, 760.0)
        pill = NSVisualEffectView.alloc().initWithFrame_(
            NSMakeRect(
                (screen_width - CAPTION_MIN_WIDTH) / 2,
                CAPTION_BOTTOM,
                CAPTION_MIN_WIDTH,
                CAPTION_FONT_SIZE * 1.45 + CAPTION_PADDING_Y * 2,
            )
        )
        pill.setMaterial_(NSVisualEffectMaterialHUDWindow)
        pill.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        pill.setState_(NSVisualEffectStateActive)
        pill.setWantsLayer_(True)
        pill.layer().setCornerRadius_(14.0)
        pill.layer().setMasksToBounds_(True)
        pill.setHidden_(True)  # пока нечего показывать — плашки нет

        field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(
                CAPTION_PADDING_X,
                CAPTION_PADDING_Y,
                CAPTION_MIN_WIDTH - CAPTION_PADDING_X * 2,
                CAPTION_FONT_SIZE * 1.45,
            )
        )
        field.setBezeled_(False)
        field.setDrawsBackground_(False)
        field.setEditable_(False)
        field.setSelectable_(False)
        field.setAlignment_(1)  # по центру
        field.setFont_(NSFont.systemFontOfSize_(CAPTION_FONT_SIZE))
        field.setTextColor_(NSColor.whiteColor())
        field.setUsesSingleLineMode_(False)
        field.cell().setWraps_(True)
        field.cell().setLineBreakMode_(NSLineBreakByWordWrapping)
        # поле тянется за плашкой, чтобы не двигать его отдельной анимацией
        field.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        field.setStringValue_("")
        pill.addSubview_(field)
        pill.setFrameSize_(NSMakeSize(CAPTION_MIN_WIDTH, CAPTION_FONT_SIZE * 1.45 + CAPTION_PADDING_Y * 2))
        return pill, field, max_width, screen_width

    def _teardown(self):
        for window in self._windows:
            window.orderOut_(None)
        self._windows = []
        self._calm_views = []
        self._loud_views = []
        self._pills = []
        self._captions = []
        self._screens = 0

    # ------------------------------------------------------------- показ

    def show(self):
        # экраны могли поменяться: подключили монитор или закрыли крышку
        if not self._windows or self._screens != len(NSScreen.screens()):
            self._build()
        self._caption_text = None
        self._level = 0.0
        self._visible = True
        self.set_caption("")
        self._apply_level()
        for window in self._windows:
            window.orderFrontRegardless()

    def hide(self):
        for window in self._windows:
            window.orderOut_(None)
        self._visible = False
        self._caption_text = None

    @property
    def visible(self):
        return self._visible

    def set_level(self, level):
        """Громкость 0..1 → толщина и яркость контура.

        Значение сглаживаем: вверх идём быстро, вниз медленно. Без этого
        контур дёргается на каждом слоге, а с одинаковыми скоростями — мерцает.
        """
        if not self._visible:
            return
        self._level = smooth_level(self._level, max(0.0, min(1.0, float(level))) ** 0.6)
        self._apply_level()

    def _apply_level(self):
        loud = self._level
        # тонкая рамка почти не меняется — она показывает, что запись идёт;
        # растёт наружу широкая, и контур на глазах прибавляет в толщине
        calm_alpha = 0.45 + 0.30 * loud
        loud_alpha = loud ** 1.4
        for view in self._calm_views:
            view.setAlphaValue_(calm_alpha)
        for view in self._loud_views:
            view.setAlphaValue_(loud_alpha)

    def _measure(self, text, width):
        """Высота текста, если разложить его по строкам в заданной ширине."""
        attributed = NSAttributedString.alloc().initWithString_attributes_(
            text, {NSFontAttributeName: NSFont.systemFontOfSize_(CAPTION_FONT_SIZE)}
        )
        return attributed.boundingRectWithSize_options_(
            NSMakeSize(width, 10_000), NSStringDrawingUsesLineFragmentOrigin
        ).size

    def set_caption(self, text):
        """Живой текст под рамкой. Плашка подгоняется под него, как пузырёк чата."""
        if not self._visible or text == self._caption_text:
            return
        self._caption_text = text
        shown = (text or "").strip()
        if len(shown) > CAPTION_MAX_CHARS:
            shown = shown[-CAPTION_MAX_CHARS:]

        for (pill, max_width, screen_width), caption in zip(self._pills, self._captions):
            if not shown:
                pill.setHidden_(True)  # пустая плашка на экране не нужна
                caption.setStringValue_("")
                continue

            text_width = max_width - CAPTION_PADDING_X * 2
            line_height = self._measure("Ap", text_width).height
            fitted = fit_to_lines(
                shown,
                lambda candidate: self._measure(candidate, text_width).height,
                line_height * CAPTION_MAX_LINES,
            )
            size = self._measure(fitted, text_width)

            width = min(
                max_width,
                max(CAPTION_MIN_WIDTH, size.width + CAPTION_PADDING_X * 2 + 2),
            )
            height = size.height + CAPTION_PADDING_Y * 2
            frame = NSMakeRect(
                (screen_width - width) / 2, CAPTION_BOTTOM, width, height
            )

            caption.setStringValue_(fitted)
            if pill.isHidden():
                # первая фраза: появляемся сразу нужного размера, без рывка
                pill.setFrame_(frame)
                pill.setHidden_(False)
            else:
                NSAnimationContext.beginGrouping()
                NSAnimationContext.currentContext().setDuration_(CAPTION_GROW)
                pill.animator().setFrame_(frame)
                NSAnimationContext.endGrouping()


def is_available():
    """Есть ли вообще экраны — на машине без монитора рамке делать нечего."""
    try:
        return bool(NSScreen.screens())
    except Exception:  # noqa: BLE001
        log.debug("NSScreen недоступен", exc_info=True)
        return False
