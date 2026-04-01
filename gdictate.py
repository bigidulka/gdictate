#!/usr/bin/env python3
"""
Google Speech Streaming Dictation via Chrome webkitSpeechRecognition.

Just run: python gdictate.py
First launch auto-detects missing browser permission and opens setup.
After that: Chrome runs hidden, hotkey toggles dictation, text is pasted.

Architecture:
  Python daemon ↔ WebSocket ↔ Chrome (webkitSpeechRecognition) → Google servers
  evdev hotkey toggles recording, result is pasted via wl-copy + ydotool.
"""

import asyncio
import json
import os
import shutil
import signal
import subprocess
import ssl
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

import aiohttp
from aiohttp import web

try:
    from overlay import OverlayPopup, DictationTray

    HAS_UI = True
except ImportError:
    HAS_UI = False

WS_PORT = 9876
PROJECT_DIR = Path(__file__).parent
CERT_DIR = PROJECT_DIR / ".certs"
CHROME_PROFILE = Path.home() / ".cache" / "gdictate-chrome"
CHROME_PATHS = [
    "/usr/bin/google-chrome-stable",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]


KWIN_RULES_FILE = Path.home() / ".config" / "kwinrulesrc"
KWIN_RULE_DESCRIPTION = "gdictate-hide-chrome"


def ensure_kwin_rule():
    """Create a KWin rule to hide gdictate Chrome from taskbar/pager/switcher.
    Silently skips if not running KDE/KWin."""
    if not KWIN_RULES_FILE.exists() and not shutil.which("qdbus6"):
        return  # Not KDE

    import configparser

    config = configparser.ConfigParser()
    config.read(KWIN_RULES_FILE)

    # Check if rule already exists
    for section in config.sections():
        if section == "General":
            continue
        if config.get(section, "Description", fallback="") == KWIN_RULE_DESCRIPTION:
            return  # already set up

    import uuid

    rule_id = str(uuid.uuid4())
    count = config.getint("General", "count", fallback=0) + 1
    rules = config.get("General", "rules", fallback="")
    rules = f"{rules},{rule_id}" if rules else rule_id

    if not config.has_section("General"):
        config.add_section("General")
    config.set("General", "count", str(count))
    config.set("General", "rules", rules)

    config.add_section(rule_id)
    config.set(rule_id, "Description", KWIN_RULE_DESCRIPTION)
    config.set(rule_id, "wmclass", "chrome-localhost__speech-proxy")
    config.set(rule_id, "wmclassmatch", "2")  # substring match
    config.set(rule_id, "skiptaskbar", "true")
    config.set(rule_id, "skiptaskbarrule", "2")  # Force
    config.set(rule_id, "skippager", "true")
    config.set(rule_id, "skippagerrule", "2")
    config.set(rule_id, "skipswitcher", "true")
    config.set(rule_id, "skipswitcherrule", "2")
    config.set(rule_id, "minimize", "true")
    config.set(rule_id, "minimizerule", "2")  # Force

    with open(KWIN_RULES_FILE, "w") as f:
        config.write(f)

    subprocess.run(
        ["qdbus6", "org.kde.KWin", "/KWin", "reconfigure"],
        capture_output=True,
    )
    print("[KWIN] Window rule installed — Chrome hidden from taskbar/Alt+Tab", flush=True)


class State(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    FINALIZING = "finalizing"


@dataclass
class TranscriptResult:
    text: str
    is_final: bool
    confidence: float = 0.0


def _pkg_hint(name: str) -> str:
    """Return distro-appropriate install hint."""
    if os.path.isfile("/etc/arch-release"):
        return f"sudo pacman -S {name}"
    if os.path.isfile("/etc/fedora-release"):
        return f"sudo dnf install {name}"
    if os.path.isfile("/etc/debian_version"):
        return f"sudo apt install {name}"
    return f"install '{name}' via your package manager"


def check_dependencies():
    """Check that required system tools are available. Auto-fix what we can."""
    missing = []
    if not any(os.path.isfile(p) for p in CHROME_PATHS):
        missing.append(("Chrome/Chromium", _pkg_hint("chromium")))
    if not shutil.which("wl-copy"):
        missing.append(("wl-clipboard", _pkg_hint("wl-clipboard")))
    if not shutil.which("ydotool"):
        missing.append(("ydotool", _pkg_hint("ydotool")))
    if missing:
        print("\033[1;31mMissing dependencies:\033[0m", file=sys.stderr)
        for name, cmd in missing:
            print(f"  {name}: {cmd}", file=sys.stderr)
        sys.exit(1)
    # Auto-start ydotoold if not running
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "ydotoold.service"],
            capture_output=True, text=True,
        )
        if result.stdout.strip() != "active":
            print("[INIT] Starting ydotoold...", flush=True)
            subprocess.run(
                ["systemctl", "--user", "enable", "--now", "ydotoold.service"],
                capture_output=True,
            )
    except FileNotFoundError:
        pass


