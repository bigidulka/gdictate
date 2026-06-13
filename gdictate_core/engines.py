from __future__ import annotations

from typing import Callable, Protocol

from .chrome import SpeechProxy
from .models import TranscriptResult


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
    raise ValueError(f"unsupported speech engine: {engine}")
