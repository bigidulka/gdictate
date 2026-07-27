from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Optional

from .app import Dictation
from .models import State


HOLD_START_DELAY_SECONDS = 0.12


def _binding_groups(binding: str, ecodes) -> list[set[int]]:
    """Parse a small, explicit hotkey grammar into evdev key groups."""
    aliases = {
        "CTRL": {ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL},
        "CONTROL": {ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL},
        "ALT": {ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT},
        "SUPER": {ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA},
        "SHIFT": {ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT},
    }
    groups: list[set[int]] = []
    for token in (part.strip().upper().replace("-", "_") for part in binding.split("+")):
        if not token:
            continue
        if token in aliases:
            groups.append(aliases[token])
            continue
        code = getattr(ecodes, f"KEY_{token}", None)
        if not isinstance(code, int):
            raise ValueError(f"unknown evdev key '{token}'")
        groups.append({code})
    if not groups:
        raise ValueError("empty hotkey")
    return groups


def _is_pressed(groups: list[set[int]], pressed: set[int]) -> bool:
    return all(bool(group & pressed) for group in groups)


def _hold_keyboards(devices, bindings: list[list[set[int]]], ecodes):
    """Ignore mouse, power and synthetic keyboard devices."""
    keyboards = []
    for device in devices:
        if "ydotool" in device.name.lower() or "virtual" in device.name.lower():
            continue
        keys = set(device.capabilities().get(ecodes.EV_KEY, []))
        if any(all(bool(group & keys) for group in binding) for binding in bindings):
            keyboards.append(device)
    return keyboards


async def run_dual_hold_evdev_actions(
    on_start,
    on_stop,
    mic_hold: str = "F8",
    speakers_hold: str = "F9",
) -> bool:
    if os.name == "nt":
        print("[WARN] evdev dual hold is Linux-only", file=sys.stderr, flush=True)
        return False

    import evdev
    from evdev import ecodes

    try:
        bindings = {
            "mic": _binding_groups(mic_hold, ecodes),
            "speakers": _binding_groups(speakers_hold, ecodes),
        }
    except ValueError as exc:
        print(f"[ERR] Invalid hold binding: {exc}", file=sys.stderr, flush=True)
        return False

    devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
    keyboards = _hold_keyboards(devices, list(bindings.values()), ecodes)
    if not keyboards:
        print(
            "[WARN] No compatible keyboards found via evdev. Add user to input group and re-login for global hotkey.",
            file=sys.stderr,
            flush=True,
        )
        return False

    device_pressed: dict[str, set[int]] = {device.path: set() for device in keyboards}
    desired_source: Optional[str] = None
    active_source: Optional[str] = None
    pending_start: Optional[asyncio.Task] = None
    lock = asyncio.Lock()

    print(f"[BIND] Hold {mic_hold} = mic; hold {speakers_hold} = speakers ({len(keyboards)} keyboard device(s))\n", flush=True)

    def target_source() -> Optional[str]:
        # A hold chord must be complete on one physical keyboard. Do not combine
        # Alt from one device with arrows from another device.
        for pressed in device_pressed.values():
            if _is_pressed(bindings["mic"], pressed):
                return "mic"
            if _is_pressed(bindings["speakers"], pressed):
                return "speakers"
        return None

    async def delayed_start(source: str) -> None:
        nonlocal active_source
        try:
            await asyncio.sleep(HOLD_START_DELAY_SECONDS)
            async with lock:
                if desired_source != source or active_source:
                    return
                await on_start(source)
                active_source = source
        except asyncio.CancelledError:
            return

    async def reconcile(source: Optional[str]) -> None:
        nonlocal active_source, pending_start
        async with lock:
            if pending_start and pending_start.done():
                pending_start = None
            if source == active_source:
                return
            if active_source:
                await on_stop()
                active_source = None
            if pending_start and not pending_start.done():
                pending_start.cancel()
            pending_start = None
            if source:
                pending_start = asyncio.create_task(delayed_start(source))

    async def read(device) -> None:
        nonlocal desired_source
        try:
            async for event in device.async_read_loop():
                if event.type != ecodes.EV_KEY or event.value == 2:
                    continue
                pressed = device_pressed[device.path]
                if event.value == 1:
                    pressed.add(event.code)
                elif event.value == 0:
                    pressed.discard(event.code)
                target = target_source()
                if target != desired_source:
                    desired_source = target
                    asyncio.create_task(reconcile(target))
        except OSError:
            pass
        finally:
            device_pressed.pop(device.path, None)
            target = target_source()
            if target != desired_source:
                desired_source = target
                asyncio.create_task(reconcile(target))

    tasks = [asyncio.create_task(read(keyboard)) for keyboard in keyboards]
    await asyncio.gather(*tasks)
    return True


