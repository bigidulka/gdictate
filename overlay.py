from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


class OverlayPopup:
    def __init__(self, click_through: bool = True, show_interim: bool = True, position: str = "lower-center"):
        self.click_through = click_through
        self.show_interim_enabled = show_interim
        self.position = position
        self._proc: subprocess.Popen[str] | None = None
        self._send_lock = threading.Lock()
        self._idle_timer: threading.Timer | None = None

    def _ensure_child(self) -> None:
        with self._send_lock:
            if self._proc and self._proc.poll() is None:
                return
            env = os.environ.copy()
            if env.get("XDG_SESSION_TYPE") == "wayland" and env.get("WAYLAND_DISPLAY"):
                env["QT_QPA_PLATFORM"] = "wayland"
            else:
                env.setdefault("QT_QPA_PLATFORM", "xcb")
            self._proc = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--popup-child",
                    json.dumps(
                        {
                            "click_through": self.click_through,
                            "show_interim": self.show_interim_enabled,
                            "position": self.position,
                        },
                        ensure_ascii=False,
                    ),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                env=env,
            )

    def _send(self, payload: dict[str, Any]) -> None:
        if self._idle_timer:
            self._idle_timer.cancel()
            self._idle_timer = None
        self._ensure_child()
        if not self._proc or not self._proc.stdin:
            return
        try:
            with self._send_lock:
                self._proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                self._proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self._proc = None

    def show_recording_start(self, label: str = "", source_name: str | None = None) -> None:
        self._send({"type": "start", "label": label})

    def show_interim(self, text: str) -> None:
        if self.show_interim_enabled:
            self._send({"type": "text", "text": text})

    def show_level(self, level: float) -> None:
        self._send({"type": "level", "level": level})

    def show_transcribing(self) -> None:
        self._send({"type": "transcribing"})

    def show_error(self, text: str) -> None:
        self._send({"type": "error", "text": text})

    def show_final(self, _text: str) -> None:
        self.hide_popup()

    def hide_popup(self) -> None:
        self._send({"type": "hide"})
        self._idle_timer = threading.Timer(8.0, self._stop_idle_child)
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def _stop_idle_child(self) -> None:
        with self._send_lock:
            proc = self._proc
            self._proc = None
            if not proc:
                return
            try:
                if proc.stdin:
                    proc.stdin.write(json.dumps({"type": "quit"}) + "\n")
                    proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.terminate()

    def close(self) -> None:
        if self._idle_timer:
            self._idle_timer.cancel()
            self._idle_timer = None
        if not self._proc:
            return
        self._send({"type": "quit"})
        try:
            self._proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self._proc.terminate()
        self._proc = None

