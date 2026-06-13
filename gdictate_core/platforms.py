from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

from .constants import LINUX_CHROME_PATHS, PROJECT_DIR, WINDOWS_CHROME_PATHS
from .install_assets import install_user_assets


@dataclass
class CapabilityReport:
    os: str
    desktop: str
    chrome: bool
    microphone_routing: str
    speaker_routing: str
    global_hotkeys: str
    paste: str
    overlay: str
    warnings: list[str]


@dataclass
class AudioDevice:
    name: str
    kind: str
    state: str = ""
    default: bool = False
    virtual: bool = False


@dataclass
class SystemAction:
    id: str
    label: str
    status: str
    command: str = ""
    description: str = ""
    requires_admin: bool = False
    manual: bool = False


@dataclass
class SystemActionResult:
    ok: bool
    action_id: str
    status: str
    message: str
    command: str = ""


@dataclass
class LiveBackendReport:
    os: str
    desktop: str
    backend: str
    supports_click_through: bool
    supports_interim: bool
    positions: list[str]
    warnings: list[str]
    actions: list[str]


@dataclass
class DiagnosticsReport:
    os: str
    desktop: str
    chrome_path: Optional[str]
    paste_backend: str
    hotkey_backend: str
    microphone_devices: list[AudioDevice]
    speaker_devices: list[AudioDevice]
    speaker_capture_ready: bool
    warnings: list[str]
    actions: list[str]
    system_actions: list[SystemAction]


def current_desktop() -> str:
    if os.name == "nt":
        return "Windows"
    return os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION") or "unknown"


def chrome_candidates(channel: str = "auto") -> list[str]:
    channel = (channel or "auto").lower()
    if os.name == "nt":
        if channel == "edge":
            return [path for path in WINDOWS_CHROME_PATHS if "Edge" in path]
        return WINDOWS_CHROME_PATHS

    channel_paths = {
        "stable": ["/usr/bin/google-chrome-stable", "/usr/bin/google-chrome"],
        "beta": ["/usr/bin/google-chrome-beta"],
        "dev": ["/usr/bin/google-chrome-unstable", "/usr/bin/google-chrome-dev"],
        "chromium": ["/usr/bin/chromium", "/usr/bin/chromium-browser", "/snap/bin/chromium"],
        "edge": ["/usr/bin/microsoft-edge", "/usr/bin/microsoft-edge-stable"],
    }
    if channel in channel_paths:
        return channel_paths[channel]
    return LINUX_CHROME_PATHS + ["/usr/bin/google-chrome-beta", "/usr/bin/google-chrome-unstable", "/usr/bin/microsoft-edge"]


def find_chrome(channel: str = "auto") -> str:
    channel = (channel or "auto").lower()
    for path in chrome_candidates(channel):
        if os.path.isfile(path):
            return path
    command_candidates = {
        "stable": ("google-chrome-stable", "google-chrome", "chrome"),
        "beta": ("google-chrome-beta",),
        "dev": ("google-chrome-unstable", "google-chrome-dev"),
        "chromium": ("chromium", "chromium-browser"),
        "edge": ("microsoft-edge", "microsoft-edge-stable", "msedge"),
    }
    names = command_candidates.get(
        channel,
        ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser", "microsoft-edge", "microsoft-edge-stable", "msedge", "chrome"),
    )
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    raise FileNotFoundError(f"Chrome/Chromium/Edge not found for channel '{channel}'")


def pkg_hint(name: str) -> str:
    if os.name == "nt":
        return f"install {name}"
    if os.path.isfile("/etc/arch-release"):
        return f"sudo pacman -S {name}"
    if os.path.isfile("/etc/fedora-release"):
        return f"sudo dnf install {name}"
    if os.path.isfile("/etc/debian_version"):
        return f"sudo apt install {name}"
    return f"install '{name}' via your package manager"


