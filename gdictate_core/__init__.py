"""gdictate core package."""

from .constants import VERSION
from .models import State, TranscriptResult
from .app import Dictation

__all__ = ["VERSION", "State", "TranscriptResult", "Dictation"]
