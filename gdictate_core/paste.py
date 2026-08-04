from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys


LINUX_CLIPBOARD_TIMEOUT = 1.2
LINUX_MODIFIER_TIMEOUT = 1.0
LINUX_KEY_DELAY_MS = "20"

KEY_LEFTCTRL = 29
KEY_RIGHTCTRL = 97
KEY_LEFTSHIFT = 42
KEY_RIGHTSHIFT = 54
KEY_LEFTALT = 56
KEY_RIGHTALT = 100
KEY_LEFTMETA = 125
KEY_RIGHTMETA = 126
KEY_V = 47
KEY_INSERT = 110

LINUX_MODIFIERS = {
    KEY_LEFTCTRL,
    KEY_RIGHTCTRL,
    KEY_LEFTSHIFT,
    KEY_RIGHTSHIFT,
    KEY_LEFTALT,
    KEY_RIGHTALT,
    KEY_LEFTMETA,
    KEY_RIGHTMETA,
}

LINUX_MODIFIER_RELEASES = [
    f"{KEY_LEFTCTRL}:0",
    f"{KEY_RIGHTCTRL}:0",
    f"{KEY_LEFTSHIFT}:0",
    f"{KEY_RIGHTSHIFT}:0",
    f"{KEY_LEFTALT}:0",
    f"{KEY_RIGHTALT}:0",
    f"{KEY_LEFTMETA}:0",
    f"{KEY_RIGHTMETA}:0",
]


async def paste_text(text: str, mode: str = "auto", linux_combo: str = "shift-insert", windows_combo: str = "ctrl-v") -> bool:
    if not text:
        return False
    mode = (mode or "auto").lower()
    if mode == "none":
        return False
    if os.name == "nt":
        if mode == "copy":
            return await _copy_windows(text)
        return await _paste_windows(text, windows_combo)
    return await _paste_linux(text, mode, linux_combo)


async def _paste_linux(text: str, mode: str, combo: str) -> bool:
    combo = _normalize_linux_combo(combo)

    if mode == "copy":
        return await _copy_linux(text)

    if mode == "type" and text.isascii():
        await _wait_linux_modifiers_released()
        if await _ydotool_type(text):
            print("[PASTE] typed ascii via ydotool", file=sys.stderr, flush=True)
            return True
        return False

    if mode == "type":
        print("[PASTE] unicode text uses verified clipboard paste", file=sys.stderr, flush=True)
        mode = "auto"

    if not await _copy_linux(text):
        return False

    await _wait_linux_modifiers_released()
    await _release_linux_virtual_modifiers()
    await asyncio.sleep(0.08)

    if mode == "auto" and await _ghostty_native_paste():
        print("[PASTE] sent via Ghostty native action", file=sys.stderr, flush=True)
        return True

    if combo == "shift-insert" and not await _copy_linux(text, primary=True):
        print("[WARN] primary clipboard unavailable; attempting regular clipboard paste", file=sys.stderr, flush=True)

    if mode in ("auto", "ydotool") and shutil.which("ydotool"):
        try:
            if await _ydotool_paste(combo):
                print(f"[PASTE] sent {combo} via ydotool", file=sys.stderr, flush=True)
                return True
        except OSError as exc:
            print(f"[WARN] ydotool paste failed: {exc}", file=sys.stderr, flush=True)
        if mode == "ydotool":
            return False

    if mode in ("auto", "wtype") and shutil.which("wtype"):
        try:
            if await _wtype_paste(combo):
                print(f"[PASTE] sent {combo} via wtype", file=sys.stderr, flush=True)
                return True
        except OSError as exc:
            print(f"[WARN] wtype paste failed: {exc}", file=sys.stderr, flush=True)

    print("[WARN] paste key injector unavailable; text copied only", file=sys.stderr, flush=True)
    return False


def _normalize_linux_combo(combo: str) -> str:
    combo = (combo or "shift-insert").lower().replace("+", "-")
    if combo in ("shift-insert", "shift-ins"):
        return "shift-insert"
    if combo in ("ctrl-shift-v", "control-shift-v"):
        return "ctrl-shift-v"
    return "ctrl-v"