def is_browser_configured() -> bool:
    """Check if Chrome profile has microphone permission for localhost."""
    prefs_file = CHROME_PROFILE / "Default" / "Preferences"
    if not prefs_file.exists():
        return False
    try:
        with open(prefs_file) as f:
            prefs = json.load(f)
        mic = (
            prefs.get("profile", {})
            .get("content_settings", {})
            .get("exceptions", {})
            .get("media_stream_mic", {})
        )
        # setting=1 means "allow"
        return any(
            v.get("setting") == 1
            for k, v in mic.items()
            if "localhost" in k
        )
    except (json.JSONDecodeError, KeyError):
        return False


def ensure_microphone():
    """Find a real microphone and set it as PulseAudio/PipeWire default source."""
    try:
        result = subprocess.run(
            ["pactl", "list", "sources"],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        return

    # Parse sources: find real input devices (not monitors)
    sources = []
    current = {}
    for line in result.stdout.splitlines():
        line = line.strip()
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

    # Filter: skip monitors, keep real inputs
    inputs = [s for s in sources if ".monitor" not in s.get("name", "")]
    if not inputs:
        print(
            "\033[0;33m[WARN]\033[0m No microphone found. "
            "Connect a mic and restart.",
            file=sys.stderr, flush=True,
        )
        return

    # Prefer: RUNNING > IDLE > SUSPENDED; skip loopback
    def score(s):
        name = s.get("name", "")
        state = s.get("state", "")
        # Deprioritize virtual loopback
        if "snd_aloop" in name:
            return -1
        if state == "RUNNING":
            return 3
        if state == "IDLE":
            return 2
        return 1

    best = max(inputs, key=score)
    name = best.get("name", "")
    desc = best.get("desc", name)

    # Check if already default
    try:
        cur = subprocess.run(
            ["pactl", "get-default-source"],
            capture_output=True, text=True,
        )
        if cur.stdout.strip() == name:
            print(f"[MIC] {desc}", flush=True)
            return
    except FileNotFoundError:
        pass

    subprocess.run(
        ["pactl", "set-default-source", name],
        capture_output=True,
    )
    print(f"[MIC] Set default: {desc}", flush=True)


def find_chrome() -> str:
    for p in CHROME_PATHS:
        if os.path.isfile(p):
            return p
    raise FileNotFoundError("Chrome not found")


def ensure_ssl():
    CERT_DIR.mkdir(exist_ok=True)
    cert = CERT_DIR / "cert.pem"
    key = CERT_DIR / "key.pem"
    if not cert.exists() or not key.exists():
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-keyout",
                str(key),
                "-out",
                str(cert),
                "-days",
                "3650",
                "-nodes",
                "-subj",
                "/CN=localhost",
            ],
            capture_output=True,
            check=True,
        )
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    return ctx