def _action(
    action_id: str,
    label: str,
    status: str,
    command: str = "",
    description: str = "",
    requires_admin: bool = False,
    manual: bool = False,
) -> SystemAction:
    return SystemAction(
        id=action_id,
        label=label,
        status=status,
        command=command,
        description=description,
        requires_admin=requires_admin,
        manual=manual,
    )


def _group_ready(name: str) -> bool:
    if os.name == "nt":
        return False
    import grp

    try:
        group = grp.getgrnam(name)
    except KeyError:
        return False
    return os.getgid() == group.gr_gid or group.gr_gid in os.getgroups()


def _user_unit_state(*names: str) -> tuple[str, str]:
    if os.name == "nt" or not shutil.which("systemctl"):
        return "", "missing"
    for name in names:
        listed = subprocess.run(
            ["systemctl", "--user", "list-unit-files", name],
            capture_output=True,
            text=True,
        )
        if name not in listed.stdout:
            continue
        active = subprocess.run(["systemctl", "--user", "is-active", name], capture_output=True, text=True)
        return name, active.stdout.strip() or "inactive"
    return "", "missing"


def _user_unit_enabled(name: str) -> str:
    if os.name == "nt" or not shutil.which("systemctl"):
        return "missing"
    result = subprocess.run(["systemctl", "--user", "is-enabled", name], capture_output=True, text=True)
    return result.stdout.strip() or "disabled"


def _project_python() -> str:
    if os.name == "nt":
        candidate = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
        return str(candidate if candidate.exists() else sys.executable)
    candidate = PROJECT_DIR / ".venv" / "bin" / "python"
    return str(candidate if candidate.exists() else sys.executable)


def ensure_ydotool_service() -> None:
    if os.name == "nt" or not shutil.which("systemctl"):
        return
    for name in ("ydotool.service", "ydotoold.service"):
        result = subprocess.run(
            ["systemctl", "--user", "list-unit-files", name],
            capture_output=True,
            text=True,
        )
        if name not in result.stdout:
            continue
        active = subprocess.run(
            ["systemctl", "--user", "is-active", name],
            capture_output=True,
            text=True,
        )
        if active.stdout.strip() != "active":
            print(f"[INIT] Starting {name}...", flush=True)
            subprocess.run(["systemctl", "--user", "enable", "--now", name], capture_output=True)
        return


def check_dependencies(paste_mode: str = "auto") -> None:
    missing = []
    try:
        find_chrome()
    except FileNotFoundError:
        missing.append(("Chrome/Chromium/Edge", pkg_hint("chromium")))

    if os.name != "nt" and paste_mode != "none":
        if not shutil.which("wl-copy"):
            missing.append(("wl-clipboard", pkg_hint("wl-clipboard")))
        if not shutil.which("ydotool") and not shutil.which("wtype"):
            missing.append(("ydotool or wtype", f"{pkg_hint('ydotool')}  # or {pkg_hint('wtype')}"))

    if missing:
        print("\033[1;31mMissing dependencies:\033[0m", file=sys.stderr)
        for name, cmd in missing:
            print(f"  {name}: {cmd}", file=sys.stderr)
        sys.exit(1)

    if paste_mode != "none":
        ensure_ydotool_service()


