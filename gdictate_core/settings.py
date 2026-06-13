from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .constants import SETTINGS_FILE


@dataclass
class EngineSettings:
    name: str = "chrome"


@dataclass
class BindingSettings:
    mode: str = "dual-hold"
    toggle: str = "CTRL+ALT"
    mic_hold: str = "ALT+LEFT"
    speakers_hold: str = "ALT+RIGHT"
    linux_backend: str = "de-shortcut+evdev"


@dataclass
class AudioSettings:
    source: str = "mic"
    restore_default_after_start: bool = True
    linux_router: str = "pipewire-pulse"
    windows_speaker_input: str = "auto"


@dataclass
class PasteSettings:
    mode: str = "auto"
    live: bool = True
    linux_terminal_combo: str = "ctrl-v"
    windows_combo: str = "ctrl-v"


@dataclass
class ChromeSettings:
    channel: str = "auto"
    hidden: bool = True
    setup_required: bool = False
    profile_dir: str = ""


@dataclass
class OverlaySettings:
    enabled: bool = True
    click_through: bool = True
    show_interim: bool = True
    position: str = "lower-center"


@dataclass
class AppSettings:
    language: str = "ru-RU"
    engine: EngineSettings = field(default_factory=EngineSettings)
    bind: BindingSettings = field(default_factory=BindingSettings)
    audio: AudioSettings = field(default_factory=AudioSettings)
    paste: PasteSettings = field(default_factory=PasteSettings)
    chrome: ChromeSettings = field(default_factory=ChromeSettings)
    overlay: OverlaySettings = field(default_factory=OverlaySettings)


@dataclass
class SettingsField:
    path: str
    label: str
    kind: str
    default: Any
    options: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class SettingsGroup:
    id: str
    label: str
    fields: list[SettingsField]


def default_settings() -> AppSettings:
    return AppSettings()


def _value_at_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _field(
    defaults: dict[str, Any],
    path: str,
    label: str,
    kind: str,
    options: list[str] | None = None,
    description: str = "",
) -> SettingsField:
    return SettingsField(
        path=path,
        label=label,
        kind=kind,
        default=_value_at_path(defaults, path),
        options=options or [],
        description=description,
    )


def settings_schema() -> list[SettingsGroup]:
    defaults = asdict(default_settings())
    return [
        SettingsGroup(
            "general",
            "General",
            [
                _field(defaults, "language", "Language", "string", description="BCP-47 speech locale, e.g. ru-RU/en-US"),
                _field(defaults, "engine.name", "Speech engine", "select", ["chrome"]),
                _field(defaults, "audio.source", "Default channel", "select", ["mic", "speakers", "both"]),
            ],
        ),
        SettingsGroup(
            "binds",
            "Binds",
            [
                _field(defaults, "bind.mode", "Bind mode", "select", ["dual-hold", "toggle", "enter"]),
                _field(defaults, "bind.toggle", "Toggle bind", "hotkey"),
                _field(defaults, "bind.mic_hold", "Microphone hold bind", "hotkey"),
                _field(defaults, "bind.speakers_hold", "Speakers hold bind", "hotkey"),
                _field(defaults, "bind.linux_backend", "Linux backend", "select", ["de-shortcut+evdev", "de-shortcut", "evdev", "terminal"]),
            ],
        ),
        SettingsGroup(
            "audio",
            "Audio",
            [
                _field(defaults, "audio.linux_router", "Linux speaker router", "select", ["pipewire-pulse", "pulse", "manual"]),
                _field(defaults, "audio.windows_speaker_input", "Windows speaker input", "select", ["auto", "stereo-mix", "vb-cable", "manual"]),
                _field(defaults, "audio.restore_default_after_start", "Restore default input after capture starts", "bool"),
            ],
        ),
        SettingsGroup(
            "paste",
            "Paste",
            [
                _field(defaults, "paste.mode", "Paste mode", "select", ["auto", "ydotool", "wtype", "none"]),
                _field(defaults, "paste.live", "Paste while dictating", "bool"),
                _field(defaults, "paste.linux_terminal_combo", "Linux terminal combo", "select", ["ctrl-shift-v", "ctrl-v"]),
                _field(defaults, "paste.windows_combo", "Windows paste combo", "select", ["ctrl-v"]),
            ],
        ),
        SettingsGroup(
            "chrome",
            "Chrome",
            [
                _field(defaults, "chrome.channel", "Chrome channel", "select", ["auto", "stable", "beta", "dev", "chromium", "edge"]),
                _field(defaults, "chrome.hidden", "Hidden automation window", "bool"),
                _field(defaults, "chrome.setup_required", "Force setup flow", "bool"),
                _field(defaults, "chrome.profile_dir", "Profile directory", "path"),
            ],
        ),
        SettingsGroup(
            "overlay",
            "Live",
            [
                _field(defaults, "overlay.enabled", "Live popup", "bool"),
                _field(defaults, "overlay.click_through", "Click-through", "bool"),
                _field(defaults, "overlay.show_interim", "Show interim text", "bool"),
                _field(defaults, "overlay.position", "Popup position", "select", ["lower-center", "top-center", "bottom-right"]),
            ],
        ),
    ]


def settings_snapshot(path: Path = SETTINGS_FILE) -> dict[str, Any]:
    return {
        "path": str(path),
        "current": asdict(load_settings(path)),
        "defaults": asdict(default_settings()),
        "schema": [asdict(group) for group in settings_schema()],
    }


def _merge_dataclass(cls, data: dict[str, Any]):
    base = cls()
    for key, value in data.items():
        if not hasattr(base, key):
            continue
        current = getattr(base, key)
        if hasattr(current, "__dataclass_fields__") and isinstance(value, dict):
            setattr(base, key, _merge_dataclass(type(current), value))
        else:
            setattr(base, key, value)
    return base


def normalize_settings(settings: AppSettings) -> AppSettings:
    if settings.overlay.position == "bottom-center":
        settings.overlay.position = "lower-center"
    return settings


def load_settings(path: Path = SETTINGS_FILE) -> AppSettings:
    if not path.exists():
        return AppSettings()
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return AppSettings()
    return normalize_settings(_merge_dataclass(AppSettings, data))


def save_settings(settings: AppSettings, path: Path = SETTINGS_FILE) -> None:
    settings = normalize_settings(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(asdict(settings), f, ensure_ascii=False, indent=2)
        f.write("\n")


def reset_settings(path: Path = SETTINGS_FILE) -> AppSettings:
    settings = default_settings()
    save_settings(settings, path)
    return settings
