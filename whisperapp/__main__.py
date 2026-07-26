"""Запуск WhisperType из командной строки.

    python -m whisperapp              — запустить программу
    python -m whisperapp doctor       — проверить, всё ли на месте
    python -m whisperapp tapcheck     — убедиться, что нажатия доходят
    python -m whisperapp tapwatch [N] — N секунд показывать, что видит перехват
    python -m whisperapp glowtest [N] — N секунд показывать подсветку с примером текста
    python -m whisperapp devices      — список микрофонов
    python -m whisperapp selftest [N] — записать N секунд и показать расшифровку

То же самое умеет и собранное приложение:

    /Applications/WhisperType.app/Contents/MacOS/WhisperType doctor
"""

import sys

from . import branding as b
from .config import LOG_PATH, load_config, setup_logging


def cmd_devices():
    from .recorder import list_input_devices

    print("Микрофоны:")
    for index, name in list_input_devices():
        print(f"  [{index}] {name}")
    print('\nВыбрать другой: "input_device": <номер или имя> в настройках')


def cmd_doctor():
    cfg = load_config()
    ok = True

    print(f"{b.APP_NAME} {b.VERSION}\n")
    print("Библиотеки:")
    for module in ("numpy", "sounddevice", "mlx", "mlx_whisper", "rumps", "Quartz"):
        try:
            __import__(module)
            print(f"  ✓ {module}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  ✗ {module}: {exc}")

    from .inserter import accessibility_ok, parent_app_path

    from .inserter import input_monitoring_state, GRANTED, DENIED

    has_access = accessibility_ok()
    listen = input_monitoring_state()
    ok = ok and has_access and listen == GRANTED
    print("\nРазрешения:")
    print(f"  {'✓' if has_access else '✗'} нажимать клавиши за вас (Универсальный доступ)")
    listen_mark = {GRANTED: "✓", DENIED: "✗"}.get(listen, "?")
    listen_word = {GRANTED: "есть", DENIED: "запрещено"}.get(listen, "ещё не спрашивали")
    print(f"  {listen_mark} слышать нажатия (Мониторинг ввода): {listen_word}")
    if not has_access:
        print(f"    включите в Универсальном доступе: {b.pretty_path(parent_app_path())}")

    from .recorder import probe_microphone

    try:
        probe_microphone()
        print("  ✓ микрофон")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"  ✗ микрофон: {exc}")

    print(f"\nРаспознавание ({cfg['model']}):")
    try:
        from .transcriber import Transcriber

        transcriber = Transcriber(cfg)
        if transcriber.warmup():
            print("  ✓ работает")
        else:
            ok = False
            print(f"  ✗ {transcriber.error}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"  ✗ {exc}")

    from .hotkey import describe

    print(f"\nГорячая клавиша: {describe(cfg)}")
    print(f"Язык: {cfg.get('language') or 'определяется сам'}")
    print(f"Журнал: {b.pretty_path(LOG_PATH)}")
    print("\nИтог:", "всё на месте" if ok else "есть проблемы, см. ✗ выше")
    return 0 if ok else 1


def cmd_tapcheck():
    """Сквозная проверка перехвата: программа сама нажимает и сама слушает."""
    import time

    import Quartz

    from .hotkey import DEVICE_MASKS, KEYCODES, HotkeyListener, describe

    cfg = load_config()
    if cfg["hotkey"]["mode"] != "double_tap":
        print("Проверка написана для двойного нажатия; сейчас выбрана комбинация.")
        return 2

    key = cfg["hotkey"]["key"]
    keycode, device_mask = KEYCODES[key], DEVICE_MASKS[key]

    fired = []
    listener = HotkeyListener(cfg, lambda: fired.append(1))
    listener.start()
    if not listener.alive:
        print("✗ Перехват не включился — macOS не дала разрешение нажимать клавиши.")
        return 1

    def send(pressed):
        event = Quartz.CGEventCreateKeyboardEvent(None, keycode, pressed)
        Quartz.CGEventSetType(event, Quartz.kCGEventFlagsChanged)
        Quartz.CGEventSetFlags(event, device_mask if pressed else 0)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    print(f"Изображаю {describe(cfg)}…")
    for _ in range(2):
        send(True)
        time.sleep(0.05)
        send(False)
        time.sleep(0.12)
    time.sleep(0.4)
    listener.stop()

    import logging

    if fired:
        result = f"✓ Нажатия доходят, сработало раз: {len(fired)}"
    else:
        names = {
            int(Quartz.kCGEventKeyDown): "нажатие клавиши",
            int(Quartz.kCGEventFlagsChanged): "модификатор",
            int(Quartz.kCGEventTapDisabledByTimeout): "СИСТЕМА ОТКЛЮЧИЛА (таймаут)",
            int(Quartz.kCGEventTapDisabledByUserInput): "СИСТЕМА ОТКЛЮЧИЛА (ввод)",
        }
        detail = ", ".join(
            f"{names.get(t, t)}×{n}" for t, n in sorted(listener.event_types.items())
        ) or "ничего"
        result = (
            f"✗ Перехват включён, но не сработал. Событий: {listener.events_seen} ({detail})"
        )
    print(result)
    logging.getLogger("tapcheck").info(result)
    return 0 if fired else 1


def cmd_tapwatch(seconds):
    """Живая проверка: N секунд смотрим, какие нажатия доходят до программы.

    В отличие от tapcheck ничего не подделываем — нужны настоящие нажатия
    человека, зато и результат честный.
    """
    import logging
    import time

    from .hotkey import BY_KEYCODE, HotkeyListener, describe

    cfg = load_config()
    fired = []
    listener = HotkeyListener(cfg, lambda: fired.append(time.monotonic()))
    listener.start()

    watch = logging.getLogger("tapwatch")
    if not listener.alive:
        watch.warning("✗ Перехват не включился — нет разрешения нажимать клавиши")
        return 1

    target = cfg["hotkey"].get("key")
    watch.info("Смотрю %.0f секунд. Нажмите %s", seconds, describe(cfg))
    seen = {}
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(0.25)
        for code, count in list(listener.event_types.items()):
            seen[code] = count
    listener.stop()

    watch.info(
        "Итог: событий %d (настоящих %d), система глушила %d раз, сработало %d раз",
        listener.events_seen, listener.real_events,
        listener.disabled_count, len(fired),
    )
    if listener.real_events == 0:
        watch.warning("✗ До программы не дошло ни одного нажатия — разрешение не работает")
        return 1
    if not fired:
        watch.warning(
            "✗ Нажатия доходят, но %s не распозналось. Клавиша: %s",
            describe(cfg), target,
        )
        return 1
    watch.info("✓ Всё работает")
    return 0


def cmd_glowtest(seconds):
    """Показывает подсветку с игрушечным дыханием — посмотреть, как она выглядит."""
    import logging
    import math

    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
    from PyObjCTools import AppHelper

    from .overlay import ScreenGlow, is_available

    watch = logging.getLogger("glowtest")
    if not is_available():
        watch.warning("Экранов не найдено")
        return 1

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    glow = ScreenGlow()
    glow.show()
    watch.info("Показываю подсветку %.0f секунд", seconds)

    state = {"frame": 0}
    phrases = [
        "",
        "Привет,",
        "Привет, это живой текст",
        "Привет, это живой текст, который появляется прямо по ходу диктовки",
        "Привет, это живой текст, который появляется прямо по ходу диктовки — "
        "плашка растёт вместе с ним, сначала вширь, потом переносит строки",
        "Привет, это живой текст, который появляется прямо по ходу диктовки — "
        "плашка растёт вместе с ним, сначала вширь, потом переносит строки и "
        "тянется вверх, а когда места не хватает, начало отрезается многоточием",
    ]
    steps = int(seconds / 0.05)

    def beat():
        index = state["frame"]
        state["frame"] += 1
        if index >= steps:
            glow.hide()
            AppHelper.stopEventLoop()
            return
        # дыхание: две синусоиды, чтобы не выглядело механически
        level = 0.35 + 0.4 * math.sin(index / 7.0) ** 2 + 0.2 * math.sin(index / 2.3) ** 2
        glow.set_level(min(1.0, level))
        glow.set_caption(phrases[min(index // max(1, steps // len(phrases)), len(phrases) - 1)])
        AppHelper.callLater(0.05, beat)

    AppHelper.callLater(0.05, beat)
    AppHelper.runEventLoop()
    watch.info("Подсветка отработала без ошибок")
    return 0


def cmd_selftest(seconds):
    import time

    from .recorder import Recorder
    from .transcriber import Transcriber

    cfg = load_config()
    transcriber = Transcriber(cfg)
    print(f"Включаю распознавание ({cfg['model']})…")
    if not transcriber.warmup():
        print("Не получилось:", transcriber.error)
        return 1

    recorder = Recorder(cfg.get("input_device"))
    print(f"Говорите — пишу {seconds:.0f} секунд…")
    recorder.start()
    time.sleep(seconds)
    audio = recorder.stop()

    started = time.monotonic()
    text = transcriber.transcribe(audio)
    print(f"\n(расшифровка заняла {time.monotonic() - started:.1f} с)")
    print("Услышал:", text or "— тишина —")
    return 0


def main():
    setup_logging()
    # Finder подмешивает свой -psn_0_… при запуске приложения — он нам не команда
    argv = [arg for arg in sys.argv[1:] if not arg.startswith("-psn")]
    command = argv[0] if argv else "run"

    if command == "doctor":
        return cmd_doctor()
    if command == "tapcheck":
        return cmd_tapcheck()
    if command == "tapwatch":
        return cmd_tapwatch(float(argv[1]) if len(argv) > 1 else 20.0)
    if command == "glowtest":
        return cmd_glowtest(float(argv[1]) if len(argv) > 1 else 8.0)
    if command == "devices":
        cmd_devices()
        return 0
    if command == "selftest":
        return cmd_selftest(float(argv[1]) if len(argv) > 1 else 5.0)
    if command == "run":
        from .app import main as run_app

        run_app()
        return 0

    print(__doc__)
    return 0 if command in ("-h", "--help", "help") else 2


if __name__ == "__main__":
    sys.exit(main())
