from __future__ import annotations

import asyncio
import json
import os
import secrets
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import aiohttp
from aiohttp import web

from .app import Dictation
from .constants import CONTROL_PORT, VERSION
from .file_jobs import FileJobManager, FileTranscriptionOptions
from .models import AppEvent


CONTROL_TOKEN_ENV = "GDICTATE_CONTROL_TOKEN"
CONTROL_TOKEN_KEY = web.AppKey("control_token", str)
IPC_VERSION = 2


def _control_token_file() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))).expanduser()
    return config_home / "gdictate" / "control-token"
IPC_VERSION = 2


def control_token() -> str:
    """Load/create per-user IPC bearer token without exposing it in settings."""
    value = os.environ.get(CONTROL_TOKEN_ENV, "").strip()
    if value:
        return value
    token_file = _control_token_file()
    try:
        value = token_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        token_file.parent.mkdir(parents=True, exist_ok=True)
        value = secrets.token_urlsafe(32)
        try:
            fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            value = token_file.read_text(encoding="utf-8").strip()
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
    if not value:
        raise RuntimeError(f"empty gdictate control token: {token_file}")
    if len(value) < 32 or any(char.isspace() for char in value):
        raise RuntimeError(f"invalid gdictate control token: {token_file}")
    return value


def _authorization_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {control_token()}"}


@web.middleware
async def _authorize_control(request: web.Request, handler):
    candidate = request.headers.get("Authorization", "")
    token = candidate[7:] if candidate.startswith("Bearer ") else ""
    if request.path == "/events" and not token:
        protocols = [part.strip() for part in request.headers.get("Sec-WebSocket-Protocol", "").split(",")]
        if len(protocols) == 2 and protocols[0] == "gdictate":
            token = protocols[1]
    if not token or not secrets.compare_digest(token, request.app[CONTROL_TOKEN_KEY]):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    return await handler(request)