async def run_evdev(dictation: Dictation, key_combo: str) -> bool:
    if os.name == "nt":
        print("[WARN] evdev hotkeys are Linux-only", file=sys.stderr, flush=True)
        return False

    import evdev
    from evdev import ecodes

    key_map = {
        "CTRL": {ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL},
        "ALT": {ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT},
        "SUPER": {ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA},
        "SHIFT": {ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT},
    }

    grouped = {}
    for part in key_combo.upper().split("+"):
        part = part.strip()
        if part in key_map:
            grouped[part] = key_map[part]

    if not grouped:
        print("[ERR] Invalid hotkey", file=sys.stderr, flush=True)
        return False

    devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
    keyboards = [device for device in devices if ecodes.EV_KEY in device.capabilities()]
    if not keyboards:
        print(
            "[WARN] No keyboards found via evdev. Add user to input group and re-login for global hotkey.",
            file=sys.stderr,
            flush=True,
        )
        return False

    print(f"[BIND] {key_combo} ({len(keyboards)} kb)\n", flush=True)
    last = 0.0
    toggling = False

    async def do_toggle() -> None:
        nonlocal toggling
        if toggling:
            return
        toggling = True
        try:
            await dictation.toggle()
        finally:
            toggling = False

    async def read(device) -> None:
        nonlocal last
        pressed = set()
        try:
            async for event in device.async_read_loop():
                if event.type != ecodes.EV_KEY:
                    continue
                if event.value == 1:
                    pressed.add(event.code)
                elif event.value == 0:
                    pressed.discard(event.code)

                ok = all(any(key in pressed for key in keys) for keys in grouped.values())
                now = time.monotonic()
                if ok and event.value == 1 and now - last > 0.3:
                    last = now
                    asyncio.ensure_future(do_toggle())
        except OSError:
            pass

    tasks = [asyncio.create_task(read(keyboard)) for keyboard in keyboards]
    await asyncio.gather(*tasks)
    return True


async def run_dual_hold_evdev(
    dictation: Dictation,
    mic_hold: str = "F8",
    speakers_hold: str = "F9",
) -> bool:
    async def on_start(source: str) -> None:
        await dictation.start_recording(source)

    async def on_stop() -> None:
        if dictation.state == State.RECORDING:
            await dictation.stop_recording()

    return await run_dual_hold_evdev_actions(on_start, on_stop, mic_hold, speakers_hold)


async def run_stdin_toggle(dictation: Dictation) -> None:
    if not sys.stdin.isatty():
        print("[WARN] No terminal input available. Waiting until Ctrl+C.", file=sys.stderr, flush=True)
        await asyncio.Event().wait()

    print("[BIND] Press Enter in this terminal to toggle recording. Ctrl+C exits.\n", flush=True)
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    while True:
        line = await reader.readline()
        if not line:
            print("[WARN] Terminal input closed. Waiting until Ctrl+C.", file=sys.stderr, flush=True)
            await asyncio.Event().wait()
        await dictation.toggle()
