from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Optional

from .app import Dictation
from .models import State


async def run_dual_hold_evdev_actions(on_start, on_stop) -> bool:
    if os.name == "nt":
        print("[WARN] evdev dual hold is Linux-only", file=sys.stderr, flush=True)
        return False

    import evdev
    from evdev import ecodes

    devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
    keyboards = [device for device in devices if ecodes.EV_KEY in device.capabilities()]
    if not keyboards:
        print(
            "[WARN] No keyboards found via evdev. Add user to input group and re-login for global hotkey.",
            file=sys.stderr,
            flush=True,
        )
        return False

    alts = {ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT}
    lefts = {ecodes.KEY_LEFT}
    rights = {ecodes.KEY_RIGHT}
    desired_source: Optional[str] = None
    active_source: Optional[str] = None
    lock = asyncio.Lock()

    print("[BIND] Hold Alt+Left = mic; hold Alt+Right = speakers\n", flush=True)

    async def switch_to(source: Optional[str]) -> None:
        nonlocal active_source
        async with lock:
            if source == active_source:
                return
            if active_source:
                await on_stop()
            active_source = None
            if source:
                await on_start(source)
                active_source = source

    async def read(device) -> None:
        nonlocal desired_source
        pressed = set()
        try:
            async for event in device.async_read_loop():
                if event.type != ecodes.EV_KEY:
                    continue
                if event.value == 1:
                    pressed.add(event.code)
                elif event.value == 0:
                    pressed.discard(event.code)
                elif event.value == 2:
                    continue

                has_alt = any(key in pressed for key in alts)
                target = None
                if has_alt and any(key in pressed for key in lefts):
                    target = "mic"
                elif has_alt and any(key in pressed for key in rights):
                    target = "speakers"
                elif has_alt and desired_source:
                    target = desired_source

                if target != desired_source:
                    desired_source = target
                    asyncio.create_task(switch_to(target))
        except OSError:
            pass

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


async def run_dual_hold_evdev(dictation: Dictation) -> bool:
    async def on_start(source: str) -> None:
        await dictation.start_recording(source)

    async def on_stop() -> None:
        if dictation.state == State.RECORDING:
            await dictation.stop_recording()

    return await run_dual_hold_evdev_actions(on_start, on_stop)


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
