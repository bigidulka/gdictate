#!/bin/bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}!${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; }

echo "╔═══════════════════════════════════════╗"
echo "║  gdictate — installer                 ║"
echo "╚═══════════════════════════════════════╝"
echo

# --- Detect distro ---
install_arch() {
    echo "Installing system packages (pacman)..."
    sudo pacman -S --needed --noconfirm \
        ydotool wl-clipboard python-pyqt6 python-dbus python-aiohttp qt6-base openssl
    ok "System packages installed"
    echo
    echo "Installing Python packages (pip)..."
    pip install --user --break-system-packages qasync evdev 2>/dev/null || \
        pip install --user qasync evdev
    ok "Python packages installed"
}

install_fedora() {
    echo "Installing system packages (dnf)..."
    sudo dnf install -y \
        ydotool wl-clipboard python3-pyqt6 python3-dbus python3-aiohttp \
        openssl kernel-headers python3-devel gcc
    ok "System packages installed"
    echo
    echo "Installing Python packages (pip)..."
    pip install --user qasync evdev
    ok "Python packages installed"
}

install_debian() {
    echo "Installing system packages (apt)..."
    sudo apt update
    sudo apt install -y \
        ydotool wl-clipboard python3-pyqt6 python3-dbus python3-aiohttp \
        openssl linux-headers-generic python3-dev gcc
    ok "System packages installed"
    echo
    echo "Installing Python packages (pip)..."
    pip install --user --break-system-packages qasync evdev 2>/dev/null || \
        pip install --user qasync evdev
    ok "Python packages installed"
}

if [ -f /etc/arch-release ]; then
    ok "Arch Linux detected"
    install_arch
elif [ -f /etc/fedora-release ]; then
    ok "Fedora detected"
    install_fedora
elif [ -f /etc/debian_version ]; then
    ok "Debian/Ubuntu detected"
    install_debian
else
    warn "Unknown distro. Install manually: ydotool wl-clipboard python3 + pip install -r requirements.txt"
    echo "    pip install --user -r requirements.txt"
    echo
fi

# --- Chrome/Chromium ---
echo
CHROME=""
for bin in google-chrome-stable google-chrome chromium chromium-browser; do
    if command -v "$bin" &>/dev/null; then
        CHROME="$bin"
        break
    fi
done

if [ -n "$CHROME" ]; then
    ok "Browser found: $CHROME"
else
    warn "Chrome/Chromium not found. Install one:"
    if [ -f /etc/arch-release ]; then
        echo "    sudo pacman -S chromium"
    elif [ -f /etc/fedora-release ]; then
        echo "    sudo dnf install chromium"
    elif [ -f /etc/debian_version ]; then
        echo "    sudo apt install chromium-browser"
    fi
fi

# --- ydotoold service ---
echo
if systemctl --user is-active ydotoold.service &>/dev/null; then
    ok "ydotoold service running"
else
    echo "Starting ydotoold..."
    systemctl --user enable --now ydotoold.service 2>/dev/null || true
    if systemctl --user is-active ydotoold.service &>/dev/null; then
        ok "ydotoold started"
    else
        warn "Could not start ydotoold. Start manually:"
        echo "    systemctl --user enable --now ydotoold.service"
    fi
fi

# --- Done ---
echo
echo "═══════════════════════════════════════"
ok "Installation complete!"
echo
echo "Run:"
echo "    python gdictate.py"
echo
echo "(first launch auto-configures browser permissions)"
echo
