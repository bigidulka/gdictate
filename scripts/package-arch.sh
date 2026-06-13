#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(node -e "console.log(require('./package.json').version.replace(/-/g, '_'))")"
PKGREL="${PKGREL:-1}"
ARCH="${ARCH:-x86_64}"
PKGVER="${VERSION}-${PKGREL}"
OUT_DIR="$ROOT/src-tauri/target/release/bundle/pacman"
WORK_DIR="$ROOT/tmp/arch-package"
DEB_DATA="$ROOT/src-tauri/target/release/bundle/deb/gdictate_$(node -e "console.log(require('./package.json').version)")_amd64/data.tar.gz"

if [[ ! -f "$DEB_DATA" ]]; then
  echo "missing deb payload: $DEB_DATA" >&2
  echo "run: npx tauri build --bundles deb" >&2
  exit 2
fi

command -v bsdtar >/dev/null
command -v zstd >/dev/null

rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR/pkg" "$OUT_DIR"
bsdtar -xzf "$DEB_DATA" -C "$WORK_DIR/pkg"

SIZE="$(du -sk "$WORK_DIR/pkg" | awk '{print $1 * 1024}')"
BUILDDATE="${SOURCE_DATE_EPOCH:-$(date +%s)}"

cat > "$WORK_DIR/pkg/.PKGINFO" <<EOF
pkgname = gdictate
pkgbase = gdictate
pkgver = $PKGVER
pkgdesc = Streaming dictation mini app using Chrome Web Speech
url = https://github.com/bigidulka/gdictate
builddate = $BUILDDATE
packager = gdictate package-arch.sh
size = $SIZE
arch = $ARCH
license = MIT
depend = gtk3
depend = libayatana-appindicator
depend = webkit2gtk-4.1
depend = openssl
depend = python
depend = python-aiohttp
depend = python-evdev
optdepend = chromium: Chrome-compatible Web Speech engine
optdepend = google-chrome: Chrome Web Speech engine
optdepend = wl-clipboard: Wayland clipboard copy backend
optdepend = ydotool: Wayland paste key injection
optdepend = wtype: Wayland paste key injection fallback
optdepend = pipewire-pulse: speaker capture routing through pactl
optdepend = python-pyqt6: legacy tray/overlay CLI UI
EOF

(
  cd "$WORK_DIR/pkg"
  bsdtar --format=mtree --options='!all,use-set,type,uid,gid,mode,time,size,md5,sha256,link' \
    --uid 0 --gid 0 --uname root --gname root \
    -cf .MTREE .PKGINFO usr
)

PACKAGE="$OUT_DIR/gdictate-${PKGVER}-${ARCH}.pkg.tar.zst"
rm -f "$PACKAGE"
(
  cd "$WORK_DIR/pkg"
  bsdtar --uid 0 --gid 0 --uname root --gname root -cf - .PKGINFO .MTREE usr | zstd -T0 -19 -q -o "$PACKAGE"
)

echo "$PACKAGE"
