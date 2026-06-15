from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
from array import array
from pathlib import Path
from typing import Any


class OverlayPopup:
    def __init__(self, click_through: bool = True, show_interim: bool = True, position: str = "lower-center"):
        self.click_through = click_through
        self.show_interim_enabled = show_interim
        self.position = position
        self._proc: subprocess.Popen[str] | None = None
        self._send_lock = threading.Lock()
        self._level_proc: subprocess.Popen[bytes] | None = None
        self._level_stop = threading.Event()

    def _ensure_child(self) -> None:
        if self._proc and self._proc.poll() is None:
            return
        env = os.environ.copy()
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
        self._start_level_monitor(source_name)
        self._send({"type": "start", "label": label})

    def show_interim(self, text: str) -> None:
        if self.show_interim_enabled:
            self._send({"type": "text", "text": text})

    def show_final(self, _text: str) -> None:
        self.hide_popup()

    def hide_popup(self) -> None:
        self._stop_level_monitor()
        self._send({"type": "hide"})

    def close(self) -> None:
        self._stop_level_monitor()
        if not self._proc:
            return
        self._send({"type": "quit"})
        try:
            self._proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self._proc.terminate()
        self._proc = None

    def _start_level_monitor(self, source_name: str | None) -> None:
        self._stop_level_monitor()
        if not source_name or not shutil.which("parec"):
            return
        self._level_stop.clear()
        try:
            self._level_proc = subprocess.Popen(
                [
                    "parec",
                    "--raw",
                    "--format=s16le",
                    "--rate=16000",
                    "--channels=1",
                    "--latency-msec=50",
                    "--device",
                    source_name,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            self._level_proc = None
            return
        threading.Thread(target=self._read_levels, daemon=True).start()

    def _stop_level_monitor(self) -> None:
        self._level_stop.set()
        proc = self._level_proc
        self._level_proc = None
        if proc and proc.poll() is None:
            proc.terminate()

    def _read_levels(self) -> None:
        proc = self._level_proc
        if not proc or not proc.stdout:
            return
        smooth = 0.0
        while not self._level_stop.is_set() and proc.poll() is None:
            chunk = proc.stdout.read(1600)
            if not chunk:
                break
            samples = array("h")
            samples.frombytes(chunk)
            if sys.byteorder != "little":
                samples.byteswap()
            if not samples:
                continue
            rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / 32768.0
            peak = max(abs(sample) for sample in samples) / 32768.0
            level = min(1.0, max(rms * 85.0, peak * 8.0))
            smooth = smooth * 0.55 + level * 0.45
            self._send({"type": "level", "level": smooth})


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
            self.timer.start(45)

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
            dot = QLabel()
            dot.setFixedSize(10, 10)
            dot.setStyleSheet("background: #ff3347; border-radius: 5px;")
            top.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
            self.wave = Wave()
            top.addWidget(self.wave, 0, Qt.AlignmentFlag.AlignVCenter)
            self.clock = QLabel("00:00")
            self.clock.setStyleSheet("font: 700 12px monospace; color: rgba(255,255,255,180);")
            top.addWidget(self.clock, 0, Qt.AlignmentFlag.AlignVCenter)
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
            self.timer.start(200)

        def handle(self, payload: dict) -> None:
            kind = payload.get("type")
            if kind == "start":
                self.active = True
                self.started = time.monotonic()
                self.visible_text = ""
                self.text.setText(self.visible_text)
                self.text.hide()
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
                if not self.active:
                    return
                self.wave.set_level(float(payload.get("level") or 0.0))
            elif kind == "hide":
                self.active = False
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
