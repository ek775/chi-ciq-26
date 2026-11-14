from .backends import (
    DiarizedChunk,
    PyannoteDiarizer,
    PyannoteVoiceEmbedder,
    SentenceTextEmbedder,
    SpeakerTurn,
    TranscribedWord,
    WhisperTranscriber,
)
from .database import TranscriptStore, create_database
from .metadata import MeetingMetadata, parse_filename
from .models import Meeting, Speaker, TranscriptSegment
from .workflow import TranscriptionWorkflow


def __getattr__(name: str):
    if name == "StreamingPyannoteDiarizer":
        from .streaming import StreamingPyannoteDiarizer

        return StreamingPyannoteDiarizer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DiarizedChunk",
    "Meeting",
    "MeetingMetadata",
    "PyannoteDiarizer",
    "PyannoteVoiceEmbedder",
    "SentenceTextEmbedder",
    "Speaker",
    "SpeakerTurn",
    "StreamingPyannoteDiarizer",
    "TranscribedWord",
    "TranscriptSegment",
    "TranscriptStore",
    "TranscriptionWorkflow",
    "WhisperTranscriber",
    "create_database",
    "parse_filename",
]
