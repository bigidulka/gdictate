from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys


async def paste_text(text: str, mode: str = "auto", linux_combo: str = "ctrl-shift-v", windows_combo: str = "ctrl-v") -> bool:
    if mode == "none":
        return False
    if os.name == "nt":
        return await _paste_windows(text, windows_combo)
    return await _paste_linux(text, mode, linux_combo)


async def _paste_linux(text: str, mode: str, combo: str) -> bool:
    if not shutil.which("wl-copy"):
        print("[WARN] wl-copy not found; skip paste", file=sys.stderr, flush=True)
        return False

    proc = await asyncio.create_subprocess_exec(
        "wl-copy",
        text,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    if await proc.wait() != 0:
        print("[WARN] wl-copy failed; skip paste", file=sys.stderr, flush=True)
        return False
    await asyncio.sleep(0.05)

    if mode in ("auto", "ydotool") and shutil.which("ydotool"):
        if await _ydotool_paste(combo):
            return True
        if mode == "ydotool":
            return False

    if mode in ("auto", "wtype") and shutil.which("wtype"):
        if await _wtype_paste(combo):
            return True

    print("[WARN] ydotool/wtype not found; text copied but not pasted", file=sys.stderr, flush=True)
    return False


def _linux_combo_keycodes(combo: str) -> list[str]:
    if combo == "ctrl-v":
        return ["29:1", "47:1", "47:0", "29:0"]
    return ["29:1", "42:1", "47:1", "47:0", "42:0", "29:0"]


async def _ydotool_paste(combo: str) -> bool:
    env = os.environ.copy()
    socket = f"/run/user/{os.getuid()}/.ydotool_socket"
    if os.path.exists(socket):
        env.setdefault("YDOTOOL_SOCKET", socket)
    proc = await asyncio.create_subprocess_exec(
        "ydotool",
        "key",
        *_linux_combo_keycodes(combo),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stderr = await proc.stderr.read() if proc.stderr else b""
    code = await proc.wait()
    if code == 0:
        return True
    detail = stderr.decode("utf-8", errors="replace").strip()
    print(f"[WARN] ydotool paste failed ({code}): {detail}", file=sys.stderr, flush=True)
    return False


async def _wtype_paste(combo: str) -> bool:
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
    escaped = text.replace("'", "''")
    proc = await asyncio.create_subprocess_exec(
        "powershell",
        "-NoProfile",
        "-Command",
        f"Set-Clipboard -Value '{escaped}'",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    if await proc.wait() != 0:
        return False
    await asyncio.sleep(0.05)
    _send_windows_combo(combo)
    return True


def _send_windows_combo(combo: str) -> None:
    import ctypes

    user32 = ctypes.windll.user32
    KEYEVENTF_KEYUP = 0x0002
    VK_CONTROL = 0x11
    VK_V = 0x56
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_V, 0, 0, 0)
    user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