class ControlServer:
    def __init__(self, dictation: Dictation, host: str = "127.0.0.1", port: int = CONTROL_PORT):
        self.dictation = dictation
        self.host = host
        self.port = port
        self._runner: Optional[web.AppRunner] = None
        self._shutdown = asyncio.Event()
        self._events: deque[dict] = deque(maxlen=100)
        self._sockets: set[web.WebSocketResponse] = set()
        self.file_jobs = FileJobManager(self._emit_raw)

    def on_event(self, event: AppEvent) -> None:
        data = {"ipc_version": IPC_VERSION, "type": event.type, **event.payload}
        self._emit_raw(data)

    def _emit_raw(self, data: dict) -> None:
        event_type = data.get("type", "")
        if event_type not in ("transcript.interim", "transcript.final"):
            self._events.append(_history_safe(data))
        for ws in list(self._sockets):
            if ws.closed:
                self._sockets.discard(ws)
                continue
            asyncio.create_task(ws.send_json(data))

    async def start(self) -> None:
        app = web.Application(middlewares=[_authorize_control])
        app[CONTROL_TOKEN_KEY] = control_token()
        app.router.add_get("/status", self._status)
        app.router.add_post("/start", self._start)
        app.router.add_post("/stop", self._stop)
        app.router.add_post("/toggle", self._toggle)
        app.router.add_get("/file-jobs", self._file_jobs)
        app.router.add_post("/file-jobs", self._file_job_start)
        app.router.add_get(r"/file-jobs/{job_id}", self._file_job_status)
        app.router.add_post(r"/file-jobs/{job_id}/cancel", self._file_job_cancel)
        app.router.add_post("/shutdown", self._shutdown_handler)
        app.router.add_get("/events", self._events_handler)

        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        print(f"[IPC] http://{self.host}:{self.port}", flush=True)

    async def wait_closed(self) -> None:
        await self._shutdown.wait()

    def request_shutdown(self) -> None:
        self._shutdown.set()

    async def close(self) -> None:
        for ws in list(self._sockets):
            await ws.close()
        self._sockets.clear()
        if self._runner:
            await self._runner.cleanup()
        await self.file_jobs.close()

    async def _json(self, request: web.Request) -> dict:
        if not request.can_read_body:
            return {}
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    async def _status(self, _request: web.Request) -> web.Response:
        return web.json_response(
            {"ok": True, "version": VERSION, "ipc_version": IPC_VERSION, **self.dictation.status()}
        )

    async def _start(self, request: web.Request) -> web.Response:
        data = await self._json(request)
        source = data.get("source")
        if source not in (None, "mic", "speakers", "both"):
            return web.json_response({"ok": False, "error": "invalid source"}, status=400)
        await self.dictation.start_recording(source)
        return web.json_response({"ok": True, "ipc_version": IPC_VERSION, **self.dictation.status()})

    async def _stop(self, _request: web.Request) -> web.Response:
        text = await self.dictation.stop_recording()
        return web.json_response(
            {"ok": True, "ipc_version": IPC_VERSION, "text_length": len(text), **self.dictation.status()}
        )

    async def _toggle(self, request: web.Request) -> web.Response:
        data = await self._json(request)
        source = data.get("source")
        if source not in (None, "mic", "speakers", "both"):
            return web.json_response({"ok": False, "error": "invalid source"}, status=400)
        await self.dictation.toggle(source)
        return web.json_response({"ok": True, "ipc_version": IPC_VERSION, **self.dictation.status()})

    async def _file_jobs(self, _request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "jobs": [asdict(job) for job in self.file_jobs.list()]})

    async def _file_job_start(self, request: web.Request) -> web.Response:
        data = await self._json(request)
        path = str(data.get("path") or "").strip()
        if not path:
            return web.json_response({"ok": False, "error": "path required"}, status=400)
        formats = data.get("formats") or ["json", "txt", "srt", "vtt"]
        if not isinstance(formats, list):
            formats = ["json", "txt", "srt", "vtt"]
        job = await self.file_jobs.start(
            FileTranscriptionOptions(
                path=path,
                output_dir=data.get("output_dir") or None,
                language=data.get("language") or self.dictation.language,
                model_size=data.get("model_size") or "small",
                device=data.get("device") or "auto",
                compute_type=data.get("compute_type") or "default",
                diarize=bool(data.get("diarize")),
                diarization_backend=data.get("diarization_backend") or data.get("diarizationBackend") or "auto",
                formats=[str(fmt) for fmt in formats],
            )
        )
        return web.json_response({"ok": True, "job": asdict(job)})

    async def _file_job_status(self, request: web.Request) -> web.Response:
        job = self.file_jobs.get(request.match_info["job_id"])
        if not job:
            return web.json_response({"ok": False, "error": "job not found"}, status=404)
        return web.json_response({"ok": True, "job": asdict(job)})

    async def _file_job_cancel(self, request: web.Request) -> web.Response:
        job = self.file_jobs.cancel(request.match_info["job_id"])
        if not job:
            return web.json_response({"ok": False, "error": "job not found"}, status=404)
        return web.json_response({"ok": True, "job": asdict(job)})

    async def _shutdown_handler(self, _request: web.Request) -> web.Response:
        self._shutdown.set()
        return web.json_response({"ok": True})

    async def _events_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(protocols=("gdictate",))
        await ws.prepare(request)
        self._sockets.add(ws)
        for event in self._events:
            await ws.send_json(event)
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.ERROR:
                    break
        finally:
            self._sockets.discard(ws)
        return ws


def _history_safe(value):
    if isinstance(value, dict):
        return {key: _history_safe(item) for key, item in value.items() if key not in {"text", "segments"}}
    if isinstance(value, list):
        return [_history_safe(item) for item in value]
    return value


async def post_control(
    path: str,
    payload: Optional[dict] = None,
    port: int = CONTROL_PORT,
    timeout_seconds: float = 3.0,
) -> dict:
    url = f"http://127.0.0.1:{port}{path}"
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout, headers=_authorization_headers()) as session:
        async with session.post(url, json=payload or {}) as response:
            data = await response.json()
            if response.status >= 400:
                raise RuntimeError(data.get("error", f"HTTP {response.status}"))
            return data


async def get_status(port: int = CONTROL_PORT) -> dict:
    return await get_control("/status", port=port)


async def get_control(path: str, port: int = CONTROL_PORT) -> dict:
    url = f"http://127.0.0.1:{port}/status"
    if path != "/status":
        url = f"http://127.0.0.1:{port}{path}"
    timeout = aiohttp.ClientTimeout(total=2)
    async with aiohttp.ClientSession(timeout=timeout, headers=_authorization_headers()) as session:
        async with session.get(url) as response:
            data = await response.json()
            if response.status >= 400:
                raise RuntimeError(data.get("error", f"HTTP {response.status}"))
            return data
