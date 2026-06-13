from __future__ import annotations

import asyncio
import json
import ssl
import subprocess
from pathlib import Path
from typing import Optional

import aiohttp
from aiohttp import web

from .constants import CERT_DIR, CHROME_PROFILE, PROJECT_DIR, WS_PORT
from .models import TranscriptResult
from .platforms import find_chrome


def chrome_profile_dir(profile_dir: str = ""):
    return Path(profile_dir).expanduser() if profile_dir else CHROME_PROFILE


def is_browser_configured(profile_dir: str = "") -> bool:
    prefs_file = chrome_profile_dir(profile_dir) / "Default" / "Preferences"
    if not prefs_file.exists():
        return False
    try:
        with prefs_file.open("r", encoding="utf-8") as f:
            prefs = json.load(f)
        mic = prefs.get("profile", {}).get("content_settings", {}).get("exceptions", {}).get("media_stream_mic", {})
        return any(v.get("setting") == 1 for k, v in mic.items() if "localhost" in k)
    except (json.JSONDecodeError, KeyError):
        return False


def ensure_ssl() -> ssl.SSLContext:
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
    def __init__(
        self,
        language: str = "ru-RU",
        setup_mode: bool = False,
        debug: bool = False,
        hidden: bool = True,
        profile_dir: str = "",
        channel: str = "auto",
    ):
        self.language = language
        self.setup_mode = setup_mode
        self._debug = debug
        self.hidden = hidden
        self.profile_dir = profile_dir
        self.channel = channel
        self._ws: Optional[web.WebSocketResponse] = None
        self._runner: Optional[web.AppRunner] = None
        self._chrome_proc: Optional[asyncio.subprocess.Process] = None
        self._on_transcript = None
        self._ready = asyncio.Event()
        self._started = asyncio.Event()
        self._launch_lock = asyncio.Lock()

    async def start(self, on_transcript=None) -> None:
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
                await self._handle_message(data)
            elif msg.type == aiohttp.WSMsgType.ERROR:
                break

        self._ws = None
        self._ready.clear()
        return ws

    async def _handle_message(self, data: dict) -> None:
        msg_type = data.get("type")
        if self._debug:
            print(f"  [WS] {data}", flush=True)

        if msg_type == "ready":
            return
        if msg_type == "started":
            self._started.set()
            print("[SPEECH] Recognition started", flush=True)
            return
        if msg_type == "stopped":
            print("[SPEECH] Recognition stopped", flush=True)
            return
        if msg_type == "final" and self._on_transcript:
            self._on_transcript(TranscriptResult(text=data["text"], is_final=True, confidence=data.get("confidence", 0.0)))
            return
        if msg_type == "interim" and self._on_transcript:
            self._on_transcript(TranscriptResult(text=data["text"], is_final=False))
            return
        if msg_type == "error":
            self._print_error(data.get("error", ""))
            return
        if msg_type == "debug" and self._debug:
            print(f"  [JS] {data.get('msg', '')}", flush=True)

    def _print_error(self, err: str) -> None:
        if err == "not-allowed":
            if self.setup_mode:
                print("[!] Click 'Start Listening' in Chrome, then allow mic", flush=True)
            else:
                print("[ERR] Permission denied. Run with --setup first", flush=True)
        elif err == "no-speech":
            if self._debug:
                print("[SPEECH] no-speech (mic silent?)", flush=True)
        elif err == "aborted":
            if self._debug:
                print("[SPEECH] aborted", flush=True)
        else:
            print(f"[SPEECH] error: {err}", flush=True)

    async def _launch_chrome(self) -> None:
        chrome = find_chrome(self.channel)
        url = f"https://localhost:{WS_PORT}/speech-proxy.html?lang={self.language}"
        profile = chrome_profile_dir(self.profile_dir)
        profile.mkdir(parents=True, exist_ok=True)

        args = [
            chrome,
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--disable-gpu",
            "--disable-software-rasterizer",
            "--no-sandbox",
            "--autoplay-policy=no-user-gesture-required",
            "--ignore-certificate-errors",
            "--enable-features=WebSpeechAPI",
            "--disable-extensions",
            "--disable-translate",
            "--disable-sync",
            "--disable-background-networking",
            "--disable-default-apps",
            "--disable-component-update",
            "--disable-domain-reliability",
            "--disable-features=OptimizationHints,Translate,MediaRouter,DialMediaRouteProvider,InterestFeedContentSuggestions,GlobalMediaControls,AutofillServerCommunication",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            "--no-default-browser-check",
            "--disable-client-side-phishing-detection",
            "--disable-breakpad",
            "--disable-crash-reporter",
            "--disable-logging",
            "--disable-notifications",
            "--disable-hang-monitor",
            "--disable-popup-blocking",
            "--disable-prompt-on-repost",
            "--hide-scrollbars",
            "--mute-audio",
            "--noerrdialogs",
            "--password-store=basic",
            "--metrics-recording-only",
            "--no-pings",
            "--disk-cache-size=1",
            "--media-cache-size=1",
            "--class=gdictate",
            "--renderer-process-limit=1",
            "--in-process-gpu",
            "--disable-site-isolation-trials",
            "--js-flags=--max-old-space-size=128",
        ]

        if self.setup_mode or not self.hidden:
            args += ["--window-size=600,400", url]
        else:
            args += [f"--app={url}", "--window-size=1,1", "--window-position=32000,32000"]

        stderr = asyncio.subprocess.PIPE if self._debug else asyncio.subprocess.DEVNULL
        self._chrome_proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL, stderr=stderr)
        if self._debug:
            asyncio.create_task(self._read_chrome_stderr())

    async def _stop_chrome_process(self) -> None:
        if not self._chrome_proc:
            return
        if self._chrome_proc.returncode is None:
            self._chrome_proc.terminate()
            try:
                await asyncio.wait_for(self._chrome_proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                self._chrome_proc.kill()
                await self._chrome_proc.wait()
        self._chrome_proc = None

    async def _ensure_connected(self) -> None:
        if self._ws and not self._ws.closed:
            return
        async with self._launch_lock:
            if self._ws and not self._ws.closed:
                return
            self._ready.clear()
            self._started.clear()
            await self._stop_chrome_process()
            await self._launch_chrome()
            await self.wait_ready(timeout=12.0)

    async def _read_chrome_stderr(self) -> None:
        if not self._chrome_proc or not self._chrome_proc.stderr:
            return
        while True:
            line = await self._chrome_proc.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if text and any(term in text.lower() for term in ("speech", "audio", "mic", "permission")):
                print(f"  [CHROME] {text}", flush=True)

    async def wait_ready(self, timeout: float = 15.0) -> None:
        await asyncio.wait_for(self._ready.wait(), timeout=timeout)

    async def wait_started(self, timeout: float = 1.0) -> None:
        await asyncio.wait_for(self._started.wait(), timeout=timeout)

    async def start_recognition(self) -> None:
        self._started.clear()
        await self._ensure_connected()
        if self._ws and not self._ws.closed:
            await self._ws.send_json({"type": "start", "lang": self.language})

    async def stop_recognition(self) -> None:
        if self._ws and not self._ws.closed:
            await self._ws.send_json({"type": "stop"})

    async def close(self) -> None:
        if self._ws and not self._ws.closed:
            await self._ws.close()
        await self._stop_chrome_process()
        if self._runner:
            await self._runner.cleanup()