def capability_report() -> CapabilityReport:
    warnings: list[str] = []
    chrome_ok = True
    try:
        find_chrome()
    except FileNotFoundError:
        chrome_ok = False
        warnings.append("Chrome/Chromium/Edge not found")

    if os.name == "nt":
        return CapabilityReport(
            os=f"Windows {platform.release()}",
            desktop="Windows",
            chrome=chrome_ok,
            microphone_routing="default input via browser",
            speaker_routing="requires Stereo Mix/VB-CABLE/Virtual Audio Cable as browser input",
            global_hotkeys="Tauri global shortcuts + CLI fallback",
            paste="clipboard + Ctrl+V",
            overlay="native transparent always-on-top window",
            warnings=warnings,
        )

    has_pactl = shutil.which("pactl") is not None
    has_evdev = True
    try:
        import evdev  # noqa: F401
    except ImportError:
        has_evdev = False

    if not has_pactl:
        warnings.append("pactl not found; speaker routing unavailable")
    if not has_evdev:
        warnings.append("evdev package missing; kernel hotkeys unavailable")

    return CapabilityReport(
        os=f"{platform.system()} {platform.release()}",
        desktop=current_desktop(),
        chrome=chrome_ok,
        microphone_routing="PipeWire/Pulse default source",
        speaker_routing="default sink monitor remapped to Chrome input" if has_pactl else "unavailable",
        global_hotkeys="DE shortcuts/CLI + evdev fallback" if has_evdev else "DE shortcuts/CLI only",
        paste="wl-copy + ydotool/wtype",
        overlay="Tauri/Qt click-through popup; GNOME OSD requires extension",
        warnings=warnings,
    )


def live_report() -> LiveBackendReport:
    desktop = current_desktop()
    positions = ["lower-center", "top-center", "bottom-right"]
    warnings: list[str] = []
    actions: list[str] = []

    if os.name == "nt":
        return LiveBackendReport(
            os=f"Windows {platform.release()}",
            desktop=desktop,
            backend="tauri-transparent-window",
            supports_click_through=True,
            supports_interim=True,
            positions=positions,
            warnings=warnings,
            actions=actions,
        )

    desktop_lower = desktop.lower()
    backend = "tauri-transparent-window"
    if "gnome" in desktop_lower:
        warnings.append("GNOME Shell has no built-in custom OSD API; use the Tauri live popup.")
        actions.append("For compositor-native OSD, install or write a GNOME Shell extension.")
    elif "kde" in desktop_lower or "plasma" in desktop_lower:
        actions.append("KDE window rules can keep the popup borderless, above, and skipped from taskbar.")
    elif "sway" in desktop_lower or "hypr" in desktop_lower:
        warnings.append("Wayland compositor policies may restrict global focus and click-through behavior.")
        actions.append("Use compositor window rules for floating/always-on-top placement when needed.")

    return LiveBackendReport(
        os=f"{platform.system()} {platform.release()}",
        desktop=desktop,
        backend=backend,
        supports_click_through=True,
        supports_interim=True,
        positions=positions,
        warnings=warnings,
        actions=actions,
    )


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def _linux_audio_devices() -> tuple[list[AudioDevice], list[AudioDevice]]:
    if not shutil.which("pactl"):
        return [], []

    default_source = ""
    default_sink = ""
    try:
        default_source = _run(["pactl", "get-default-source"]).stdout.strip()
        default_sink = _run(["pactl", "get-default-sink"]).stdout.strip()
    except FileNotFoundError:
        return [], []

    microphones: list[AudioDevice] = []
    speakers: list[AudioDevice] = []
    sources = _run(["pactl", "list", "short", "sources"]).stdout
    for line in sources.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name = parts[1]
        if name.endswith(".monitor"):
            continue
        microphones.append(
            AudioDevice(
                name=name,
                kind="input",
                state=parts[4] if len(parts) > 4 else "",
                default=name == default_source,
                virtual=name.startswith("gdictate_") or "snd_aloop" in name,
            )
        )

    sinks = _run(["pactl", "list", "short", "sinks"]).stdout
    for line in sinks.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name = parts[1]
        speakers.append(
            AudioDevice(
                name=name,
                kind="output",
                state=parts[4] if len(parts) > 4 else "",
                default=name == default_sink,
                virtual=name.startswith("gdictate_"),
            )
        )

    return microphones, speakers


