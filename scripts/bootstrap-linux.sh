#!/usr/bin/env bash
set -euo pipefail

install_system=0
with_gui=1
with_batch=0

for arg in "$@"; do
  case "$arg" in
    --install-system) install_system=1 ;;
    --no-gui) with_gui=0 ;;
    --with-batch) with_batch=1 ;;
    -h|--help)
      cat <<'EOF'
Usage: scripts/bootstrap-linux.sh [--install-system] [--no-gui] [--with-batch]

Creates project-local .venv and installs Python deps.
--install-system also installs distro packages with sudo.
--with-batch also installs faster-whisper/WhisperX/pyannote into .venv.
EOF
      exit 0
      ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")/.."

detect_distro() {
  if [ -f /etc/arch-release ]; then echo arch; return; fi
  if [ -f /etc/fedora-release ]; then echo fedora; return; fi
  if [ -f /etc/debian_version ]; then echo debian; return; fi
  echo unknown
}

system_install_command() {
  case "$(detect_distro)" in
    arch)
      echo "sudo pacman -S --needed python python-pip python-virtualenv python-aiohttp python-evdev openssl pipewire-pulse wl-clipboard ydotool wtype chromium nodejs npm rust gtk3 webkit2gtk-4.1 libayatana-appindicator"
      ;;
    fedora)
      echo "sudo dnf install -y python3 python3-pip python3-aiohttp python3-evdev openssl pipewire-pulseaudio wl-clipboard ydotool wtype chromium nodejs npm cargo gtk3 webkit2gtk4.1-devel libappindicator-gtk3-devel"
      ;;
    debian)
      echo "sudo apt update && sudo apt install -y python3 python3-pip python3-venv python3-aiohttp python3-evdev openssl pipewire-pulse wl-clipboard ydotool wtype chromium-browser nodejs npm cargo libgtk-3-dev libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev"
      ;;
    *)
      echo ""
      ;;
  esac
}

cmd="$(system_install_command)"
if [ "$install_system" -eq 1 ]; then
  if [ -z "$cmd" ]; then
    echo "unknown distro; install Python 3, openssl, PipeWire/Pulse, wl-clipboard, ydotool or wtype, Chromium, Node.js, Rust, GTK/WebKitGTK manually" >&2
  else
    echo "+ $cmd"
    eval "$cmd"
  fi
else
  echo "system packages not changed"
  if [ -n "$cmd" ]; then
    echo "to install OS deps:"
    echo "  $cmd"
  fi
fi

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
if [ "$with_batch" -eq 1 ]; then
  .venv/bin/python -m pip install -r requirements-batch.txt
fi

if [ "$with_gui" -eq 1 ]; then
  if command -v npm >/dev/null 2>&1; then
    npm install
  else
    echo "npm not found; GUI deps skipped"
  fi
fi

.venv/bin/python gdictate.py --capabilities
