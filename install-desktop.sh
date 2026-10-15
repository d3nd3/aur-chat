#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
DEST="${AUR_CHAT_ROOT:-$HOME/.local/share/aur-chat}"
BIN="${INSTALL_BIN:-$HOME/.local/bin/aur-chat}"
AURELIA_ROOT="${AURELIA_ROOT:-$ROOT/../../../Aurelia}"
mkdir -p "$DEST" "$HOME/.local/bin" "$HOME/.local/share/applications" \
  "$HOME/.local/share/icons/hicolor/scalable/apps"
rm -rf "$DEST/aur_chat"
cp -a "$ROOT/aur_chat" "$DEST/"
install -m755 "$ROOT/aur-chat" "$BIN"
if [[ -x "$AURELIA_ROOT/target/release/aurelia" ]]; then
  install -m755 "$AURELIA_ROOT/target/release/aurelia" "$HOME/.local/bin/aurelia"
fi
install -m644 "$ROOT/share/applications/aur-chat.desktop" "$HOME/.local/share/applications/"
install -m644 "$ROOT/share/icons/hicolor/scalable/apps/aur-chat.svg" \
  "$HOME/.local/share/icons/hicolor/scalable/apps/"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
if [[ -x "$HOME/.local/bin/aurelia" ]]; then
  echo "Installed $BIN and Aurelia (package in $DEST). Log in: aurelia login"
else
  echo "Installed $BIN (package in $DEST). Build Aurelia or set AURELIA_BIN before using it."
fi