def _windows_audio_devices() -> tuple[list[AudioDevice], list[AudioDevice], bool]:
    if os.name != "nt" or not shutil.which("powershell"):
        return [], [], False

    script = r"""
$items = Get-PnpDevice -Class AudioEndpoint -Status OK | Select-Object FriendlyName,InstanceId
$items | ConvertTo-Json -Compress
"""
    result = _run(["powershell", "-NoProfile", "-Command", script])
    if result.returncode != 0 or not result.stdout.strip():
        return [], [], False

    try:
        import json

        raw = json.loads(result.stdout)
    except Exception:
        return [], [], False

    if isinstance(raw, dict):
        raw = [raw]

    microphones: list[AudioDevice] = []
    speakers: list[AudioDevice] = []
    virtual_ready = False
    virtual_terms = ("stereo mix", "vb-cable", "virtual audio cable", "cable output", "voicemeeter")
    for item in raw:
        name = str(item.get("FriendlyName", ""))
        instance = str(item.get("InstanceId", "")).lower()
        lower = name.lower()
        is_virtual = any(term in lower for term in virtual_terms)
        if is_virtual:
            virtual_ready = True
        device = AudioDevice(name=name, kind="endpoint", state="OK", virtual=is_virtual)
        if any(term in lower for term in ("microphone", "mic", "input", "cable output", "stereo mix")) or "capture" in instance:
            microphones.append(device)
        else:
            speakers.append(device)

    return microphones, speakers, virtual_ready


