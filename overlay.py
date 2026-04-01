"""Overlay popup + system tray icon for gdictate.

Uses KDE Plasma OSD when available, falls back to notify-send."""

import shutil
import subprocess

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

# Try KDE OSD
try:
    import dbus

    _bus = dbus.SessionBus()
    _osd = dbus.Interface(
        _bus.get_object("org.kde.plasmashell", "/org/kde/osdService"),
        "org.kde.osdService",
    )
    _HAS_KDE_OSD = True
except Exception:
    _HAS_KDE_OSD = False

_HAS_NOTIFY = shutil.which("notify-send") is not None


class OverlayPopup:
    """Shows dictation text via KDE OSD or notify-send fallback.

    notify-send uses x-canonical-private-synchronous hint to replace
    the previous notification instead of stacking them."""

    def _notify(self, text: str, icon: str = "audio-input-microphone", timeout: int = 3000):
        if not _HAS_NOTIFY:
            return
        subprocess.Popen(
            [
                "notify-send", "-t", str(timeout),
                "-h", "string:x-canonical-private-synchronous:gdictate",
                "-i", icon, "gdictate", text,
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def show_interim(self, text: str):
        if _HAS_KDE_OSD:
            _osd.showText("audio-input-microphone", text)
        else:
            self._notify(text)

    def show_final(self, text: str):
        if _HAS_KDE_OSD:
            _osd.showText("dialog-ok", text)
        else:
            self._notify(text, icon="dialog-ok", timeout=2000)

    def hide_popup(self):
        pass  # auto-hides


class DictationTray(QSystemTrayIcon):
    """System tray icon with state-colored indicator. Left-click toggles dictation."""

    toggle_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._icons = {
            "idle": self._make_icon(QColor(128, 128, 128)),
            "recording": self._make_icon(QColor(220, 40, 40)),
            "finalizing": self._make_icon(QColor(220, 160, 40)),
        }
        self.setIcon(self._icons["idle"])
        self.setToolTip("gdictate — idle")

        self.activated.connect(self._on_activated)

        menu = QMenu()
        menu.addAction("Quit", QApplication.quit)
        self.setContextMenu(menu)
        self.show()

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:  # left click
            self.toggle_requested.emit()

    @staticmethod
    def _make_icon(color: QColor) -> QIcon:
        px = QPixmap(32, 32)
        px.fill(QColor(0, 0, 0, 0))
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(2, 2, 28, 28)
        p.end()
        return QIcon(px)

    def set_state(self, state_value: str):
        self.setIcon(self._icons.get(state_value, self._icons["idle"]))
        self.setToolTip(f"gdictate — {state_value}")
