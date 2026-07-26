#!/bin/bash
# Удаление Whisper.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="/Applications/WhisperType.app"
LABEL="io.github.maxecho.whispertype"

launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
pkill -f "$APP/Contents/MacOS/WhisperType" 2>/dev/null || true
rm -rf "$APP" "$HERE/.venv" "$HERE/build" "$HERE/dist"

echo "WhisperType закрыт и удалён из программ."
echo
echo "Осталось — удалите, если больше не нужно:"
echo "  настройки      rm -rf ~/Library/Application\\ Support/WhisperType"
echo "  журналы        rm -rf ~/Library/Logs/WhisperType"
echo "  распознавание  rm -rf ~/.cache/huggingface/hub/models--mlx-community--whisper-large-v3-turbo-q4   # ~0.5 ГБ"
echo "  исходники      rm -rf \"$HERE\""
echo
echo "И уберите «WhisperType» из Системные настройки → Универсальный доступ."
