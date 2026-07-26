"""Запуск Whisper из командной строки.

    python -m whisperapp              — запустить программу
    python -m whisperapp doctor       — проверить, всё ли на месте
    python -m whisperapp tapcheck     — убедиться, что нажатия доходят
    python -m whisperapp devices      — список микрофонов
    python -m whisperapp selftest [N] — записать N секунд и показать расшифровку

То же самое умеет и собранное приложение:

    /Applications/Whisper.app/Contents/MacOS/Whisper doctor
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

    has_access = accessibility_ok()
    ok = ok and has_access
    print("\nРазрешения:")
    print(f"  {'✓' if has_access else '✗'} нажимать клавиши за вас")
    if not has_access:
        print(f"    включите в Универсальном доступе: {parent_app_path()}")

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
    print(f"Журнал: {LOG_PATH}")
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
        result = f"✗ Перехват включён, но не сработал. Событий получено: {listener.events_seen}"
    print(result)
    logging.getLogger("tapcheck").info(result)
    return 0 if fired else 1


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