def diagnostics_report() -> DiagnosticsReport:
    warnings: list[str] = []
    actions: list[str] = []
    system_actions: list[SystemAction] = []
    try:
        chrome_path: Optional[str] = find_chrome()
    except FileNotFoundError:
        chrome_path = None
        warnings.append("Chrome/Chromium/Edge not found.")
        actions.append(pkg_hint("chromium"))
        system_actions.append(
            _action(
                "install_chromium",
                "Install Chrome/Chromium",
                "missing",
                pkg_hint("chromium"),
                "Required by the Chrome/WebSpeech engine.",
                requires_admin=os.name != "nt",
            )
        )

    desktop = current_desktop()

    if os.name == "nt":
        microphones, speakers, speaker_ready = _windows_audio_devices()
        paste_backend = "PowerShell Set-Clipboard + Win32 Ctrl+V"
        hotkey_backend = "Tauri global shortcuts planned; CLI/AutoHotkey fallback"
        if not speaker_ready:
            warnings.append("Windows speaker capture is not ready for Chrome-only engine.")
            actions.append("Install or enable Stereo Mix, VB-CABLE, Virtual Audio Cable, or Voicemeeter.")
            actions.append("Set the virtual/stereo mix endpoint as the default recording input before speakers mode.")
            system_actions.append(
                _action(
                    "windows_virtual_loopback",
                    "Install virtual loopback input",
                    "manual",
                    "",
                    "Use Stereo Mix when available, or install VB-CABLE/Virtual Audio Cable/Voicemeeter and set it as recording input.",
                    requires_admin=True,
                    manual=True,
                )
            )
            system_actions.append(
                _action(
                    "windows_sound_settings",
                    "Open Windows sound input settings",
                    "manual",
                    "start ms-settings:sound",
                    "Set the virtual/stereo mix endpoint as the default input before speakers mode.",
                    manual=True,
                )
            )
        if not microphones:
            warnings.append("No Windows recording endpoints detected via PowerShell.")
            actions.append("Check Windows Settings > System > Sound > Input.")
            system_actions.append(
                _action(
                    "windows_check_input",
                    "Check recording endpoints",
                    "manual",
                    "start ms-settings:sound",
                    "Windows did not report recording endpoints via PowerShell.",
                    manual=True,
                )
            )
        batch_requirements = PROJECT_DIR / "requirements-batch.txt"
        system_actions.append(
            _action(
                "install_batch_extras",
                "Install batch transcription extras",
                "available",
                f"{_project_python()} -m pip install -r {batch_requirements}",
                "Installs faster-whisper, WhisperX, and pyannote.audio into the project virtualenv.",
            )
        )
        system_actions.append(
            _action(
                "install_user_assets",
                "Install user startup assets",
                "available",
                "python gdictate.py --install-user-assets",
                "Write source-tree startup assets for the current user.",
            )
        )
        return DiagnosticsReport(
            os=f"Windows {platform.release()}",
            desktop=desktop,
            chrome_path=chrome_path,
            paste_backend=paste_backend,
            hotkey_backend=hotkey_backend,
            microphone_devices=microphones,
            speaker_devices=speakers,
            speaker_capture_ready=speaker_ready,
            warnings=warnings,
            actions=actions,
            system_actions=system_actions,
        )

    microphones, speakers = _linux_audio_devices()
    has_pactl = shutil.which("pactl") is not None
    has_wl_copy = shutil.which("wl-copy") is not None
    has_ydotool = shutil.which("ydotool") is not None
    has_wtype = shutil.which("wtype") is not None
    ydotool_unit, ydotool_state = _user_unit_state("ydotool.service", "ydotoold.service")
    gdictate_unit, gdictate_state = _user_unit_state("gdictate-daemon.service")
    gdictate_hotkeys_unit, gdictate_hotkeys_state = _user_unit_state("gdictate-hotkeys.service")
    gdictate_enabled = _user_unit_enabled(gdictate_unit) if gdictate_unit else "missing"
    gdictate_hotkeys_enabled = _user_unit_enabled(gdictate_hotkeys_unit) if gdictate_hotkeys_unit else "missing"
    input_group_ready = _group_ready("input")
    hotkey_backend = "evdev hold + DE shortcut toggle"
    paste_backend = "wl-copy + "
    paste_backend += "ydotool" if has_ydotool else "wtype" if has_wtype else "missing key injector"
    if has_ydotool:
        paste_backend += " (+ direct type)"
    speaker_ready = has_pactl and bool(speakers)

    if not has_pactl:
        warnings.append("pactl not found; Linux speaker routing unavailable.")
        actions.append(pkg_hint("pulseaudio-utils"))
        system_actions.append(
            _action(
                "install_pactl",
                "Install pactl",
                "missing",
                pkg_hint("pulseaudio-utils"),
                "Required for PipeWire/Pulse speaker monitor routing.",
                requires_admin=True,
            )
        )
    if not has_wl_copy:
        warnings.append("wl-copy not found; Wayland clipboard paste unavailable.")
        actions.append(pkg_hint("wl-clipboard"))
        system_actions.append(
            _action(
                "install_wl_clipboard",
                "Install wl-clipboard",
                "missing",
                pkg_hint("wl-clipboard"),
                "Required for Wayland clipboard copy.",
                requires_admin=True,
            )
        )
    batch_requirements = PROJECT_DIR / "requirements-batch.txt"
    system_actions.append(
        _action(
            "install_batch_extras",
            "Install batch transcription extras",
            "available",
            f"{_project_python()} -m pip install -r {batch_requirements}",
            "Installs faster-whisper, WhisperX, and pyannote.audio into the project virtualenv.",
        )
    )
    if not has_ydotool and not has_wtype:
        warnings.append("No key injector found; text can be copied but not pasted automatically.")
        actions.append(f"{pkg_hint('ydotool')} or {pkg_hint('wtype')}")
        system_actions.append(
            _action(
                "install_key_injector",
                "Install key injector",
                "missing",
                f"{pkg_hint('ydotool')} or {pkg_hint('wtype')}",
                "Required for automatic paste outside the clipboard.",
                requires_admin=True,
            )
        )
    if has_ydotool and ydotool_state not in ("active", "missing"):
        system_actions.append(
            _action(
                "enable_ydotool",
                "Enable ydotool user service",
                ydotool_state,
                f"systemctl --user enable --now {ydotool_unit}",
                "ydotool needs a running daemon on many distributions.",
            )
        )
    if not input_group_ready:
        system_actions.append(
            _action(
                "join_input_group",
                "Allow evdev global hold hotkeys",
                "manual",
                "sudo usermod -aG input $USER",
                "Log out and back in after adding the user to the input group.",
                requires_admin=True,
                manual=True,
            )
        )
    if not microphones:
        warnings.append("No microphone sources detected.")
        system_actions.append(
            _action(
                "connect_microphone",
                "Connect or enable microphone",
                "manual",
                "",
                "No non-monitor Pulse/PipeWire sources were detected.",
                manual=True,
            )
        )
    if not speakers:
        warnings.append("No speaker sinks detected.")
        system_actions.append(
            _action(
                "connect_speakers",
                "Connect or enable speaker output",
                "manual",
                "",
                "No Pulse/PipeWire sinks were detected; speaker monitor capture needs a sink.",
                manual=True,
            )
        )

    desktop_lower = desktop.lower()
    if "gnome" in desktop_lower:
        system_actions.append(
            _action(
                "gnome_shortcuts",
                "GNOME custom shortcuts",
                "manual",
                "gnome-control-center keyboard",
                "GNOME exposes command shortcuts as press/toggle; use Tauri native hotkeys or evdev for true hold.",
                manual=True,
            )
        )
    elif "kde" in desktop_lower or "plasma" in desktop_lower:
        system_actions.append(
            _action(
                "kde_shortcuts",
                "KDE shortcuts",
                "manual",
                "kcmshell6 kcm_keys || kcmshell5 keys",
                "Use daemon start/stop/toggle commands from the shortcut report.",
                manual=True,
            )
        )

    system_actions.append(
        _action(
            "install_user_assets",
            "Install user startup assets",
            "available",
            "python gdictate.py --install-user-assets",
            "Write systemd user services, desktop launcher, and autostart entry for the current user.",
        )
    )
    if gdictate_unit:
        if gdictate_state != "active" or gdictate_enabled != "enabled":
            system_actions.append(
                _action(
                    "enable_daemon_service",
                    "Enable daemon service",
                    f"{gdictate_state}/{gdictate_enabled}",
                    "systemctl --user daemon-reload && systemctl --user enable --now gdictate-daemon.service",
                    "Start the Python daemon at login and now for this user.",
                )
            )
        else:
            system_actions.append(
                _action(
                    "disable_daemon_service",
                    "Disable daemon service",
                    "active/enabled",
                    "systemctl --user disable --now gdictate-daemon.service",
                    "Stop auto-starting the Python daemon for this user.",
                )
            )

    if gdictate_hotkeys_unit:
        if gdictate_hotkeys_state != "active" or gdictate_hotkeys_enabled != "enabled":
            system_actions.append(
                _action(
                    "enable_hotkeys_service",
                    "Enable hotkeys service",
                    f"{gdictate_hotkeys_state}/{gdictate_hotkeys_enabled}",
                    "systemctl --user daemon-reload && systemctl --user enable --now gdictate-hotkeys.service",
                    "Start the evdev hold hotkey listener at login and now for this user.",
                )
            )
        else:
            system_actions.append(
                _action(
                    "disable_hotkeys_service",
                    "Disable hotkeys service",
                    "active/enabled",
                    "systemctl --user disable --now gdictate-hotkeys.service",
                    "Stop auto-starting the evdev hold hotkey listener for this user.",
                )
            )

    return DiagnosticsReport(
        os=f"{platform.system()} {platform.release()}",
        desktop=desktop,
        chrome_path=chrome_path,
        paste_backend=paste_backend,
        hotkey_backend=hotkey_backend,
        microphone_devices=microphones,
        speaker_devices=speakers,
        speaker_capture_ready=speaker_ready,
        warnings=warnings,
        actions=actions,
        system_actions=system_actions,
    )


