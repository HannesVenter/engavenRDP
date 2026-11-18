#!/usr/bin/env bash
# Install engavenRDP for the current user (no root needed for the app itself).
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

# 1. FreeRDP (needs root; only suggested)
if ! command -v xfreerdp3 >/dev/null && ! command -v xfreerdp >/dev/null; then
  say "FreeRDP is not installed. Install it with one of:"
  echo "    Ubuntu/Debian:  sudo apt install freerdp3-x11     (or freerdp2-x11 on older releases)"
  echo "    Fedora:         sudo dnf install freerdp"
  echo "    Arch:           sudo pacman -S freerdp"
  echo "    openSUSE:       sudo zypper install freerdp"
fi

# Qt's X11 plugin needs libxcb-cursor on some distros
if command -v ldconfig >/dev/null && ! ldconfig -p 2>/dev/null | grep -q libxcb-cursor.so.0; then
  say "Qt needs libxcb-cursor:  sudo apt install libxcb-cursor0  |  sudo dnf install xcb-util-cursor  |  sudo pacman -S xcb-util-cursor"
fi

# 2. The app
if command -v pipx >/dev/null; then
  say "Installing with pipx"
  pipx install --force ".[keyring]"
else
  say "pipx not found — installing into ~/.local with pip"
  python3 -m pip install --user --upgrade ".[keyring]" || \
    python3 -m pip install --user --break-system-packages --upgrade ".[keyring]"
fi

# 3. Desktop launcher + icon
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
mkdir -p "$APPS" "$ICONS"
cp data/engavenrdp.desktop "$APPS/engavenrdp.desktop"
cp data/engavenrdp.svg "$ICONS/engavenrdp.svg"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" >/dev/null 2>&1 || true

say "Done. Start it from your app menu or run:  engavenrdp"
say "Check your setup with:  engavenrdp --check"
