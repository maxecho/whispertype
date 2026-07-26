"""Запуск Whisper при входе в систему (LaunchAgent)."""

import logging
import pathlib
import sys

from .branding import APP_NAME, BUNDLE_ID

log = logging.getLogger("autostart")

LABEL = BUNDLE_ID
PLIST_PATH = pathlib.Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"

TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
{arguments}  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONPATH</key><string>{workdir}</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>KeepAlive</key>
  <dict><key>SuccessfulExit</key><false/></dict>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""


def project_dir():
    return pathlib.Path(__file__).resolve().parent.parent


def bundle_executable():
    """Исполняемый файл внутри Whisper.app, если программа собрана как приложение."""
    try:
        from Foundation import NSBundle

        path = NSBundle.mainBundle().executablePath()
        if path and ".app/Contents/MacOS/" in str(path):
            return pathlib.Path(str(path))
    except Exception:  # noqa: BLE001
        log.debug("NSBundle недоступен", exc_info=True)
    return None


def launch_arguments():
    """Чем запускать: собранным приложением или пакетом из исходников."""
    executable = bundle_executable()
    if executable is not None:
        return [str(executable)]
    return [sys.executable, "-m", "whisperapp"]


def render():
    from .config import LOG_DIR

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    arguments = "".join(f"    <string>{arg}</string>\n" for arg in launch_arguments())
    return TEMPLATE.format(
        label=LABEL,
        arguments=arguments,
        workdir=str(project_dir()),
        log=str(LOG_DIR / f"{APP_NAME.lower()}-launch.log"),
    )


def enabled():
    return PLIST_PATH.exists()


def enable():
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(render(), "utf-8")
    log.info("Автозапуск включён: %s", PLIST_PATH)


def disable():
    # Файл просто убираем, работающую копию не трогаем: выключение автозапуска
    # не должно закрывать программу прямо сейчас.
    if PLIST_PATH.exists():
        PLIST_PATH.unlink()
    log.info("Автозапуск выключен")


def toggle():
    if enabled():
        disable()
    else:
        enable()
    return enabled()
