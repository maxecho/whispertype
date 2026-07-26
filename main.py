"""Точка входа для собранного WhisperType.app.

py2app запускает именно этот файл. В alias-сборке он лежит в бандле
символической ссылкой, поэтому путь к проекту берём через realpath.
"""

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(os.path.realpath(__file__)).parent))

from whisperapp.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