def apply_system_action(action_id: str) -> SystemActionResult:
    report = diagnostics_report()
    action = next((item for item in report.system_actions if item.id == action_id), None)
    if not action:
        return SystemActionResult(False, action_id, "not_found", "system action not found")

    if action.requires_admin or action.manual:
        return SystemActionResult(False, action.id, "manual", action.description or "manual action required", action.command)

    if action.id == "enable_ydotool":
        unit, state = _user_unit_state("ydotool.service", "ydotoold.service")
        if not unit:
            return SystemActionResult(False, action.id, "missing", "ydotool user service unit not found", action.command)
        if state == "active":
            return SystemActionResult(True, action.id, "active", f"{unit} already active", action.command)
        result = subprocess.run(["systemctl", "--user", "enable", "--now", unit], capture_output=True, text=True)
        if result.returncode == 0:
            return SystemActionResult(True, action.id, "done", f"{unit} enabled and started", action.command)
        message = result.stderr.strip() or result.stdout.strip() or f"failed to enable {unit}"
        return SystemActionResult(False, action.id, "failed", message, action.command)

    if action.id == "install_user_assets":
        result = install_user_assets()
        message = "\n".join(result.installed) if result.installed else "; ".join(result.warnings or result.actions)
        return SystemActionResult(result.ok, action.id, "done" if result.ok else "failed", message, action.command)

    if action.id == "install_batch_extras":
        requirements = PROJECT_DIR / "requirements-batch.txt"
        if not requirements.exists():
            return SystemActionResult(False, action.id, "missing", f"{requirements} not found", action.command)
        result = subprocess.run(
            [_project_python(), "-m", "pip", "install", "-r", str(requirements)],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return SystemActionResult(True, action.id, "done", "batch transcription extras installed", action.command)
        message = result.stderr.strip() or result.stdout.strip() or "failed to install batch transcription extras"
        return SystemActionResult(False, action.id, "failed", message, action.command)

    if action.id == "enable_daemon_service":
        reload_result = subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, text=True)
        if reload_result.returncode != 0:
            message = reload_result.stderr.strip() or reload_result.stdout.strip() or "systemctl daemon-reload failed"
            return SystemActionResult(False, action.id, "failed", message, action.command)
        result = subprocess.run(
            ["systemctl", "--user", "enable", "--now", "gdictate-daemon.service"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return SystemActionResult(True, action.id, "done", "gdictate-daemon.service enabled and started", action.command)
        message = result.stderr.strip() or result.stdout.strip() or "failed to enable gdictate-daemon.service"
        return SystemActionResult(False, action.id, "failed", message, action.command)

    if action.id == "enable_hotkeys_service":
        reload_result = subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, text=True)
        if reload_result.returncode != 0:
            message = reload_result.stderr.strip() or reload_result.stdout.strip() or "systemctl daemon-reload failed"
            return SystemActionResult(False, action.id, "failed", message, action.command)
        result = subprocess.run(
            ["systemctl", "--user", "enable", "--now", "gdictate-hotkeys.service"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return SystemActionResult(True, action.id, "done", "gdictate-hotkeys.service enabled and started", action.command)
        message = result.stderr.strip() or result.stdout.strip() or "failed to enable gdictate-hotkeys.service"
        return SystemActionResult(False, action.id, "failed", message, action.command)

    if action.id == "disable_daemon_service":
        result = subprocess.run(
            ["systemctl", "--user", "disable", "--now", "gdictate-daemon.service"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return SystemActionResult(True, action.id, "done", "gdictate-daemon.service disabled and stopped", action.command)
        message = result.stderr.strip() or result.stdout.strip() or "failed to disable gdictate-daemon.service"
        return SystemActionResult(False, action.id, "failed", message, action.command)

    if action.id == "disable_hotkeys_service":
        result = subprocess.run(
            ["systemctl", "--user", "disable", "--now", "gdictate-hotkeys.service"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return SystemActionResult(True, action.id, "done", "gdictate-hotkeys.service disabled and stopped", action.command)
        message = result.stderr.strip() or result.stdout.strip() or "failed to disable gdictate-hotkeys.service"
        return SystemActionResult(False, action.id, "failed", message, action.command)

    return SystemActionResult(False, action.id, "blocked", "automatic execution is not allowed for this action", action.command)
