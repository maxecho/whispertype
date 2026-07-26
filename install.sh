#!/bin/bash
# Установка Whisper: окружение, распознавание, само приложение и автозапуск.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"
APP="/Applications/WhisperType.app"
LABEL="io.github.maxecho.whispertype"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

say() { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }

if [[ "$(uname -m)" != "arm64" ]]; then
  echo "Нужен Mac на чипе Apple: распознавание считается на его графическом ядре." >&2
  exit 1
fi

# --- Python ---------------------------------------------------------------
say "Ищу Python"
PY=""
for candidate in \
  /opt/homebrew/opt/python@3.13/bin/python3.13 \
  /opt/homebrew/opt/python@3.12/bin/python3.12 \
  /opt/homebrew/opt/python@3.11/bin/python3.11 \
  /opt/homebrew/bin/python3
do
  [[ -x "$candidate" ]] && PY="$candidate" && break
done

if [[ -z "$PY" ]]; then
  say "Ставлю Python через Homebrew"
  brew install python@3.12
  PY=/opt/homebrew/opt/python@3.12/bin/python3.12
fi
echo "  $("$PY" -V)"

# --- окружение ------------------------------------------------------------
if [[ ! -x "$VENV/bin/python" ]]; then
  say "Готовлю рабочее окружение"
  "$PY" -m venv --copies "$VENV"
fi

say "Ставлю библиотеки (в первый раз это несколько минут)"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r "$HERE/requirements.txt"
"$VENV/bin/python" -m pip install --quiet pillow py2app

# --- распознавание --------------------------------------------------------
MODEL="$("$VENV/bin/python" -c "
import sys; sys.path.insert(0, '$HERE')
from whisperapp.config import load_config; print(load_config()['model'])
")"

say "Скачиваю распознавание: $MODEL (~0.5 ГБ, один раз)"
"$VENV/bin/python" - "$MODEL" <<'PY'
import sys
from huggingface_hub import snapshot_download
path = snapshot_download(
    sys.argv[1],
    allow_patterns=["*.json", "*.safetensors", "*.npz", "*.txt", "*.tiktoken"],
)
print("  лежит в", path)
PY

# --- иконки и сборка ------------------------------------------------------
say "Рисую иконки"
"$VENV/bin/python" "$HERE/tools/make_icons.py"

# Бандл — тонкая обёртка: код лежит рядом, в исходниках, и подхватывается при
# запуске. Поэтому пересобираем только когда меняется сама обёртка (версия,
# иконка, Info.plist). Это не оптимизация, а необходимость: у пересобранного
# бандла другая подпись, и macOS перестаёт доверять уже выданному разрешению
# «Универсальный доступ» — его пришлось бы включать заново после каждой правки.
NEED_BUNDLE=1
if [[ -d "$APP" ]]; then
  INSTALLED_VERSION="$(defaults read "$APP/Contents/Info" CFBundleShortVersionString 2>/dev/null || echo "")"
  INSTALLED_ID="$(defaults read "$APP/Contents/Info" CFBundleIdentifier 2>/dev/null || echo "")"
  WANT_VERSION="$("$VENV/bin/python" -c "
import sys; sys.path.insert(0, '$HERE')
from whisperapp import branding; print(branding.VERSION)
")"
  if [[ "$INSTALLED_VERSION" == "$WANT_VERSION" && "$INSTALLED_ID" == "$LABEL" ]]; then
    NEED_BUNDLE=0
  fi
fi

# Работающую копию в любом случае закрываем — код обновится при следующем старте.
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
pkill -f "$APP/Contents/MacOS/WhisperType" 2>/dev/null || true
sleep 1

if [[ "$NEED_BUNDLE" == "1" ]]; then
  say "Собираю WhisperType.app"
  rm -rf "$HERE/build" "$HERE/dist"
  (cd "$HERE" && "$VENV/bin/python" setup.py py2app -A >/dev/null)
  rm -rf "$APP"
  ditto "$HERE/dist/WhisperType.app" "$APP"
  echo "  $APP"
  REGRANT=1
else
  say "Приложение на месте, пересборка не нужна"
  echo "  разрешения сохранятся"
  REGRANT=0
fi

# Уборка после старого имени (до v1.1 приложение называлось Whisper).
# Чужой Whisper.app не трогаем — проверяем, что это именно наш bundle id.
launchctl bootout "gui/$UID/local.whisper.dictation" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/local.whisper.dictation.plist"
if [[ "$(defaults read /Applications/Whisper.app/Contents/Info CFBundleIdentifier 2>/dev/null)" == "local.whisper.dictation" ]]; then
  rm -rf /Applications/Whisper.app
fi

# --- автозапуск -----------------------------------------------------------
say "Включаю запуск при входе в систему"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/WhisperType"
"$VENV/bin/python" - "$HERE" "$APP" "$PLIST" "$LABEL" <<'PY'
import pathlib
import sys

sys.path.insert(0, sys.argv[1])
from whisperapp import autostart

here, app, plist_path, label = sys.argv[1:5]
plist = autostart.TEMPLATE.format(
    label=label,
    arguments=f"    <string>{app}/Contents/MacOS/WhisperType</string>\n",
    workdir=here,
    log=str(pathlib.Path.home() / "Library/Logs/WhisperType/whispertype-launch.log"),
)
pathlib.Path(plist_path).write_text(plist, "utf-8")
print("  " + plist_path)
PY

say "Запускаю"
# Через open, а не launchctl: так macOS видит именно приложение Whisper,
# и разрешение записывается на него, а не на терминал.
open -a "$APP"

cat <<EOF

  Готово. Значок звуковой волны — в правой части строки меню.

$(if [[ "$REGRANT" == "1" ]]; then cat <<'REG'
  Осталось выдать два разрешения — macOS держит их раздельно:

    1. Мониторинг ввода — чтобы программа замечала горячую клавишу.
    2. Универсальный доступ — чтобы она вставляла текст за вас.

  Программа сама покажет окна и откроет нужные панели настроек;
  включите в обеих «WhisperType». Перезапускать ничего не придётся.

  Если программа уже стояла и была в списке — macOS могла запомнить
  прежнюю версию: выключите галочку и включите обратно.
REG
else cat <<'REG'
  Разрешения не тронуты — приложение осталось прежним, обновился только код.
REG
fi)

  Как пользоваться:
    • поставьте курсор в поле ввода
    • дважды нажмите ⌘ справа — значок станет красным
    • говорите
    • дважды нажмите ⌘ справа ещё раз — текст появится сам
    • Esc во время записи — отмена

  Всё остальное есть в меню программы: горячая клавиша, язык,
  «Как этим пользоваться» и «Что-то не работает».

EOF
