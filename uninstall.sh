#!/bin/bash
# Удаление Whisper.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="/Applications/Whisper.app"
LABEL="local.whisper.dictation"

launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
pkill -f "$APP/Contents/MacOS/Whisper" 2>/dev/null || true
rm -rf "$APP" "$HERE/.venv" "$HERE/build" "$HERE/dist"

echo "Whisper закрыт и удалён из программ."
echo
echo "Осталось — удалите, если больше не нужно:"
echo "  настройки      rm -rf ~/Library/Application\\ Support/Whisper"
echo "  журналы        rm -rf ~/Library/Logs/Whisper"
echo "  распознавание  rm -rf ~/.cache/huggingface/hub/models--mlx-community--whisper-large-v3-turbo   # ~1.6 ГБ"
echo "  исходники      rm -rf \"$HERE\""
echo
echo "И уберите «Whisper» из Системные настройки → Универсальный доступ."
