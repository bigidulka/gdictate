from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AudioRouting:
    mode: str = "mic"
    router: str = ""
    previous_default_source: Optional[str] = None
    active_source: Optional[str] = None
    module_ids: list[str] = field(default_factory=list)

    def restore_default_source(self) -> None:
        if os.name == "nt":
            return
        if self.previous_default_source and self.active_source:
            current = get_default_source()
            if current == self.active_source:
                set_default_source(self.previous_default_source)

    def close(self) -> None:
        self.restore_default_source()
        if os.name == "nt":
            return
        for module_id in reversed(self.module_ids):
            subprocess.run(
                ["pactl", "unload-module", module_id],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def _pactl(args: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["pactl", *args], capture_output=True, text=True, check=check)


def get_default_source() -> Optional[str]:
    if os.name == "nt":
        return None
    try:
        result = _pactl(["get-default-source"])
    except FileNotFoundError:
        return None
    return result.stdout.strip() or None


def get_default_sink() -> Optional[str]:
    if os.name == "nt":
        return None
    try:
        result = _pactl(["get-default-sink"])
    except FileNotFoundError:
        return None
    return result.stdout.strip() or None


def set_default_source(name: str) -> bool:
    if os.name == "nt":
        return False
    result = subprocess.run(["pactl", "set-default-source", name], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[WARN] Failed to set source {name}: {result.stderr.strip()}", file=sys.stderr, flush=True)
        return False
    return True


def source_names() -> set[str]:
    if os.name == "nt":
        return set()
    try:
        result = _pactl(["list", "short", "sources"])
    except FileNotFoundError:
        return set()
    names = set()
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            names.add(parts[1])
    return names


def _wait_for_source(name: str, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if name in source_names():
            return True
        time.sleep(0.05)
    return name in source_names()


def _wait_for_default_source(name: str, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if get_default_source() == name:
            return True
        time.sleep(0.05)
    return get_default_source() == name


def find_best_microphone() -> Optional[dict]:
    if os.name == "nt":
        return None
    try:
        result = _pactl(["list", "sources"])
    except FileNotFoundError:
        return None

    sources = []
    current = {}
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if line.startswith("Source #"):
            if current:
                sources.append(current)
            current = {}
        elif line.startswith("Name:"):
            current["name"] = line.split(":", 1)[1].strip()
        elif line.startswith("Description:"):
            current["desc"] = line.split(":", 1)[1].strip()
        elif line.startswith("State:"):
            current["state"] = line.split(":", 1)[1].strip()
    if current:
        sources.append(current)

    inputs = [s for s in sources if ".monitor" not in s.get("name", "")]
    if not inputs:
        return None

    def score(source: dict) -> int:
        name = source.get("name", "")
        state = source.get("state", "")
        if "snd_aloop" in name or name.startswith("gdictate_"):
            return -1
        if state == "RUNNING":
            return 3
        if state == "IDLE":
            return 2
        return 1

    return max(inputs, key=score)


def ensure_microphone() -> Optional[str]:
    if os.name == "nt":
        return None
    best = find_best_microphone()
    if not best:
        print("\033[0;33m[WARN]\033[0m No microphone found. Connect a mic and restart.", file=sys.stderr, flush=True)
        return None

    name = best.get("name", "")
    desc = best.get("desc", name)
    if get_default_source() == name:
        print(f"[MIC] {desc}", flush=True)
        return name

    set_default_source(name)
    print(f"[MIC] Set default: {desc}", flush=True)
    return name


def unload_stale_audio_modules() -> None:
    if os.name == "nt":
        return
    try:
        result = _pactl(["list", "short", "modules"])
    except FileNotFoundError:
        return
    for line in result.stdout.splitlines():
        if "gdictate_" not in line:
            continue
        module_id = line.split("\t", 1)[0]
        subprocess.run(["pactl", "unload-module", module_id], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _load_module(args: list[str]) -> str:
    result = _pactl(["load-module", *args], check=True)
    return result.stdout.strip()


def _windows_audio_route(mode: str, speaker_input: str = "auto") -> AudioRouting:
    if mode == "mic":
        return AudioRouting(mode=mode, router=f"windows:{speaker_input}")
    if speaker_input == "manual":
        print(
            "[AUDIO] Windows speakers mode: using current default recording input; configure loopback manually.",
            file=sys.stderr,
            flush=True,
        )
        return AudioRouting(mode=mode, router=f"windows:{speaker_input}")

    input_label = {
        "stereo-mix": "Stereo Mix",
        "vb-cable": "VB-CABLE / Cable Output",
        "auto": "Stereo Mix, VB-CABLE, Virtual Audio Cable, or Voicemeeter",
    }.get(speaker_input, speaker_input)
    print(
        f"[WARN] Windows speaker capture needs {input_label} as the default recording input before starting {mode} mode.",
        file=sys.stderr,
        flush=True,
    )
    return AudioRouting(mode=mode, router=f"windows:{speaker_input}")


def audio_router_label(linux_router: str = "pipewire-pulse", windows_speaker_input: str = "auto") -> str:
    if os.name == "nt":
        return f"windows:{windows_speaker_input}"
    return linux_router or "pipewire-pulse"


def configure_audio_source(mode: str, linux_router: str = "pipewire-pulse", windows_speaker_input: str = "auto") -> AudioRouting:
    """Select browser default capture source for current OS."""
    if os.name == "nt":
        return _windows_audio_route(mode, windows_speaker_input)

    linux_router = linux_router or "pipewire-pulse"
    if linux_router == "manual":
        print("[AUDIO] Linux manual router: leaving default input unchanged", file=sys.stderr, flush=True)
        return AudioRouting(mode=mode, router=linux_router)
    if linux_router not in ("pipewire-pulse", "pulse"):
        print(f"[WARN] Unknown Linux audio router '{linux_router}'; using pactl-compatible routing", file=sys.stderr, flush=True)
        linux_router = "pipewire-pulse"

    if mode == "mic":
        previous = get_default_source()
        active = ensure_microphone()
        if active and previous != active:
            return AudioRouting(mode=mode, router=linux_router, previous_default_source=previous, active_source=active)
        return AudioRouting(mode=mode, router=linux_router)

    try:
        previous = get_default_source()
        sink = get_default_sink()
    except FileNotFoundError:
        print("[WARN] pactl not found; audio source unchanged", file=sys.stderr, flush=True)
        return AudioRouting(mode=mode, router=linux_router)

    if not sink:
        print("[WARN] No default speaker sink found", file=sys.stderr, flush=True)
        return AudioRouting(mode=mode, router=linux_router)

    speaker_monitor = f"{sink}.monitor"
    if speaker_monitor not in source_names():
        print(f"[WARN] Speaker monitor not found: {speaker_monitor}", file=sys.stderr, flush=True)
        return AudioRouting(mode=mode, router=linux_router)

    if mode == "speakers":
        unload_stale_audio_modules()
        previous = get_default_source()
        module_ids = []
        speaker_source = "gdictate_speakers_source"
        try:
            module_ids.append(
                _load_module(
                    [
                        "module-remap-source",
                        f"master={speaker_monitor}",
                        f"source_name={speaker_source}",
                        "source_properties=device.description=gdictate_speakers_source",
                    ]
                )
            )
        except subprocess.CalledProcessError as e:
            print(f"[WARN] Failed to create speaker source: {e.stderr.strip()}", file=sys.stderr, flush=True)
            return AudioRouting(mode=mode, router=linux_router)

        if not _wait_for_source(speaker_source) or not set_default_source(speaker_source):
            for module_id in reversed(module_ids):
                subprocess.run(["pactl", "unload-module", module_id], capture_output=True)
            return AudioRouting(mode=mode, router=linux_router)
        _wait_for_default_source(speaker_source)
        print(f"[AUDIO] Set default source: speakers ({speaker_source})", flush=True)
        return AudioRouting(mode=mode, router=linux_router, previous_default_source=previous, active_source=speaker_source, module_ids=module_ids)

    unload_stale_audio_modules()
    previous = get_default_source()
    mic = find_best_microphone()
    if not mic:
        print("[WARN] No microphone found; using speakers only", file=sys.stderr, flush=True)
        set_default_source(speaker_monitor)
        return AudioRouting(mode=mode, router=linux_router, previous_default_source=previous, active_source=speaker_monitor)

    module_ids = []
    try:
        module_ids.append(_load_module(["module-null-sink", "sink_name=gdictate_mix_sink", "sink_properties=device.description=gdictate_mix"]))
        module_ids.append(_load_module(["module-loopback", f"source={mic['name']}", "sink=gdictate_mix_sink", "latency_msec=20"]))
        module_ids.append(_load_module(["module-loopback", f"source={speaker_monitor}", "sink=gdictate_mix_sink", "latency_msec=20"]))
        module_ids.append(
            _load_module(
                [
                    "module-remap-source",
                    "master=gdictate_mix_sink.monitor",
                    "source_name=gdictate_mix_source",
                    "source_properties=device.description=gdictate_mix_source",
                ]
            )
        )
    except subprocess.CalledProcessError as e:
        for module_id in reversed(module_ids):
            subprocess.run(["pactl", "unload-module", module_id], capture_output=True)
        print(f"[WARN] Failed to create mixed source: {e.stderr.strip()}", file=sys.stderr, flush=True)
        return AudioRouting(mode=mode, router=linux_router)

    mixed_source = "gdictate_mix_source"
    if not _wait_for_source(mixed_source):
        for module_id in reversed(module_ids):
            subprocess.run(["pactl", "unload-module", module_id], capture_output=True)
        print(f"[WARN] Mixed source did not appear: {mixed_source}", file=sys.stderr, flush=True)
        return AudioRouting(mode=mode, router=linux_router)

    if not set_default_source(mixed_source):
        for module_id in reversed(module_ids):
            subprocess.run(["pactl", "unload-module", module_id], capture_output=True)
        return AudioRouting(mode=mode, router=linux_router)
    if not _wait_for_default_source(mixed_source):
        for module_id in reversed(module_ids):
            subprocess.run(["pactl", "unload-module", module_id], capture_output=True)
        print(f"[WARN] Default source did not switch to {mixed_source}", file=sys.stderr, flush=True)
        return AudioRouting(mode=mode, router=linux_router)

    print(f"[AUDIO] Set default source: mic + speakers ({mixed_source})", flush=True)
    return AudioRouting(mode=mode, router=linux_router, previous_default_source=previous, active_source=mixed_source, module_ids=module_ids)
