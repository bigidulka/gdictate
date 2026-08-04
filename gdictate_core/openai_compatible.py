from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from .audio_capture import PipeWireCapture
from .models import TranscriptResult


DEFAULT_CHATGPT_ENDPOINT = "http://127.0.0.1:37182/v1/audio/transcriptions"


def endpoint_health_url(endpoint: str) -> str:
    """Return loopback bridge health URL for a /v1/audio/transcriptions endpoint."""
    parsed = urlsplit(endpoint)
    suffix = "/v1/audio/transcriptions"
    path = parsed.path
    if path.endswith(suffix):
        path = path[: -len(suffix)] or "/"
    return urlunsplit((parsed.scheme, parsed.netloc, path.rstrip("/") + "/health", "", ""))


class OpenAICompatibleSpeechEngine:
    """Record current PulseAudio/PipeWire source, then call OpenAI STT endpoint.

    OpenAI-compatible services return a final transcript only, unlike Chrome Web
    Speech's streaming interim events. The engine still implements same contract
    used by Dictation, so capture, paste, hotkeys and UI stay backend-agnostic.
    """

    name = "openai"

    def __init__(
        self,
        endpoint: str,
        language: str,
        model: str = "gpt-4o-transcribe",
        timeout_seconds: int = 120,
        api_key_env: str = "GDICTATE_TRANSCRIBER_API_KEY",
        debug: bool = False,
        health_check: bool = False,
    ):
        self.endpoint = endpoint.strip()
        self.language = language
        self.model = model.strip() or "gpt-4o-transcribe"
        self.timeout_seconds = max(5, int(timeout_seconds))
        self.api_key_env = api_key_env.strip()
        self.debug = debug
        self.health_check = health_check
        self._on_transcript = None
        self._started = asyncio.Event()
        self._capture: Optional[PipeWireCapture] = None
        self._audio_path: Optional[Path] = None
        self.capture_source: Optional[str] = None
        self.on_audio_path: Optional[Callable[[Optional[Path]], None]] = None

    async def start(self, on_transcript=None) -> None:
        if not self.endpoint:
            raise RuntimeError("OpenAI-compatible transcription endpoint is not configured")
        if not PipeWireCapture.available():
            raise RuntimeError("pw-record is required for native PipeWire dictation capture")
        self._on_transcript = on_transcript

    async def wait_ready(self, timeout: float = 15.0) -> None:
        if not self.health_check:
            return
        timeout_config = aiohttp.ClientTimeout(total=min(timeout, 5.0))
        try:
            async with aiohttp.ClientSession(timeout=timeout_config) as session:
                async with session.get(endpoint_health_url(self.endpoint)) as response:
                    if response.status >= 400:
                        raise RuntimeError(f"bridge health returned HTTP {response.status}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise RuntimeError(f"ChatGPT bridge unavailable at {endpoint_health_url(self.endpoint)}: {exc}") from exc

    async def wait_started(self, timeout: float = 1.0) -> None:
        await asyncio.wait_for(self._started.wait(), timeout=timeout)

    async def start_recognition(self) -> None:
        if self._capture and self._capture.process and self._capture.process.returncode is None:
            return
        self._started.clear()
        handle = tempfile.NamedTemporaryFile(prefix="gdictate-", suffix=".wav", delete=False)
        handle.close()
        self._audio_path = Path(handle.name)
        self._capture = PipeWireCapture(
            self._audio_path,
            self.capture_source,
            rate=48000,
            channels=1,
            debug=self.debug,
        )
        try:
            await self._capture.start()
        except Exception:
            self._capture = None
            await self._cleanup_audio()
            raise
        if self.on_audio_path:
            self.on_audio_path(self._audio_path)
        self._started.set()
        print(f"[PIPEWIRE] Recording {self.capture_source or '@DEFAULT_AUDIO_SOURCE@'}", flush=True)

    async def stop_recognition(self) -> None:
        capture, self._capture = self._capture, None
        if not capture:
            return
        await capture.stop()
        if not self._audio_path or not self._audio_path.exists() or self._audio_path.stat().st_size < 128:
            await self._cleanup_audio()
            raise RuntimeError("recorded audio is empty")
        try:
            text = await self._transcribe(self._audio_path)
        finally:
            await self._cleanup_audio()
        text = text.strip()
        if text and self._on_transcript:
            self._on_transcript(TranscriptResult(text=text, is_final=True))

    async def _transcribe(self, audio_path: Path) -> str:
        form = aiohttp.FormData()
        form.add_field("model", self.model)
        form.add_field("language", self.language)
        form.add_field("response_format", "json")
        headers = {}
        api_key = os.environ.get(self.api_key_env) if self.api_key_env else None
        parsed_endpoint = urlsplit(self.endpoint)
        if api_key and parsed_endpoint.scheme != "https" and parsed_endpoint.hostname not in ("127.0.0.1", "::1", "localhost"):
            raise RuntimeError("refusing to send transcription API key over non-loopback HTTP")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        timeout = aiohttp.ClientTimeout(total=float(self.timeout_seconds))
        try:
            audio = audio_path.open("rb")
        except OSError as exc:
            raise RuntimeError(f"cannot read recorded audio: {exc}") from exc
        try:
            form.add_field("file", audio, filename="speech.wav", content_type="audio/wav")
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(self.endpoint, data=form, headers=headers) as response:
                        body = await response.read()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                raise RuntimeError(f"transcription request failed: {exc}") from exc
        finally:
            audio.close()
        if response.status >= 400:
            detail = body.decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"transcription endpoint returned HTTP {response.status}: {detail}")
        try:
            result = __import__("json").loads(body)
        except ValueError as exc:
            raise RuntimeError("transcription endpoint returned invalid JSON") from exc
        text = result.get("text") if isinstance(result, dict) else None
        if not isinstance(text, str):
            raise RuntimeError("transcription endpoint response has no text field")
        return text.strip()

    async def _cleanup_audio(self) -> None:
        if self.on_audio_path:
            self.on_audio_path(None)
        path, self._audio_path = self._audio_path, None
        if path:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    async def close(self) -> None:
        capture, self._capture = self._capture, None
        if capture:
            await capture.close()
        await self._cleanup_audio()


class ChatGPTSpeechEngine(OpenAICompatibleSpeechEngine):
    name = "chatgpt"

    def __init__(self, endpoint: str = DEFAULT_CHATGPT_ENDPOINT, **kwargs):
        super().__init__(endpoint=endpoint, health_check=True, **kwargs)
