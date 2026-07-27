from __future__ import annotations

from typing import Callable, Protocol

from .chrome import SpeechProxy
from .models import TranscriptResult
from .openai_compatible import ChatGPTSpeechEngine, OpenAICompatibleSpeechEngine


TranscriptHandler = Callable[[TranscriptResult], None]


class SpeechEngine(Protocol):
    name: str

    async def start(self, on_transcript: TranscriptHandler | None = None) -> None:
        ...

    async def wait_ready(self, timeout: float = 15.0) -> None:
        ...

    async def wait_started(self, timeout: float = 1.0) -> None:
        ...

    async def start_recognition(self) -> None:
        ...

    async def stop_recognition(self) -> None:
        ...

    async def close(self) -> None:
        ...


class ChromeSpeechEngine(SpeechProxy):
    name = "chrome"


def create_engine(
    engine: str,
    language: str,
    setup_mode: bool = False,
    debug: bool = False,
    chrome_channel: str = "auto",
    chrome_hidden: bool = True,
    chrome_profile_dir: str = "",
    transcriber_endpoint: str = "",
    transcriber_model: str = "gpt-4o-transcribe",
    transcriber_timeout_seconds: int = 120,
    transcriber_api_key_env: str = "GDICTATE_TRANSCRIBER_API_KEY",
) -> SpeechEngine:
    if engine == "chrome":
        return ChromeSpeechEngine(
            language,
            setup_mode=setup_mode,
            debug=debug,
            hidden=chrome_hidden,
            profile_dir=chrome_profile_dir,
            channel=chrome_channel,
        )
    common = {
        "language": language,
        "model": transcriber_model,
        "timeout_seconds": transcriber_timeout_seconds,
        "api_key_env": transcriber_api_key_env,
        "debug": debug,
    }
    if engine == "chatgpt":
        return ChatGPTSpeechEngine(endpoint=transcriber_endpoint or "http://127.0.0.1:37182/v1/audio/transcriptions", **common)
    if engine == "openai":
        return OpenAICompatibleSpeechEngine(endpoint=transcriber_endpoint, **common)
    raise ValueError(f"unsupported speech engine: {engine}")
