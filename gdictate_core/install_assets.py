from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .constants import PROJECT_DIR


@dataclass
class UserInstallAsset:
    id: str
    label: str
    path: str
    kind: str
    exists: bool
    executable: bool
    content: str


@dataclass
class UserInstallPlan:
    os: str
    installable: bool
    assets: list[UserInstallAsset]
    warnings: list[str]
    actions: list[str]


@dataclass
class UserInstallResult:
    ok: bool
    installed: list[str]
    warnings: list[str]
    actions: list[str]


def user_install_plan(home: Optional[Path] = None) -> UserInstallPlan:
    home = home or Path.home()
    if os.name == "nt":
        return _windows_plan(home)
    return _linux_plan(home)


def install_user_assets(home: Optional[Path] = None) -> UserInstallResult:
    plan = user_install_plan(home)
    installed: list[str] = []
    warnings = list(plan.warnings)
    if not plan.installable:
        return UserInstallResult(False, installed, warnings, plan.actions)

    for asset in plan.assets:
        path = Path(asset.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(asset.content, encoding="utf-8")
        if asset.executable:
            path.chmod(path.stat().st_mode | 0o755)
        installed.append(str(path))

    return UserInstallResult(True, installed, warnings, plan.actions)


def _linux_plan(home: Path) -> UserInstallPlan:
    python = _python_command()
    gui_exec = _gui_command()
    service_path = home / ".config" / "systemd" / "user" / "gdictate-daemon.service"
    hotkeys_service_path = home / ".config" / "systemd" / "user" / "gdictate-hotkeys.service"
    desktop_path = home / ".local" / "share" / "applications" / "gdictate.desktop"
    autostart_path = home / ".config" / "autostart" / "gdictate.desktop"
    icon_path = PROJECT_DIR / "src-tauri" / "icons" / "icon.png"

    desktop_content = _desktop_entry(gui_exec, icon_path)
    assets = [
        UserInstallAsset(
            id="systemd_user_service",
            label="gdictate daemon user service",
            path=str(service_path),
            kind="systemd-user-service",
            exists=service_path.exists(),
            executable=False,
            content=(
                "[Unit]\n"
                "Description=gdictate speech daemon\n"
                "After=graphical-session.target pipewire.service pipewire-pulse.service\n\n"
                "[Service]\n"
                f"WorkingDirectory={PROJECT_DIR}\n"
                f"ExecStart={python} {PROJECT_DIR / 'gdictate.py'} --daemon --no-ui\n"
                "Restart=on-failure\n"
                "RestartSec=2\n\n"
                "[Install]\n"
                "WantedBy=default.target\n"
            ),
        ),
        UserInstallAsset(
            id="systemd_hotkeys_service",
            label="gdictate hotkeys user service",
            path=str(hotkeys_service_path),
            kind="systemd-user-service",
            exists=hotkeys_service_path.exists(),
            executable=False,
            content=(
                "[Unit]\n"
                "Description=gdictate evdev hold hotkeys\n"
                "After=graphical-session.target gdictate-daemon.service\n"
                "Wants=gdictate-daemon.service\n\n"
                "[Service]\n"
                f"WorkingDirectory={PROJECT_DIR}\n"
                f"ExecStart={python} {PROJECT_DIR / 'gdictate.py'} --daemon-hotkeys\n"
                "Restart=on-failure\n"
                "RestartSec=2\n\n"
                "[Install]\n"
                "WantedBy=default.target\n"
            ),
        ),
        UserInstallAsset(
            id="desktop_entry",
            label="gdictate desktop launcher",
            path=str(desktop_path),
            kind="desktop-entry",
            exists=desktop_path.exists(),
            executable=False,
            content=desktop_content,
        ),
        UserInstallAsset(
            id="autostart_entry",
            label="gdictate GUI autostart",
            path=str(autostart_path),
            kind="desktop-autostart",
            exists=autostart_path.exists(),
            executable=False,
            content=desktop_content,
        ),
    ]
    actions = [
        "systemctl --user daemon-reload",
        "systemctl --user enable --now gdictate-daemon.service gdictate-hotkeys.service",
        "update-desktop-database ~/.local/share/applications  # optional",
    ]
    warnings: list[str] = []
    if not shutil.which("systemctl"):
        warnings.append("systemctl not found; daemon service file can be written but not enabled automatically.")
    if "tauri dev" in gui_exec:
        warnings.append("Release GUI binary not found; desktop entry falls back to npm run tauri:dev.")
    return UserInstallPlan(f"{sys.platform}", True, assets, warnings, actions)


def _windows_plan(home: Path) -> UserInstallPlan:
    startup = home / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    cmd_path = startup / "gdictate-daemon.cmd"
    python = _python_command()
    assets = [
        UserInstallAsset(
            id="startup_daemon_cmd",
            label="gdictate daemon startup command",
            path=str(cmd_path),
            kind="windows-startup-cmd",
            exists=cmd_path.exists(),
            executable=False,
            content=f'@echo off\r\ncd /d "{PROJECT_DIR}"\r\n"{python}" "{PROJECT_DIR / "gdictate.py"}" --daemon --no-ui\r\n',
        )
    ]
    return UserInstallPlan(
        f"Windows {sys.getwindowsversion().major}" if hasattr(sys, "getwindowsversion") else "Windows",
        True,
        assets,
        ["GUI startup shortcut is installer-owned; this source-tree asset starts the Python daemon only."],
        ["Place a gdictate app shortcut in Startup after installing the native package."],
    )


def _python_command() -> str:
    if os.name == "nt":
        venv_python = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
        return str(venv_python if venv_python.exists() else "python")
    venv_python = PROJECT_DIR / ".venv" / "bin" / "python"
    return str(venv_python if venv_python.exists() else sys.executable)


def _gui_command() -> str:
    if os.name == "nt":
        release = PROJECT_DIR / "src-tauri" / "target" / "release" / "gdictate-app.exe"
        if release.exists():
            return str(release)
        return "npm run tauri:dev"
    release = PROJECT_DIR / "src-tauri" / "target" / "release" / "gdictate-app"
    if release.exists():
        return str(release)
    return f"sh -lc 'cd {PROJECT_DIR} && npm run tauri:dev'"


def _desktop_entry(command: str, icon_path: Path) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=gdictate\n"
        "Comment=Streaming dictation and live transcription\n"
        f"Exec={command}\n"
        f"Icon={icon_path}\n"
        "Terminal=false\n"
        "Categories=Utility;AudioVideo;Accessibility;\n"
        "StartupNotify=false\n"
    )