async def _copy_linux(text: str, primary: bool = False) -> bool:
    if not shutil.which("wl-copy"):
        print("[WARN] wl-copy not found; skip paste", file=sys.stderr, flush=True)
        return False

    args = ["wl-copy"]
    if primary:
        args.append("--primary")
    args += ["--type", "text/plain;charset=utf-8"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as exc:
        print(f"[WARN] wl-copy failed to start: {exc}", file=sys.stderr, flush=True)
        return False
    if not proc.stdin:
        return False
    proc.stdin.write(text.encode("utf-8"))
    await proc.stdin.drain()
    proc.stdin.close()
    try:
        await proc.stdin.wait_closed()
    except BrokenPipeError:
        pass

    try:
        code = await asyncio.wait_for(proc.wait(), timeout=0.12)
    except asyncio.TimeoutError:
        code = None
        asyncio.create_task(_reap_process(proc))

    label = "primary clipboard" if primary else "clipboard"
    if code not in (None, 0):
        print(f"[WARN] wl-copy {label} failed ({code})", file=sys.stderr, flush=True)
        return False

    if not shutil.which("wl-paste"):
        print(f"[PASTE] {label} set; wl-paste unavailable for verification", file=sys.stderr, flush=True)
        return True

    verified = await _wait_linux_clipboard_text(text, primary=primary)
    if verified:
        print(f"[PASTE] {label} verified", file=sys.stderr, flush=True)
    else:
        # wl-copy successfully accepted input. Verification is racy under
        # Wayland because another clipboard client may own or inspect the
        # selection before wl-paste reads it. Never drop a recognized phrase
        # merely because this optional read-back lost that race.
        print(f"[WARN] {label} read-back failed; attempting paste", file=sys.stderr, flush=True)
    return True


async def _ghostty_native_paste() -> bool:
    """Use Ghostty's paste action only when its window owns focused surface.

    AT-SPI gives us the active top-level process without creating or focusing a
    window. If that process is Ghostty and it exposes exactly one D-Bus window,
    its native action avoids layout-sensitive synthetic key delivery. Any
    uncertainty falls through to the generic Wayland injector.
    """
    if os.name != "posix" or not shutil.which("gdbus"):
        return False
    try:
        focused_pids = await asyncio.to_thread(_focused_accessible_process_ids)
        owner_pid = await _gdbus_process_id("com.mitchellh.ghostty")
        if not owner_pid or owner_pid not in focused_pids:
            return False
        paths = await _ghostty_window_paths()
        if len(paths) != 1:
            return False
        proc = await asyncio.create_subprocess_exec(
            "gdbus",
            "call",
            "--session",
            "--dest",
            "com.mitchellh.ghostty",
            "--object-path",
            paths[0],
            "--method",
            "org.gtk.Actions.Activate",
            "paste",
            "[]",
            "{}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=0.8)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return False
        return proc.returncode == 0
    except (OSError, RuntimeError, TimeoutError):
        return False


def _focused_accessible_process_ids() -> set[int]:
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except Exception:
        return set()

    pids: set[int] = set()
    try:
        desktop = Atspi.get_desktop(0)

        def walk(obj, depth: int = 0) -> None:
            if depth > 20:
                return
            try:
                state = obj.get_state_set()
                role = obj.get_role_name()
                if state.contains(Atspi.StateType.FOCUSED) or (
                    role in ("frame", "window") and state.contains(Atspi.StateType.ACTIVE)
                ):
                    pid = int(obj.get_process_id())
                    if pid > 0:
                        pids.add(pid)
                for index in range(obj.get_child_count()):
                    child = obj.get_child_at_index(index)
                    if child:
                        walk(child, depth + 1)
            except Exception:
                return

        walk(desktop)
    except Exception:
        return set()
    return pids


async def _gdbus_process_id(bus_name: str) -> int | None:
    proc = await asyncio.create_subprocess_exec(
        "gdbus",
        "call",
        "--session",
        "--dest",
        "org.freedesktop.DBus",
        "--object-path",
        "/org/freedesktop/DBus",
        "--method",
        "org.freedesktop.DBus.GetConnectionUnixProcessID",
        bus_name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=0.5)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return None
    if proc.returncode != 0:
        return None
    match = re.search(rb"\b(\d+)\b", stdout)
    return int(match.group(1)) if match else None


async def _ghostty_window_paths() -> list[str]:
    proc = await asyncio.create_subprocess_exec(
        "gdbus",
        "introspect",
        "--xml",
        "--session",
        "--dest",
        "com.mitchellh.ghostty",
        "--object-path",
        "/com/mitchellh/ghostty/window",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=0.5)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return []
    if proc.returncode != 0:
        return []
    names = re.findall(rb'<node name="([0-9]+)"', stdout)
    return [f"/com/mitchellh/ghostty/window/{name.decode('ascii')}" for name in names]


async def _reap_process(proc: asyncio.subprocess.Process) -> None:
    try:
        await proc.wait()
    except Exception:
        pass


async def _wait_linux_clipboard_text(expected: str, primary: bool = False) -> bool:
    deadline = asyncio.get_running_loop().time() + LINUX_CLIPBOARD_TIMEOUT
    last = ""
    while asyncio.get_running_loop().time() < deadline:
        args = ["wl-paste", "--no-newline"]
        if primary:
            args.append("--primary")
        args += ["--type", "text/plain"]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=0.35)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            await asyncio.sleep(0.04)
            continue
        if proc.returncode == 0:
            last = stdout.decode("utf-8", errors="replace")
            if last == expected:
                return True
        await asyncio.sleep(0.04)
    if last:
        print(f"[WARN] clipboard mismatch: expected {len(expected)} chars, got {len(last)}", file=sys.stderr, flush=True)
    return False


async def _wait_linux_modifiers_released() -> None:
    try:
        import evdev
    except Exception:
        await asyncio.sleep(0.18)
        return

    deadline = asyncio.get_running_loop().time() + LINUX_MODIFIER_TIMEOUT
    while asyncio.get_running_loop().time() < deadline:
        active = False
        for path in evdev.list_devices():
            try:
                device = evdev.InputDevice(path)
                # ydotool reports its own synthetic modifier state. Waiting
                # on it makes a completed physical hold look permanently held.
                if "ydotool" in device.name.lower() or "virtual" in device.name.lower():
                    device.close()
                    continue
                keys = set(device.active_keys())
                device.close()
            except Exception:
                continue
            if keys & LINUX_MODIFIERS:
                active = True
                break
        if not active:
            return
        await asyncio.sleep(0.025)
    print("[WARN] modifiers still pressed before paste", file=sys.stderr, flush=True)


async def _release_linux_virtual_modifiers() -> None:
    if not shutil.which("ydotool"):
        return
    await _run_ydotool_key(LINUX_MODIFIER_RELEASES)


async def _ydotool_type(text: str) -> bool:
    if not shutil.which("ydotool"):
        print("[WARN] ydotool not found; skip direct type", file=sys.stderr, flush=True)
        return False
    env = _ydotool_env()
    proc = await asyncio.create_subprocess_exec(
        "ydotool",
        "type",
        "--key-delay=1",
        "--key-hold=1",
        "--file",
        "-",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    _, stderr = await proc.communicate(text.encode("utf-8"))
    if proc.returncode == 0:
        return True
    detail = stderr.decode("utf-8", errors="replace").strip()
    print(f"[WARN] ydotool type failed ({proc.returncode}): {detail}", file=sys.stderr, flush=True)
    return False


def _linux_combo_keycodes(combo: str) -> list[str]:
    if combo == "shift-insert":
        return [f"{KEY_LEFTSHIFT}:1", f"{KEY_INSERT}:1", f"{KEY_INSERT}:0", f"{KEY_LEFTSHIFT}:0"]
    if combo == "ctrl-shift-v":
        return [f"{KEY_LEFTCTRL}:1", f"{KEY_LEFTSHIFT}:1", f"{KEY_V}:1", f"{KEY_V}:0", f"{KEY_LEFTSHIFT}:0", f"{KEY_LEFTCTRL}:0"]
    return [f"{KEY_LEFTCTRL}:1", f"{KEY_V}:1", f"{KEY_V}:0", f"{KEY_LEFTCTRL}:0"]


async def _ydotool_paste(combo: str) -> bool:
    return await _run_ydotool_key(_linux_combo_keycodes(combo))


async def _run_ydotool_key(keycodes: list[str]) -> bool:
    env = _ydotool_env()
    try:
        proc = await asyncio.create_subprocess_exec(
            "ydotool",
            "key",
            "-d",
            LINUX_KEY_DELAY_MS,
            *keycodes,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError:
        print("[WARN] ydotool not found; skip key injection", file=sys.stderr, flush=True)
        return False
    stderr = await proc.stderr.read() if proc.stderr else b""
    code = await proc.wait()
    if code == 0:
        return True
    detail = stderr.decode("utf-8", errors="replace").strip()
    print(f"[WARN] ydotool key failed ({code}): {detail}", file=sys.stderr, flush=True)
    return False


def _ydotool_env() -> dict[str, str]:
    env = os.environ.copy()
    socket = f"/run/user/{os.getuid()}/.ydotool_socket"
    if os.path.exists(socket):
        env.setdefault("YDOTOOL_SOCKET", socket)
    return env


async def _wtype_paste(combo: str) -> bool:
    if combo == "shift-insert":
        args = ["wtype", "-M", "shift", "-k", "Insert", "-m", "shift"]
    else:
        args = ["wtype", "-M", "ctrl"]
        if combo == "ctrl-shift-v":
            args += ["-M", "shift", "v", "-m", "shift"]
        else:
            args += ["v"]
        args += ["-m", "ctrl"]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    stderr = await proc.stderr.read() if proc.stderr else b""
    code = await proc.wait()
    if code == 0:
        return True
    detail = stderr.decode("utf-8", errors="replace").strip()
    print(f"[WARN] wtype paste failed ({code}): {detail}", file=sys.stderr, flush=True)
    return False


async def _paste_windows(text: str, combo: str) -> bool:
    if not await _copy_windows(text):
        return False
    await _wait_windows_modifiers_released()
    ok = await asyncio.to_thread(_send_windows_combo, combo)
    if ok:
        print(f"[PASTE] sent {combo} via SendInput", file=sys.stderr, flush=True)
    return ok


async def _copy_windows(text: str) -> bool:
    ok = await asyncio.to_thread(_set_windows_clipboard_text, text)
    if ok:
        print("[PASTE] clipboard set", file=sys.stderr, flush=True)
    return ok


def _set_windows_clipboard_text(text: str) -> bool:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    data = (text + "\0").encode("utf-16le")

    for _ in range(20):
        if user32.OpenClipboard(None):
            break
        import time

        time.sleep(0.03)
    else:
        print("[WARN] OpenClipboard failed", file=sys.stderr, flush=True)
        return False

    handle = None
    try:
        if not user32.EmptyClipboard():
            print("[WARN] EmptyClipboard failed", file=sys.stderr, flush=True)
            return False
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            print("[WARN] GlobalAlloc failed", file=sys.stderr, flush=True)
            return False
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            kernel32.GlobalFree(handle)
            print("[WARN] GlobalLock failed", file=sys.stderr, flush=True)
            return False
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            print("[WARN] SetClipboardData failed", file=sys.stderr, flush=True)
            return False
        handle = None
        return True
    finally:
        user32.CloseClipboard()
        if handle:
            kernel32.GlobalFree(handle)


async def _wait_windows_modifiers_released() -> None:
    if os.name != "nt":
        return
    import ctypes

    user32 = ctypes.windll.user32
    modifiers = [0x10, 0x11, 0x12, 0x5B, 0x5C]
    deadline = asyncio.get_running_loop().time() + 1.0
    while asyncio.get_running_loop().time() < deadline:
        if not any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in modifiers):
            return
        await asyncio.sleep(0.025)
    print("[WARN] Windows modifiers still pressed before paste", file=sys.stderr, flush=True)


def _send_windows_combo(combo: str) -> bool:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    ULONG_PTR = wintypes.WPARAM
    KEYEVENTF_KEYUP = 0x0002
    VK_CONTROL = 0x11
    VK_SHIFT = 0x10
    VK_V = 0x56

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("union", INPUTUNION)]

    def key(vk: int, flags: int = 0) -> INPUT:
        item = INPUT()
        item.type = 1
        item.union.ki = KEYBDINPUT(vk, 0, flags, 0, 0)
        return item

    combo = (combo or "ctrl-v").lower().replace("+", "-")
    events = [key(VK_CONTROL)]
    if combo == "ctrl-shift-v":
        events.append(key(VK_SHIFT))
    events += [key(VK_V), key(VK_V, KEYEVENTF_KEYUP)]
    if combo == "ctrl-shift-v":
        events.append(key(VK_SHIFT, KEYEVENTF_KEYUP))
    events.append(key(VK_CONTROL, KEYEVENTF_KEYUP))

    array_type = INPUT * len(events)
    inputs = array_type(*events)
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    sent = user32.SendInput(len(events), inputs, ctypes.sizeof(INPUT))
    if sent != len(events):
        print(f"[WARN] SendInput sent {sent}/{len(events)} events", file=sys.stderr, flush=True)
        return False
    return True
