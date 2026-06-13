from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import asdict
from typing import Optional

import aiohttp
from aiohttp import web

from .app import Dictation
from .constants import CONTROL_PORT, VERSION
from .file_jobs import FileJobManager, FileTranscriptionOptions
from .models import AppEvent


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
        data = {"type": event.type, **event.payload}
        self._emit_raw(data)

    def _emit_raw(self, data: dict) -> None:
        self._events.append(data)
        for ws in list(self._sockets):
            if ws.closed:
                self._sockets.discard(ws)
                continue
            asyncio.create_task(ws.send_json(data))

    async def start(self) -> None:
        app = web.Application()
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
        return web.json_response({"ok": True, "version": VERSION, **self.dictation.status()})

    async def _start(self, request: web.Request) -> web.Response:
        data = await self._json(request)
        source = data.get("source")
        if source not in (None, "mic", "speakers", "both"):
            return web.json_response({"ok": False, "error": "invalid source"}, status=400)
        await self.dictation.start_recording(source)
        return web.json_response({"ok": True, **self.dictation.status()})

    async def _stop(self, _request: web.Request) -> web.Response:
        text = await self.dictation.stop_recording()
        return web.json_response({"ok": True, "text": text, **self.dictation.status()})

    async def _toggle(self, request: web.Request) -> web.Response:
        data = await self._json(request)
        source = data.get("source")
        if source not in (None, "mic", "speakers", "both"):
            return web.json_response({"ok": False, "error": "invalid source"}, status=400)
        await self.dictation.toggle(source)
        return web.json_response({"ok": True, **self.dictation.status()})

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
        ws = web.WebSocketResponse()
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


async def post_control(path: str, payload: Optional[dict] = None, port: int = CONTROL_PORT) -> dict:
    url = f"http://127.0.0.1:{port}{path}"
    timeout = aiohttp.ClientTimeout(total=3)
    async with aiohttp.ClientSession(timeout=timeout) as session:
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
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            data = await response.json()
            if response.status >= 400:
                raise RuntimeError(data.get("error", f"HTTP {response.status}"))
            return data