def _run_child(raw_config: str) -> int:
    from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal
    from PyQt6.QtGui import QColor, QPainter, QPen
    from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

    config = json.loads(raw_config)

    class Bridge(QObject):
        message = pyqtSignal(dict)

    class Wave(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.setFixedSize(112, 22)
            self.levels = [0.0] * 13
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.tick)

        def start_animation(self) -> None:
            if not self.timer.isActive():
                self.timer.start(45)

        def stop_animation(self) -> None:
            self.timer.stop()

        def tick(self) -> None:
            self.levels = [level * 0.92 for level in self.levels]
            self.update()

        def set_level(self, level: float) -> None:
            level = max(0.0, min(1.0, level))
            self.levels = [*self.levels[1:], level]
            self.update()

        def paintEvent(self, _event) -> None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor(255, 255, 255, 220), 4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            center = self.height() / 2
            x = 3
            for level in self.levels:
                h = 5 + level * (self.height() - 7)
                painter.drawLine(int(x), int(center - h / 2), int(x), int(center + h / 2))
                x += 9

    class Popup(QWidget):
        def __init__(self) -> None:
            flags = (
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
                | Qt.WindowType.WindowDoesNotAcceptFocus
            )
            if config.get("click_through", True):
                flags |= Qt.WindowType.WindowTransparentForInput
            super().__init__(None, flags)
            self.started = time.monotonic()
            self.visible_text = ""
            self.active = False
            self.transcribing = False

            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, bool(config.get("click_through", True)))
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

            outer = QVBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)

            card = QFrame(self)
            card.setObjectName("card")
            card.setStyleSheet(
                """
                QFrame#card {
                    background: rgba(24, 24, 27, 222);
                    border: 1px solid rgba(255, 255, 255, 32);
                    border-radius: 16px;
                }
                QLabel {
                    color: rgba(255, 255, 255, 235);
                    background: transparent;
                }
                """
            )
            outer.addWidget(card)

            layout = QVBoxLayout(card)
            layout.setContentsMargins(12, 9, 12, 10)
            layout.setSpacing(6)

            top = QHBoxLayout()
            top.setSpacing(9)
            self.dot = QLabel()
            self.dot.setFixedSize(10, 10)
            self.dot.setStyleSheet("background: #ff3347; border-radius: 5px;")
            top.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
            self.wave = Wave()
            top.addWidget(self.wave, 0, Qt.AlignmentFlag.AlignVCenter)
            self.clock = QLabel("00:00")
            self.clock.setStyleSheet("font: 700 12px monospace; color: rgba(255,255,255,180);")
            top.addWidget(self.clock, 0, Qt.AlignmentFlag.AlignVCenter)
            self.status = QLabel("REC")
            self.status.setStyleSheet("font: 700 11px monospace; color: rgba(255,255,255,180);")
            top.addWidget(self.status, 0, Qt.AlignmentFlag.AlignVCenter)
            top.addStretch(1)
            layout.addLayout(top)

            self.text = QLabel("")
            self.text.setWordWrap(True)
            self.text.setMaximumWidth(410)
            self.text.setStyleSheet("font: 500 13px sans-serif;")
            self.text.hide()
            layout.addWidget(self.text)

            self.timer = QTimer(self)
            self.timer.timeout.connect(self.update_clock)

        def handle(self, payload: dict) -> None:
            kind = payload.get("type")
            if kind == "start":
                self.active = True
                self.transcribing = False
                self.started = time.monotonic()
                self.visible_text = ""
                self.text.setText(self.visible_text)
                self.text.hide()
                self.dot.setStyleSheet("background: #ff3347; border-radius: 5px;")
                self.status.setText("REC")
                self.wave.show()
                self.wave.start_animation()
                self.clock.show()
                self.timer.start(200)
                self.update_clock()
                self.adjustSize()
                self.reposition()
                self.show()
            elif kind == "text":
                if not self.active:
                    return
                self.visible_text = payload.get("text") or ""
                self.text.setText(self.visible_text)
                self.text.setVisible(bool(self.visible_text))
                self.adjustSize()
                self.reposition()
            elif kind == "level":
                if not self.active or self.transcribing:
                    return
                self.wave.set_level(float(payload.get("level") or 0.0))
            elif kind == "transcribing":
                if not self.active:
                    return
                self.transcribing = True
                self.dot.setStyleSheet("background: #58a6ff; border-radius: 5px;")
                self.status.setText("STT…")
                self.wave.hide()
                self.wave.stop_animation()
                self.clock.hide()
                self.timer.stop()
                self.text.setText("Обработка записи…")
                self.text.show()
                self.adjustSize()
                self.reposition()
            elif kind == "error":
                self.active = True
                self.transcribing = False
                self.dot.setStyleSheet("background: #ffb020; border-radius: 5px;")
                self.status.setText("ERROR")
                self.wave.hide()
                self.wave.stop_animation()
                self.clock.hide()
                self.timer.stop()
                self.text.setText(str(payload.get("text") or "Ошибка диктовки"))
                self.text.show()
                self.adjustSize()
                self.reposition()
                self.show()
            elif kind == "hide":
                self.transcribing = False
                self.active = False
                self.visible_text = ""
                self.text.clear()
                self.text.hide()
                self.wave.stop_animation()
                self.timer.stop()
                self.hide()
            elif kind == "quit":
                QApplication.quit()

        def update_clock(self) -> None:
            elapsed = int(time.monotonic() - self.started)
            self.clock.setText(f"{elapsed // 60:02d}:{elapsed % 60:02d}")

        def reposition(self) -> None:
            screen = QApplication.primaryScreen()
            if not screen:
                return
            area = screen.availableGeometry()
            margin_bottom = 150
            pos = config.get("position", "lower-center")
            if pos == "top-center":
                x = area.x() + (area.width() - self.width()) // 2
                y = area.y() + 90
            elif pos == "bottom-right":
                x = area.x() + area.width() - self.width() - 32
                y = area.y() + area.height() - self.height() - margin_bottom
            else:
                x = area.x() + (area.width() - self.width()) // 2
                y = area.y() + area.height() - self.height() - margin_bottom
            self.move(x, y)

    app = QApplication(sys.argv[:1])
    bridge = Bridge()
    popup = Popup()
    bridge.message.connect(popup.handle)

    def reader() -> None:
        for line in sys.stdin:
            try:
                bridge.message.emit(json.loads(line))
            except json.JSONDecodeError:
                continue
        bridge.message.emit({"type": "quit"})

    threading.Thread(target=reader, daemon=True).start()
    return app.exec()


if __name__ == "__main__" and "--popup-child" in sys.argv:
    idx = sys.argv.index("--popup-child")
    raise SystemExit(_run_child(sys.argv[idx + 1]))