class SpeechProxy:
    def __init__(self, language: str = "ru-RU", setup_mode: bool = False, debug: bool = False):
        self.language = language
        self.setup_mode = setup_mode
        self._debug = debug
        self._ws: Optional[web.WebSocketResponse] = None
        self._runner: Optional[web.AppRunner] = None
        self._chrome_proc: Optional[asyncio.subprocess.Process] = None
        self._on_transcript = None
        self._ready = asyncio.Event()
        self._started = asyncio.Event()

    async def start(self, on_transcript=None):
        self._on_transcript = on_transcript

        ssl_ctx = ensure_ssl()

        app = web.Application()
        app.router.add_get("/ws", self._ws_handler)
        app.router.add_static("/", PROJECT_DIR, show_index=False)

        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", WS_PORT, ssl_context=ssl_ctx)
        await site.start()

        await self._launch_chrome()

    async def _ws_handler(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws = ws
        self._ready.set()

        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                t = data.get("type")

                if self._debug:
                    print(f"  [WS] {data}", flush=True)

                if t == "ready":
                    pass
                elif t == "started":
                    self._started.set()
                    print("[SPEECH] Recognition started", flush=True)
                elif t == "stopped":
                    print("[SPEECH] Recognition stopped", flush=True)
                elif t == "final":
                    if self._on_transcript:
                        self._on_transcript(
                            TranscriptResult(
                                text=data["text"],
                                is_final=True,
                                confidence=data.get("confidence", 0.0),
                            )
                        )
                elif t == "interim":
                    if self._on_transcript:
                        self._on_transcript(
                            TranscriptResult(
                                text=data["text"],
                                is_final=False,
                            )
                        )
                elif t == "error":
                    err = data.get("error", "")
                    if err == "not-allowed":
                        if self.setup_mode:
                            print(
                                "[!] Click 'Start Listening' in Chrome, then allow mic",
                                flush=True,
                            )
                        else:
                            print(
                                "[ERR] Permission denied. Run with --setup first",
                                file=sys.stderr,
                                flush=True,
                            )
                    elif err == "no-speech":
                        if self._debug:
                            print("[SPEECH] no-speech (mic silent?)", flush=True)
                    elif err == "aborted":
                        if self._debug:
                            print("[SPEECH] aborted", flush=True)
                    else:
                        print(f"[SPEECH] error: {err}", file=sys.stderr, flush=True)
                elif t == "pong":
                    pass
                elif t == "debug":
                    if self._debug:
                        print(f"  [JS] {data.get('msg', '')}", flush=True)
                else:
                    if self._debug:
                        print(f"  [WS] unknown: {data}", flush=True)
            elif msg.type == aiohttp.WSMsgType.ERROR:
                break

        self._ws = None
        self._ready.clear()
        return ws

    async def _launch_chrome(self):
        chrome = find_chrome()
        url = f"https://localhost:{WS_PORT}/speech-proxy.html?lang={self.language}"
        CHROME_PROFILE.mkdir(parents=True, exist_ok=True)

        args = [
            chrome,
            f"--user-data-dir={CHROME_PROFILE}",
            "--no-first-run",
            "--disable-gpu",
            "--disable-software-rasterizer",
            "--no-sandbox",
            "--autoplay-policy=no-user-gesture-required",
            "--ignore-certificate-errors",
            "--enable-features=WebSpeechAPI",
            # --- minimize resource usage ---
            "--disable-extensions",
            "--disable-translate",
            "--disable-sync",
            "--disable-background-networking",
            "--disable-default-apps",
            "--disable-component-update",
            "--disable-domain-reliability",
            "--disable-features=OptimizationHints,Translate,MediaRouter,DialMediaRouteProvider,InterestFeedContentSuggestions",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            "--no-default-browser-check",
            "--disable-client-side-phishing-detection",
            "--disable-hang-monitor",
            "--disable-popup-blocking",
            "--disable-prompt-on-repost",
            "--metrics-recording-only",
            "--no-pings",
            "--disk-cache-size=1",
            "--media-cache-size=1",
            "--class=gdictate",
            # --- reduce process/memory footprint ---
            "--renderer-process-limit=1",
            "--in-process-gpu",
            "--disable-site-isolation-trials",
            '--js-flags=--max-old-space-size=128',
        ]

        if self.setup_mode:
            args += ["--window-size=600,400", url]
        else:
            args += [f"--app={url}", "--window-size=1,1", "--window-position=32000,32000"]

        if self._debug:
            self._chrome_proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            asyncio.create_task(self._read_chrome_stderr())
        else:
            self._chrome_proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )

    async def _read_chrome_stderr(self):
        if not self._chrome_proc or not self._chrome_proc.stderr:
            return
        while True:
            line = await self._chrome_proc.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if text and (
                "speech" in text.lower()
                or "audio" in text.lower()
                or "mic" in text.lower()
                or "permission" in text.lower()
            ):
                print(f"  [CHROME] {text}", flush=True)

    async def wait_ready(self, timeout: float = 15.0):
        await asyncio.wait_for(self._ready.wait(), timeout=timeout)

    async def start_recognition(self):
        self._started.clear()
        if self._ws and not self._ws.closed:
            await self._ws.send_json({"type": "start", "lang": self.language})

    async def stop_recognition(self):
        if self._ws and not self._ws.closed:
            await self._ws.send_json({"type": "stop"})

    async def close(self):
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._chrome_proc:
            self._chrome_proc.terminate()
            try:
                await asyncio.wait_for(self._chrome_proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                self._chrome_proc.kill()
        if self._runner:
            await self._runner.cleanup()


class Dictation:
    def __init__(self, language: str = "ru-RU", paste_mode: str = "ydotool", debug: bool = False):
        self.language = language
        self.paste_mode = paste_mode
        self.debug = debug
        self.state = State.IDLE
        self.proxy: Optional[SpeechProxy] = None
        self._full_text = ""
        self.overlay: Optional["OverlayPopup"] = None
        self.tray: Optional["DictationTray"] = None

    def on_transcript(self, r: TranscriptResult):
        if r.is_final and r.text:
            conf = f" ({r.confidence:.0%})" if r.confidence > 0 else ""
            print(f"\r\033[K\033[1;32m+\033[0m {r.text}{conf}", flush=True)
        elif r.text:
            self._full_text = r.text
            print(f"\r\033[K\033[0;33m~\033[0m {r.text}", end="", flush=True)
            if self.overlay:
                self.overlay.show_interim(r.text)

    async def init(self, setup_mode: bool = False):
        self.proxy = SpeechProxy(self.language, setup_mode=setup_mode, debug=self.debug)
        await self.proxy.start(on_transcript=self.on_transcript)
        print("[CHROME] Connecting...", flush=True)
        await self.proxy.wait_ready()
        print("[CHROME] Ready", flush=True)

    async def start_recording(self):
        if self.state != State.IDLE:
            return
        self.state = State.RECORDING
        self._full_text = ""
        if self.tray:
            self.tray.set_state("recording")
        print("\033[1;31m● REC\033[0m  Говорите...", flush=True)
        await self.proxy.start_recognition()

    async def stop_recording(self) -> str:
        if self.state != State.RECORDING:
            return ""
        self.state = State.FINALIZING
        if self.tray:
            self.tray.set_state("finalizing")
        print(flush=True)
        await self.proxy.stop_recognition()
        await asyncio.sleep(0.3)

        text = self._full_text.strip()

        if text:
            await self._paste(text)
            print(f"\033[1;32m= {text}\033[0m", flush=True)
            if self.overlay:
                self.overlay.show_final(text)
        else:
            print("\033[0;33m! Нет текста\033[0m", flush=True)

        self.state = State.IDLE
        if self.tray:
            self.tray.set_state("idle")
        return text

    async def toggle(self):
        if self.state == State.IDLE:
            await self.start_recording()
        elif self.state == State.RECORDING:
            await self.stop_recording()

    async def _paste(self, text: str):
        proc = await asyncio.create_subprocess_exec(
            "wl-copy",
            text,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        await asyncio.sleep(0.05)
        # Ctrl+Shift+V — works in terminals and most apps
        proc = await asyncio.create_subprocess_exec(
            "ydotool",
            "key",
            "29:1",   # LCtrl down
            "42:1",   # LShift down
            "47:1",   # V down
            "47:0",   # V up
            "42:0",   # LShift up
            "29:0",   # LCtrl up
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

    async def close(self):
        if self.proxy:
            await self.proxy.close()


async def run_evdev(dictation: Dictation, key_combo: str):
    import evdev
    from evdev import ecodes

    KEY_MAP = {
        "CTRL": {ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL},
        "ALT": {ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT},
        "SUPER": {ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA},
        "SHIFT": {ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT},
    }

    grouped = {}
    for part in key_combo.upper().split("+"):
        p = part.strip()
        if p in KEY_MAP:
            grouped[p] = KEY_MAP[p]

    if not grouped:
        print("[ERR] Invalid hotkey", file=sys.stderr)
        return

    devices = [evdev.InputDevice(p) for p in evdev.list_devices()]
    kbs = [d for d in devices if ecodes.EV_KEY in d.capabilities()]
    if not kbs:
        print("[ERR] No keyboards found", file=sys.stderr)
        return

    print(f"[BIND] {key_combo} ({len(kbs)} kb)\n", flush=True)

    pressed = set()
    last = 0.0

    async def read(dev):
        nonlocal pressed, last
        async for ev in dev.async_read_loop():
            if ev.type != ecodes.EV_KEY:
                continue
            if ev.value == 1:
                pressed.add(ev.code)
            elif ev.value == 0:
                pressed.discard(ev.code)

            ok = all(any(k in pressed for k in ks) for ks in grouped.values())
            now = time.monotonic()
            if ok and ev.value == 1 and (now - last) > 0.3:
                last = now
                await dictation.toggle()

    tasks = [asyncio.create_task(read(kb)) for kb in kbs]
    await asyncio.gather(*tasks)


async def main(args, overlay=None, tray=None):
    ensure_kwin_rule()
    ensure_microphone()

    d = Dictation(language=args.lang, paste_mode=args.paste, debug=args.debug)
    d.overlay = overlay
    d.tray = tray

    ui_status = "on" if (overlay or tray) else "off"
    print("╔═══════════════════════════════════════╗", flush=True)
    print("║  Google Speech Streaming Dictation    ║", flush=True)
    print("╠═══════════════════════════════════════╣", flush=True)
    print(f"║  Lang: {args.lang:<30}║", flush=True)
    print(f"║  Key:  {args.key:<30}║", flush=True)
    print(f"║  UI:   {ui_status:<30}║", flush=True)
    print("╚═══════════════════════════════════════╝", flush=True)

    need_setup = args.setup or not is_browser_configured()
    if need_setup:
        print("\n[SETUP] First run — opening Chrome to grant microphone permission.", flush=True)
        print("        Click 'Allow' when prompted, then close Chrome.\n", flush=True)
        await d.init(setup_mode=True)
        print("[SETUP] Waiting for permission... (Ctrl+C when done)\n", flush=True)
        try:
            while not is_browser_configured():
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        await d.close()
        if is_browser_configured():
            print("\n[SETUP] Permission granted! Restarting in normal mode...\n", flush=True)
            # Re-init in normal (hidden) mode
            d = Dictation(language=args.lang, paste_mode=args.paste, debug=args.debug)
            d.overlay = overlay
            d.tray = tray
        else:
            print("\n[SETUP] Permission not detected. Run again to retry.", flush=True)
            return

    await d.init()

    if args.test:
        print("=== TEST: 5s ===", flush=True)
        await d.start_recording()
        await asyncio.sleep(5)
        await d.stop_recording()
        await d.close()
        return

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGINT, lambda: [t.cancel() for t in asyncio.all_tasks(loop)])
    loop.add_signal_handler(signal.SIGTERM, lambda: [t.cancel() for t in asyncio.all_tasks(loop)])

    try:
        await run_evdev(d, args.key)
    except asyncio.CancelledError:
        pass
    finally:
        await d.close()


def run():
    import argparse

    parser = argparse.ArgumentParser(description="Google Speech Dictation")
    parser.add_argument("--lang", default="ru-RU")
    parser.add_argument("--key", default="CTRL+ALT")
    parser.add_argument("--paste", default="ydotool", choices=["ydotool", "xdotool"])
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Force browser setup (auto-detected on first run)",
    )
    parser.add_argument("--test", action="store_true", help="5s test recording")
    parser.add_argument("--debug", action="store_true", help="Show all WS messages")
    parser.add_argument("--no-ui", action="store_true", help="Disable overlay/tray")
    args = parser.parse_args()

    check_dependencies()

    if HAS_UI and not args.no_ui:
        from PyQt6.QtWidgets import QApplication
        import qasync

        app = QApplication(sys.argv)
        overlay = OverlayPopup()
        tray = DictationTray()

        loop = qasync.QEventLoop(app)
        asyncio.set_event_loop(loop)
        with loop:
            loop.run_until_complete(main(args, overlay, tray))
    else:
        asyncio.run(main(args))


if __name__ == "__main__":
    run()
