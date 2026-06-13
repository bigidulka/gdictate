from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class State(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    FINALIZING = "finalizing"


@dataclass
class TranscriptResult:
    text: str
    is_final: bool
    confidence: float = 0.0
    channel: str = "mic"


@dataclass
class AppEvent:
    type: str
    payload: dict
