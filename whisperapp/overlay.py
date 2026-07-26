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
    NSLineBreakByTruncatingHead,
    NSMakeRect,
    NSScreen,
    NSScreenSaverWindowLevel,
    NSTextField,
    NSView,
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

CAPTION_MAX_CHARS = 140
CAPTION_HEIGHT = 40.0
CAPTION_FONT_SIZE = 18.0
CAPTION_PADDING = 22.0


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

            pill, caption = self._make_caption(width)
            container.addSubview_(pill)

            window.setContentView_(container)
            # окно всегда непрозрачно: дышат отдельные слои, иначе вместе с
            # рамкой мигал бы и текст — читать его было невозможно
            window.setAlphaValue_(1.0)

            self._windows.append(window)
            self._calm_views.append(calm)
            self._loud_views.append(loud)
            self._pills.append(pill)
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
        width = min(screen_width * 0.55, 760.0)
        pill = NSVisualEffectView.alloc().initWithFrame_(
            NSMakeRect(
                (screen_width - width) / 2,
                GLOW_WIDTH_LOUD + 14,
                width,
                CAPTION_HEIGHT,
            )
        )
        pill.setMaterial_(NSVisualEffectMaterialHUDWindow)
        pill.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        pill.setState_(NSVisualEffectStateActive)
        pill.setWantsLayer_(True)
        pill.layer().setCornerRadius_(CAPTION_HEIGHT / 2)
        pill.layer().setMasksToBounds_(True)
        pill.setHidden_(True)  # пока нечего показывать — плашки нет

        field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(
                CAPTION_PADDING,
                (CAPTION_HEIGHT - CAPTION_FONT_SIZE * 1.5) / 2,
                width - CAPTION_PADDING * 2,
                CAPTION_FONT_SIZE * 1.5,
            )
        )
        field.setBezeled_(False)
        field.setDrawsBackground_(False)
        field.setEditable_(False)
        field.setSelectable_(False)
        field.setAlignment_(1)  # по центру
        field.setFont_(NSFont.systemFontOfSize_(CAPTION_FONT_SIZE))
        field.setTextColor_(NSColor.whiteColor())
        # обрезаем начало, а не конец: интересны последние сказанные слова
        field.cell().setLineBreakMode_(NSLineBreakByTruncatingHead)
        field.setStringValue_("")
        pill.addSubview_(field)
        return pill, field

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

    def set_caption(self, text):
        """Живой текст под рамкой. Пустая строка убирает подпись."""
        if not self._visible or text == self._caption_text:
            return
        self._caption_text = text
        shown = (text or "").strip()
        if len(shown) > CAPTION_MAX_CHARS:
            shown = shown[-CAPTION_MAX_CHARS:]
        for pill, caption in zip(self._pills, self._captions):
            caption.setStringValue_(shown)
            pill.setHidden_(not shown)  # пустая плашка на экране не нужна


def is_available():
    """Есть ли вообще экраны — на машине без монитора рамке делать нечего."""
    try:
        return bool(NSScreen.screens())
    except Exception:  # noqa: BLE001
        log.debug("NSScreen недоступен", exc_info=True)
        return False
