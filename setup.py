"""Сборка WhisperType.app.

    .venv/bin/python setup.py py2app -A

Сборка в режиме alias: бандл ссылается на исходники и на .venv, поэтому весит
килобайты и пересобирается за секунду. Смысл бандла не в переносимости, а в том,
чтобы macOS видела приложение «WhisperType» с собственной иконкой — в списке
Универсального доступа, в запросе доступа к микрофону и в Force Quit.
"""

from setuptools import setup

from whisperapp import branding as b

setup(
    app=["main.py"],
    name=b.APP_NAME,
    setup_requires=["py2app"],
    options={
        "py2app": {
            "iconfile": str(b.ICON_ICNS),
            "plist": {
                "CFBundleName": b.APP_NAME,
                "CFBundleDisplayName": b.APP_NAME,
                "CFBundleIdentifier": b.BUNDLE_ID,
                "CFBundleShortVersionString": b.VERSION,
                "CFBundleVersion": b.VERSION,
                "NSHumanReadableCopyright": b.TAGLINE,
                # только строка меню, без значка в Dock и без переключения по ⌘Tab
                "LSUIElement": True,
                "LSMinimumSystemVersion": "13.0",
                "NSMicrophoneUsageDescription": (
                    f"{b.APP_NAME} слушает микрофон, чтобы превратить вашу речь в текст. "
                    "Запись остаётся на этом маке и не сохраняется на диск."
                ),
            },
        }
    },
)
