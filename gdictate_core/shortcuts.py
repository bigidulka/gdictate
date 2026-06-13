from __future__ import annotations

import os
import shlex
import sys
from dataclasses import dataclass

from .constants import PROJECT_DIR
from .platforms import current_desktop
from .settings import AppSettings


@dataclass
class ShortcutCommand:
    id: str
    label: str
    suggested_key: str
    mode: str
    command: str
    description: str


@dataclass
class ShortcutReport:
    desktop: str
    recommended_backend: str
    hold_available: bool
    notes: list[str]
    commands: list[ShortcutCommand]


def _python_command() -> list[str]:
    script = PROJECT_DIR / "gdictate.py"
    if os.name == "nt":
        venv_python = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
        python = str(venv_python) if venv_python.exists() else "python"
    else:
        venv_python = PROJECT_DIR / ".venv" / "bin" / "python"
        python = str(venv_python) if venv_python.exists() else sys.executable
    return [python, str(script)]


def _shell(args: list[str]) -> str:
    if os.name == "nt":
        return " ".join(f'"{arg}"' if " " in arg or "\\" in arg else arg for arg in args)
    return " ".join(shlex.quote(arg) for arg in args)


def shortcut_report(settings: AppSettings) -> ShortcutReport:
    desktop = current_desktop()
    desktop_lower = desktop.lower()
    base = _python_command()
    notes: list[str] = []
    recommended_backend = settings.bind.linux_backend
    hold_available = True

    if os.name == "nt":
        recommended_backend = "tauri-global-shortcuts"
        hold_available = True
        notes.append("Windows speaker channel needs Stereo Mix/VB-CABLE/Virtual Audio Cable as default browser input.")
        notes.append("Tauri GUI registers native shortcuts while the app is running; AutoHotkey is an optional external fallback.")
    elif "gnome" in desktop_lower:
        recommended_backend = "gnome-custom-shortcuts-toggle"
        hold_available = False
        notes.append("GNOME Settings custom shortcuts launch commands on key press only; release/hold is not exposed.")
        notes.append("Use the Tauri GUI native hotkeys, toggle commands in GNOME, or evdev mode for real hold-to-talk.")
    elif "kde" in desktop_lower or "plasma" in desktop_lower:
        recommended_backend = "kde-custom-shortcuts-or-evdev"
        notes.append("KDE custom shortcuts can run daemon control commands; if release binding is unavailable, use toggle.")
    else:
        recommended_backend = "de-shortcuts-or-evdev"
        notes.append("Desktop shortcut release support varies; use toggle commands when only press actions exist.")
        notes.append("Use evdev backend for real hold-to-talk on Linux.")

    commands = [
        ShortcutCommand(
            id="daemon",
            label="Start daemon",
            suggested_key="autostart",
            mode="background",
            command=_shell([*base, "--daemon", "--no-ui"]),
            description="Run once at login before shortcut commands.",
        ),
        ShortcutCommand(
            id="toggle_mic",
            label="Toggle mic",
            suggested_key=settings.bind.mic_hold,
            mode="toggle",
            command=_shell([*base, "--toggle", "mic"]),
            description="Press once to start/stop microphone channel. Best for GNOME custom shortcuts.",
        ),
        ShortcutCommand(
            id="toggle_speakers",
            label="Toggle speakers",
            suggested_key=settings.bind.speakers_hold,
            mode="toggle",
            command=_shell([*base, "--toggle", "speakers"]),
            description="Press once to start/stop speaker channel. Best for GNOME custom shortcuts.",
        ),
        ShortcutCommand(
            id="start_mic",
            label="Start mic",
            suggested_key=settings.bind.mic_hold,
            mode="press",
            command=_shell([*base, "--start", "mic"]),
            description="Use for press action when the DE can bind a separate release command.",
        ),
        ShortcutCommand(
            id="start_speakers",
            label="Start speakers",
            suggested_key=settings.bind.speakers_hold,
            mode="press",
            command=_shell([*base, "--start", "speakers"]),
            description="Use for press action when the DE can bind a separate release command.",
        ),
        ShortcutCommand(
            id="stop",
            label="Stop recording",
            suggested_key="release",
            mode="release",
            command=_shell([*base, "--stop"]),
            description="Use for release action, or bind as a separate stop hotkey.",
        ),
        ShortcutCommand(
            id="status",
            label="Daemon status",
            suggested_key="manual",
            mode="debug",
            command=_shell([*base, "--status"]),
            description="Debug daemon state from terminal or launcher.",
        ),
    ]

    return ShortcutReport(
        desktop=desktop,
        recommended_backend=recommended_backend,
        hold_available=hold_available,
        notes=notes,
        commands=commands,
    )
