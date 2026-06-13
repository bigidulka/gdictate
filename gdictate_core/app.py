from __future__ import annotations

import asyncio
import sys
from typing import Callable, Optional

from .audio import AudioRouting, configure_audio_source
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
        paste_linux_combo: str = "ctrl-shift-v",
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
            conf = f" ({result.confidence:.0%})" if result.confidence > 0 else ""
            print(f"\r\033[K\033[1;32m+\033[0m {result.text}{conf}", flush=True)
            self.emit("transcript.final", text=result.text, confidence=result.confidence, channel=result.channel)
            if self.paste_live and self.paste_live_during_recording:
                self._queue_live_delta(final_text)
        elif result.text:
            self._full_text = result.text
            print(f"\r\033[K\033[0;33m~\033[0m {result.text}", end="", flush=True)
            self.emit("transcript.interim", text=result.text, channel=result.channel)
            if self.paste_live and self.paste_live_during_recording:
                self._queue_live_delta(result.text)
            if self.overlay:
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
        )
        await self.engine.start(on_transcript=self.on_transcript)
        print(f"[ENGINE] Connecting: {self.engine.name}", flush=True)
        await self.engine.wait_ready()
        print(f"[ENGINE] Ready: {self.engine.name}", flush=True)
        self.emit("engine.ready", engine=self.engine.name)

    async def start_recording(self, source: Optional[str] = None) -> None:
        if self.state != State.IDLE:
            return
        source = source or self.audio_source
        self._active_source = source
        self._audio_route.close()
        self._audio_route = configure_audio_source(source, self.audio_linux_router, self.audio_windows_speaker_input)
        self.state = State.RECORDING
        self._full_text = ""
        self._final_segments = []
        self._live_target_text = ""
        self._live_pasted_text = ""
        self._live_paste_failed = False
        if self.tray:
            self.tray.set_state("recording")
        label = {"mic": "я", "speakers": "собеседник", "both": "микрофон+динамики"}.get(source, source)
        print(f"\033[1;31m● REC\033[0m  {label}...", flush=True)
        self.emit("recording.started", channel=source)
        if not self.engine:
            return
        await self.engine.start_recognition()
        try:
            await self.engine.wait_started()
        except asyncio.TimeoutError:
            pass
        if self.restore_default_after_start:
            self._audio_route.restore_default_source()

    async def stop_recording(self) -> str:
        if self.state != State.RECORDING:
            return ""
        self.state = State.FINALIZING
        if self.tray:
            self.tray.set_state("finalizing")
        print(flush=True)
        if self.engine:
            await self.engine.stop_recognition()
        await asyncio.sleep(0.3)

        text = (self._full_text or " ".join(self._final_segments)).strip()
        if self.paste_live and self.paste_live_during_recording and text:
            self._queue_live_delta(text)
        await self._flush_live_paste()
        await asyncio.sleep(0.15)
        if text and (
            not self.paste_live
            or not self.paste_live_during_recording
            or self._live_paste_failed
            or not self._live_pasted_text.strip()
        ):
            await paste_text(text, self.paste_mode, self.paste_linux_combo, self.paste_windows_combo)
        if text:
            print(f"\033[1;32m= {text}\033[0m", flush=True)
            if self.overlay:
                self.overlay.show_final(text)
        else:
            print("\033[0;33m! Нет текста\033[0m", flush=True)

        self.state = State.IDLE
        if self.tray:
            self.tray.set_state("idle")
        self._audio_route.close()
        self._audio_route = AudioRouting()
        self.emit("recording.stopped", channel=self._active_source, text=text)
        return text

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
            "audio_source": self.audio_source,
            "audio_router": self._audio_route.router or self.audio_linux_router,
            "windows_speaker_input": self.audio_windows_speaker_input,
            "active_source": self._active_source,
            "paste_mode": self.paste_mode,
            "paste_live": self.paste_live,
            "paste_live_active": self.paste_live and self.paste_live_during_recording,
            "text": self._full_text,
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
            finally:
                self._paste_queue.task_done()

    async def _flush_live_paste(self) -> None:
        if self._paste_worker:
            await self._paste_queue.join()
            await self._paste_worker

    async def close(self) -> None:
        await self._flush_live_paste()
        self._audio_route.close()
        if self.engine:
            await self.engine.close()
