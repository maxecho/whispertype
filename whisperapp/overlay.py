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
    NSMakeRect,
    NSScreen,
    NSScreenSaverWindowLevel,
    NSTextField,
    NSView,
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

GLOW_WIDTH = 26.0     # толщина свечения в точках
GLOW_STEPS = 22       # из скольких колец собираем мягкий край
CORNER_RADIUS = 14.0

CAPTION_MAX_CHARS = 110
CAPTION_HEIGHT = 30.0


def _color(rgb, alpha):
    return NSColor.colorWithSRGBRed_green_blue_alpha_(rgb[0], rgb[1], rgb[2], alpha)


def _glow_image(width, height):
    """Рамка: вложенные кольца, гаснущие внутрь экрана."""
    image = NSImage.alloc().initWithSize_((width, height))
    image.lockFocus()

    full = NSMakeRect(0, 0, width, height)
    step = GLOW_WIDTH / GLOW_STEPS
    context = NSGraphicsContext.currentContext()

    for index in range(GLOW_STEPS):
        outer = NSInsetRect(full, index * step, index * step)
        inner = NSInsetRect(full, (index + 1) * step, (index + 1) * step)
        if inner.size.width <= 0 or inner.size.height <= 0:
            break

        # кольцо = внешний контур минус внутренний, чётно-нечётное заполнение
        ring = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            outer, CORNER_RADIUS, CORNER_RADIUS
        )
        ring.appendBezierPath_(
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                inner, CORNER_RADIUS, CORNER_RADIUS
            )
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
        self._captions = []
        self._visible = False
        self._caption_text = None
        self._screens = 0

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

            glow = NSImageView.alloc().initWithFrame_(local)
            glow.setImage_(_glow_image(width, height))
            glow.setImageScaling_(NSImageScaleAxesIndependently)
            container.addSubview_(glow)

            caption = self._make_caption(width)
            container.addSubview_(caption)

            window.setContentView_(container)
            self._windows.append(window)
            self._captions.append(caption)
        self._screens = len(self._windows)

    def _make_caption(self, screen_width):
        width = min(screen_width * 0.62, 900.0)
        field = NSTextField.alloc().initWithFrame_(
            NSMakeRect((screen_width - width) / 2, GLOW_WIDTH + 16, width, CAPTION_HEIGHT)
        )
        field.setBezeled_(False)
        field.setDrawsBackground_(False)
        field.setEditable_(False)
        field.setSelectable_(False)
        field.setAlignment_(1)  # NSTextAlignmentCenter
        field.setFont_(NSFont.systemFontOfSize_(16))
        field.setTextColor_(NSColor.whiteColor())
        field.setStringValue_("")
        # тень: текст читается и на светлом фоне под ним
        field.setWantsLayer_(True)
        field.layer().setShadowOpacity_(0.9)
        field.layer().setShadowRadius_(5.0)
        field.layer().setShadowOffset_((0, -1))
        return field

    def _teardown(self):
        for window in self._windows:
            window.orderOut_(None)
        self._windows = []
        self._captions = []
        self._screens = 0

    # ------------------------------------------------------------- показ

    def show(self):
        # экраны могли поменяться: подключили монитор или закрыли крышку
        if not self._windows or self._screens != len(NSScreen.screens()):
            self._build()
        self._caption_text = None
        self.set_caption("")
        for window in self._windows:
            window.setAlphaValue_(0.0)
            window.orderFrontRegardless()
        self._visible = True

    def hide(self):
        for window in self._windows:
            window.orderOut_(None)
        self._visible = False
        self._caption_text = None

    @property
    def visible(self):
        return self._visible

    def set_level(self, level):
        """Громкость 0..1 → прозрачность рамки."""
        if not self._visible:
            return
        level = max(0.0, min(1.0, float(level)))
        # снизу подпираем: в тишине рамка тусклая, но видно, что запись идёт
        alpha = 0.20 + 0.80 * level ** 0.6
        for window in self._windows:
            window.setAlphaValue_(alpha)

    def set_caption(self, text):
        """Живой текст под рамкой. Пустая строка убирает подпись."""
        if not self._visible or text == self._caption_text:
            return
        self._caption_text = text
        shown = text or ""
        if len(shown) > CAPTION_MAX_CHARS:
            shown = "…" + shown[-CAPTION_MAX_CHARS:]
        for caption in self._captions:
            caption.setStringValue_(shown)


def is_available():
    """Есть ли вообще экраны — на машине без монитора рамке делать нечего."""
    try:
        return bool(NSScreen.screens())
    except Exception:  # noqa: BLE001
        log.debug("NSScreen недоступен", exc_info=True)
        return False
