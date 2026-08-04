from __future__ import annotations

import asyncio
import math
import struct
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

from .audio import AudioRouting, configure_audio_source, get_default_source
from .engines import SpeechEngine, create_engine
from .models import AppEvent, State, TranscriptResult
from .paste import paste_text


EventHandler = Callable[[AppEvent], None]


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


_configure_stdio()


class Dictation:
    def __init__(
        self,
        language: str = "ru-RU",
        paste_mode: str = "auto",
        paste_live: bool = True,
        paste_live_during_recording: bool = True,
        paste_linux_combo: str = "shift-insert",
        paste_windows_combo: str = "ctrl-v",
        audio_source: str = "mic",
        engine: str = "chrome",
        debug: bool = False,
        restore_default_after_start: bool = True,
        audio_linux_router: str = "pipewire-pulse",
        audio_windows_speaker_input: str = "auto",
        chrome_channel: str = "auto",
        chrome_hidden: bool = True,
        chrome_profile_dir: str = "",
        transcriber_endpoint: str = "",
        transcriber_model: str = "gpt-4o-transcribe",
        transcriber_timeout_seconds: int = 120,
        transcriber_api_key_env: str = "GDICTATE_TRANSCRIBER_API_KEY",
        on_event: Optional[EventHandler] = None,
    ):
        self.language = language
        self.paste_mode = paste_mode
        self.paste_live = paste_live
        self.paste_live_during_recording = paste_live_during_recording
        self.paste_linux_combo = paste_linux_combo
        self.paste_windows_combo = paste_windows_combo
        self.audio_source = audio_source
        self.engine_name = engine
        self.debug = debug
        self.restore_default_after_start = restore_default_after_start
        self.audio_linux_router = audio_linux_router
        self.audio_windows_speaker_input = audio_windows_speaker_input
        self.chrome_channel = chrome_channel
        self.chrome_hidden = chrome_hidden
        self.chrome_profile_dir = chrome_profile_dir
        self.transcriber_endpoint = transcriber_endpoint
        self.transcriber_model = transcriber_model
        self.transcriber_timeout_seconds = transcriber_timeout_seconds
        self.transcriber_api_key_env = transcriber_api_key_env
        self.on_event = on_event
        self.state = State.IDLE
        self.engine: Optional[SpeechEngine] = None
        self._full_text = ""
        self._final_segments: list[str] = []
        self._active_source = audio_source
        self._audio_route = AudioRouting()
        self._paste_queue: asyncio.Queue[str] = asyncio.Queue()
        self._paste_worker: Optional[asyncio.Task] = None
        self._live_target_text = ""
        self._live_pasted_text = ""
        self._live_paste_failed = False
        self._level_stop = threading.Event()
        self._level_thread: Optional[threading.Thread] = None
        self._level_audio_path: Optional[Path] = None
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        self._operation_lock = asyncio.Lock()
        self.overlay = None
        self.tray = None

    def emit(self, event_type: str, **payload) -> None:
        payload.setdefault("state", self.state.value)
        payload.setdefault("active_source", self._active_source)
        if self.on_event:
            self.on_event(AppEvent(event_type, payload))

    def on_transcript(self, result: TranscriptResult) -> None:
        result.channel = self._active_source
        if result.is_final and result.text:
            self._final_segments.append(result.text)
            final_text = " ".join(self._final_segments)
            if final_text:
                self._full_text = final_text
            if self.debug:
                print(
                    f"[DEBUG] final transcript: {len(result.text)} chars; confidence={result.confidence:.3f}",
                    flush=True,
                )
            self.emit("transcript.final", text=result.text, confidence=result.confidence, channel=result.channel)
            if self.paste_live and self.paste_live_during_recording:
                self._queue_live_delta(final_text)
            if self.overlay and self.state == State.RECORDING:
                self.overlay.show_interim(final_text)
        elif result.text:
            self._full_text = result.text
            if self.debug:
                print(f"[DEBUG] interim transcript: {len(result.text)} chars", flush=True)
            self.emit("transcript.interim", text=result.text, channel=result.channel)
            if self.paste_live and self.paste_live_during_recording:
                self._queue_live_delta(result.text)
            if self.overlay and self.state == State.RECORDING:
                self.overlay.show_interim(result.text)

    async def init(self, setup_mode: bool = False) -> None:
        self.engine = create_engine(
            self.engine_name,
            self.language,
            setup_mode=setup_mode,
            debug=self.debug,
            chrome_channel=self.chrome_channel,
            chrome_hidden=self.chrome_hidden,
            chrome_profile_dir=self.chrome_profile_dir,
            transcriber_endpoint=self.transcriber_endpoint,
            transcriber_model=self.transcriber_model,
            transcriber_timeout_seconds=self.transcriber_timeout_seconds,
            transcriber_api_key_env=self.transcriber_api_key_env,
        )
        await self.engine.start(on_transcript=self.on_transcript)
        if hasattr(self.engine, "on_audio_path"):
            self.engine.on_audio_path = self._set_level_audio_path
        print(f"[ENGINE] Connecting: {self.engine.name}", flush=True)
        await self.engine.wait_ready()
        print(f"[ENGINE] Ready: {self.engine.name}", flush=True)
        self.emit("engine.ready", engine=self.engine.name)

    async def start_recording(self, source: Optional[str] = None) -> None:
        async with self._operation_lock:
            if self.state != State.IDLE:
                return
            source = source or self.audio_source
            self._active_source = source
            try:
                self._audio_route.close()
                self._audio_route = configure_audio_source(
                    source,
                    self.audio_linux_router,
                    self.audio_windows_speaker_input,
                    change_default=self.engine_name == "chrome",
                )
            except Exception as exc:
                await self._recording_failed(f"audio routing failed: {exc}")
                return
            level_source = self._audio_route.active_source or get_default_source()
            if self.engine_name in ("chatgpt", "openai") and not level_source:
                await self._recording_failed(f"no capture source available for {source}")
                return
            if self.engine and hasattr(self.engine, "capture_source"):
                self.engine.capture_source = level_source
            self._full_text = ""
            self._final_segments = []
            self._live_target_text = ""
            self._live_pasted_text = ""
            self._live_paste_failed = False
            label = {"mic": "я", "speakers": "собеседник", "both": "микрофон+динамики"}.get(source, source)
            try:
                if not self.engine:
                    raise RuntimeError("speech engine is not ready")
                await self.engine.start_recognition()
                await self.engine.wait_started()
            except (RuntimeError, asyncio.TimeoutError) as exc:
                restart_error = None
                if self.engine:
                    try:
                        await self.engine.close()
                        await self.engine.start(on_transcript=self.on_transcript)
                        if hasattr(self.engine, "on_audio_path"):
                            self.engine.on_audio_path = self._set_level_audio_path
                    except Exception as restart_exc:
                        restart_error = restart_exc
                detail = f"recording start failed: {exc}"
                if restart_error:
                    detail += f"; engine reset failed: {restart_error}"
                await self._recording_failed(detail)
                return
            self.state = State.RECORDING
            if self.tray:
                self.tray.set_state("recording")
            if self.overlay:
                self.overlay.show_recording_start(label)
            print(f"\033[1;31m● REC\033[0m  {label}...", flush=True)
            self.emit("recording.started", channel=source)
            if self.restore_default_after_start:
                self._audio_route.restore_default_source()

    async def stop_recording(self) -> str:
        async with self._operation_lock:
            if self.state != State.RECORDING:
                return ""
            self.state = State.FINALIZING
            if self.tray:
                self.tray.set_state("finalizing")
            self._stop_level_events()
            if self.overlay:
                self.overlay.show_transcribing()
            print("[OPENAI] Transcribing audio", flush=True)
            self.emit("transcription.started", channel=self._active_source)
            error = ""
            text = ""
            paste_ok = False
            overlay_hidden_for_paste = False
            try:
                if self.engine:
                    await self.engine.stop_recognition()
                text = (self._full_text or " ".join(self._final_segments)).strip()
                if self.paste_live and self.paste_live_during_recording and text:
                    self._queue_live_delta(text)
                await self._flush_live_paste()
                if text and (
                    not self.paste_live
                    or not self.paste_live_during_recording
                    or self._live_paste_failed
                    or not self._live_pasted_text.strip()
                ):
                    if self.overlay:
                        self.overlay.hide_popup()
                        overlay_hidden_for_paste = True
                        await asyncio.sleep(0.12)
                    paste_ok = await paste_text(text, self.paste_mode, self.paste_linux_combo, self.paste_windows_combo)
                elif text:
                    paste_ok = True
            except asyncio.CancelledError:
                error = "operation cancelled"
                raise
            except Exception as exc:
                error = str(exc)
                print(f"[ERR] {self.engine_name} transcription failed: {error}", file=sys.stderr, flush=True)
                self.emit("engine.error", engine=self.engine_name, error=error)
            finally:
                if self.debug:
                    print(f"[DEBUG] completed transcript: {len(text)} chars", flush=True)
                elif text:
                    print(f"[DONE] {len(text)} chars", flush=True)
                else:
                    print("[DONE] no transcript", flush=True)
                if self.overlay:
                    if error:
                        self.overlay.show_error(error)
                        await asyncio.sleep(1.8)
                        self.overlay.hide_popup()
                    elif text and not paste_ok and self.paste_mode not in ("copy", "none"):
                        self.overlay.show_error("Текст скопирован, но вставка не выполнена")
                        await asyncio.sleep(1.8)
                        self.overlay.hide_popup()
                    elif not overlay_hidden_for_paste:
                        self.overlay.hide_popup()
                self.state = State.IDLE
                if self.tray:
                    self.tray.set_state("idle")
                self._audio_route.close()
                self._audio_route = AudioRouting()
                text_length = len(text)
                self.emit(
                    "recording.stopped",
                    channel=self._active_source,
                    text_length=text_length,
                    error=error,
                    paste_ok=paste_ok,
                )
                self._clear_transcript_buffers()
            return text

    def _clear_transcript_buffers(self) -> None:
        self._full_text = ""
        self._final_segments.clear()
        self._live_target_text = ""
        self._live_pasted_text = ""
        self._live_paste_failed = False

    async def _recording_failed(self, error: str) -> None:
        print(f"[ERR] {error}", file=sys.stderr, flush=True)
        self.emit("engine.error", engine=self.engine_name, error=error)
        if self.overlay:
            self.overlay.show_error(error)
            await asyncio.sleep(1.8)
            self.overlay.hide_popup()
        self.state = State.IDLE
        self._clear_transcript_buffers()
        self._audio_route.close()
        self._audio_route = AudioRouting()

    def _set_level_audio_path(self, path: Optional[Path]) -> None:
        self._stop_level_events()
        self._level_audio_path = path
        if not path:
            return
        self._level_stop.clear()
        self._event_loop = asyncio.get_running_loop()
        self._level_thread = threading.Thread(target=self._read_level_events, daemon=True)
        self._level_thread.start()

    def _stop_level_events(self) -> None:
        self._level_stop.set()
        self._level_audio_path = None
        self._level_thread = None

    def _read_level_events(self) -> None:
        path = self._level_audio_path
        if not path:
            return
        offset = 44
        smooth = 0.0
        while not self._level_stop.wait(0.05):
            try:
                size = path.stat().st_size
                if size <= offset:
                    continue
                with path.open("rb") as audio:
                    audio.seek(offset)
                    chunk = audio.read(min(4800, size - offset))
            except OSError:
                continue
            if len(chunk) < 2:
                continue
            offset += len(chunk) - len(chunk) % 2
            samples = struct.unpack(f"<{len(chunk) // 2}h", chunk[: len(chunk) - len(chunk) % 2])
            if not samples:
                continue
            rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / 32768.0
            peak = max(abs(sample) for sample in samples) / 32768.0
            level = min(1.0, max(rms * 85.0, peak * 8.0))
            smooth = smooth * 0.55 + level * 0.45
            loop = self._event_loop
            if loop and loop.is_running():
                loop.call_soon_threadsafe(lambda value=smooth: self._publish_level(value))

    def _publish_level(self, level: float) -> None:
        self.emit("audio.level", level=level)
        if self.overlay and self.state == State.RECORDING:
            self.overlay.show_level(level)

    async def toggle(self, source: Optional[str] = None) -> None:
        if self.state == State.IDLE:
            await self.start_recording(source)
        elif self.state == State.RECORDING:
            await self.stop_recording()

    def status(self) -> dict:
        return {
            "state": self.state.value,
            "language": self.language,
            "engine": self.engine_name,
            "chrome_channel": self.chrome_channel,
            "transcriber_endpoint": self.transcriber_endpoint,
            "audio_source": self.audio_source,
            "audio_router": self._audio_route.router or self.audio_linux_router,
            "windows_speaker_input": self.audio_windows_speaker_input,
            "active_source": self._active_source,
            "paste_mode": self.paste_mode,
            "paste_live": self.paste_live,
            "paste_live_active": self.paste_live and self.paste_live_during_recording,
            "text_length": len(self._full_text),
        }

    def _queue_live_paste(self, text: str) -> None:
        chunk = text
        if not chunk:
            return
        self._paste_queue.put_nowait(chunk)
        if not self._paste_worker or self._paste_worker.done():
            self._paste_worker = asyncio.create_task(self._live_paste_worker())

    def _queue_live_delta(self, text: str) -> None:
        current = " ".join(text.split())
        if not current:
            return

        if len(current) <= len(self._live_target_text):
            return
        if not current.startswith(self._live_target_text):
            return
        suffix = current[len(self._live_target_text):]
        if suffix:
            self._live_target_text = current
            self._queue_live_paste(suffix)

    async def _live_paste_worker(self) -> None:
        while True:
            try:
                chunk = await asyncio.wait_for(self._paste_queue.get(), timeout=0.25)
            except asyncio.TimeoutError:
                return
            try:
                ok = await paste_text(chunk, self.paste_mode, self.paste_linux_combo, self.paste_windows_combo)
                if ok:
                    self._live_pasted_text += chunk
                else:
                    self._live_paste_failed = True
            except Exception as exc:
                self._live_paste_failed = True
                print(f"[WARN] live paste failed: {exc}", file=sys.stderr, flush=True)
            finally:
                self._paste_queue.task_done()

    async def _flush_live_paste(self) -> None:
        if self._paste_worker:
            await self._paste_queue.join()
            try:
                await self._paste_worker
            finally:
                self._paste_worker = None

    async def close(self) -> None:
        self._stop_level_events()
        await self._flush_live_paste()
        self._audio_route.close()
        if self.engine:
            await self.engine.close()
        self._clear_transcript_buffers()
        if self.overlay:
            close = getattr(self.overlay, "close", None)
            if close:
                close()
