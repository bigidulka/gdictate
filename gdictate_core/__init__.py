"""gdictate core package."""

from .constants import VERSION
from .models import State, TranscriptResult

__all__ = ["VERSION", "State", "TranscriptResult", "Dictation"]


def __getattr__(name: str):
    """Preserve the public Dictation export without eager heavyweight imports."""
    if name == "Dictation":
        from .app import Dictation

        return Dictation
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
