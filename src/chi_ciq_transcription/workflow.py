import logging
import math
import subprocess
import wave
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

import numpy as np

from .backends import (
    SAMPLE_RATE,
    WHISPER_WINDOW_SECONDS,
    Audio,
    Diarizer,
    SpeakerTurn,
    TextEmbedder,
    TranscribedWord,
    Transcriber,
    VoiceEmbedder,
)
from .database import TranscriptStore
from .metadata import parse_filename
from .models import TranscriptSegment
from .progress import timed_operation

logger = logging.getLogger(__name__)


def align_words(
    words: list[TranscribedWord],
    turns: list[SpeakerTurn],
    offset: int,
    frames: int,
) -> Iterator[tuple[SpeakerTurn, str]]:
    speaker = ""
    first = last = 0
    text: list[str] = []
    previous = 0.0
    for word in words:
        if not (
            math.isfinite(word.start)
            and math.isfinite(word.end)
            and previous <= word.start <= word.end
            and word.end <= frames / SAMPLE_RATE + 1 / SAMPLE_RATE
        ):
            raise ValueError(f"Whisper returned invalid word timestamps: {word}")
        if not isinstance(word.text, str) or not word.text.strip():
            raise ValueError("Whisper returned an empty or non-string word")
        previous = word.start
        # Whisper can assign zero-duration words; retain at least one audio sample.
        start = offset + min(round(word.start * SAMPLE_RATE), frames - 1)
        end = offset + min(
            max(round(word.end * SAMPLE_RATE), start - offset + 1), frames
        )
        overlaps: dict[str, float] = {}
        for turn in turns:
            overlap = min(end / SAMPLE_RATE, turn.end) - max(
                start / SAMPLE_RATE, turn.start
            )
            if overlap > 0:
                overlaps[turn.speaker] = overlaps.get(turn.speaker, 0.0) + overlap
        assigned = max(overlaps, key=overlaps.__getitem__) if overlaps else "UNKNOWN"
        if text and assigned != speaker:
            yield (
                SpeakerTurn(first / SAMPLE_RATE, last / SAMPLE_RATE, speaker),
                ("".join(text).strip()),
            )
            text = []
        if not text:
            first = start
            last = end
            speaker = assigned
        last = max(last, end)
        text.append(word.text)
    if text:
        yield (
            SpeakerTurn(first / SAMPLE_RATE, last / SAMPLE_RATE, speaker),
            ("".join(text).strip()),
        )


def extract_audio(source: Path, destination: Path) -> None:
    command = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        str(destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"FFmpeg audio extraction failed: {result.stderr.strip()}")


def read_audio(audio_file: wave.Wave_read, start: int, end: int) -> Audio:
    audio_file.setpos(start)
    pcm = audio_file.readframes(end - start)
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768


class TranscriptionWorkflow:
    def __init__(
        self,
        store: TranscriptStore,
        diarizer: Diarizer,
        transcriber: Transcriber,
        text_embedder: TextEmbedder,
        voice_embedder: VoiceEmbedder,
        *,
        max_chunk_seconds: float = WHISPER_WINDOW_SECONDS,
    ):
        if not (
            math.isfinite(max_chunk_seconds)
            and 1 / SAMPLE_RATE <= max_chunk_seconds <= WHISPER_WINDOW_SECONDS
        ):
            raise ValueError(
                "max_chunk_seconds must be finite, at least one audio sample, "
                f"and at most {WHISPER_WINDOW_SECONDS} seconds"
            )
        self.store = store
        self.diarizer = diarizer
        self.transcriber = transcriber
        self.text_embedder = text_embedder
        self.voice_embedder = voice_embedder
        self.max_chunk_frames = int(max_chunk_seconds * SAMPLE_RATE)

    def transcribe(self, source: str | Path) -> Iterator[TranscriptSegment]:
        """Commit each segment before yielding it; close the iterator if stopping early."""
        source = Path(source)
        metadata = parse_filename(source)
        if not source.is_file():
            raise FileNotFoundError(source)
        meeting_id = self.store.start(
            source,
            metadata,
            transcription_model=self.transcriber.model_name,
            diarization_model=self.diarizer.model_name,
            text_embedding_model=self.text_embedder.model_name,
            voice_embedding_model=self.voice_embedder.model_name,
        )
        completed = False
        logger.info("Meeting %s: %s", meeting_id, source.name)
        try:
            with TemporaryDirectory(prefix="chi-ciq-") as directory:
                audio_path = Path(directory) / "audio.wav"
                with timed_operation(f"Meeting {meeting_id}: FFmpeg extraction"):
                    extract_audio(source, audio_path)
                with wave.open(str(audio_path), "rb") as audio_file:
                    frame_count = self._validate_audio(meeting_id, audio_file)
                    turns = self._diarize(meeting_id, audio_path, frame_count)
                    yield from self._transcribe_audio(
                        meeting_id, audio_file, frame_count, turns
                    )
            completed = True
        finally:
            self.store.finish(meeting_id, completed=completed)
            logger.info(
                "Meeting %s: status=%s",
                meeting_id,
                "completed" if completed else "incomplete",
            )

    def _validate_audio(self, meeting_id: UUID, audio_file: wave.Wave_read) -> int:
        if (
            audio_file.getframerate() != SAMPLE_RATE
            or audio_file.getnchannels() != 1
            or audio_file.getsampwidth() != 2
        ):
            raise ValueError("Expected mono, 16 kHz, signed 16-bit PCM audio")
        frame_count = audio_file.getnframes()
        self.store.set_recording_duration(meeting_id, frame_count / SAMPLE_RATE)
        logger.info(
            "Meeting %s: recording duration %.2fs; chunk limit %.2fs",
            meeting_id,
            frame_count / SAMPLE_RATE,
            self.max_chunk_frames / SAMPLE_RATE,
        )
        return frame_count

    def _diarize(
        self, meeting_id: UUID, audio_path: Path, frame_count: int
    ) -> list[SpeakerTurn]:
        with timed_operation(f"Meeting {meeting_id}: full-file diarization"):
            turns = list(self.diarizer.diarize(str(audio_path)))
        logger.info("Meeting %s: %d speaker turns", meeting_id, len(turns))
        for turn in turns:
            if not (
                math.isfinite(turn.start)
                and math.isfinite(turn.end)
                and 0 <= turn.start < turn.end
                and turn.end <= frame_count / SAMPLE_RATE + 1 / SAMPLE_RATE
            ):
                raise ValueError(f"Diarizer returned invalid timestamps: {turn}")
            if not turn.speaker.strip():
                raise ValueError("Diarizer returned an empty speaker label")
        return sorted(turns, key=lambda turn: (turn.start, turn.end, turn.speaker))

    def _speech_windows(
        self, frame_count: int, turns: list[SpeakerTurn]
    ) -> Iterator[tuple[int, int, list[SpeakerTurn]]]:
        cursor = 0
        active: list[SpeakerTurn] = []
        for start in range(0, frame_count, self.max_chunk_frames):
            end = min(start + self.max_chunk_frames, frame_count)
            active = [turn for turn in active if turn.end > start / SAMPLE_RATE]
            while cursor < len(turns) and turns[cursor].start < end / SAMPLE_RATE:
                if turns[cursor].end > start / SAMPLE_RATE:
                    active.append(turns[cursor])
                cursor += 1
            if active:
                yield start, end, active

    def _transcribe_audio(
        self,
        meeting_id: UUID,
        audio_file: wave.Wave_read,
        frame_count: int,
        turns: list[SpeakerTurn],
    ) -> Iterator[TranscriptSegment]:
        sequence = 0
        for start, end, active in self._speech_windows(frame_count, turns):
            chunk = read_audio(audio_file, start, end)
            label = (
                f"Meeting {meeting_id}: window {start // self.max_chunk_frames + 1} "
                f"[{start / SAMPLE_RATE:.3f}-{end / SAMPLE_RATE:.3f}s]"
            )
            with timed_operation(f"{label}: Whisper"):
                words = self.transcriber.transcribe_words(chunk)
            if not words:
                logger.warning(
                    "%s: Whisper returned no words; no segment stored", label
                )
                continue
            for turn, transcript in align_words(words, active, start, end - start):
                segment = self._store_segment(
                    meeting_id, audio_file, sequence, turn, transcript
                )
                sequence += 1
                yield segment

    def _store_segment(
        self,
        meeting_id: UUID,
        audio_file: wave.Wave_read,
        sequence: int,
        turn: SpeakerTurn,
        transcript: str,
    ) -> TranscriptSegment:
        if turn.speaker == "UNKNOWN":
            logger.warning(
                "Meeting %s: no diarization overlap for words at %.3f-%.3fs; "
                "assigning UNKNOWN",
                meeting_id,
                turn.start,
                turn.end,
            )
        chunk = read_audio(
            audio_file, round(turn.start * SAMPLE_RATE), round(turn.end * SAMPLE_RATE)
        )
        label = (
            f"Meeting {meeting_id}: chunk {sequence + 1} "
            f"{turn.speaker} [{turn.start:.3f}-{turn.end:.3f}s]"
        )
        with timed_operation(f"{label}: text embedding"):
            text_embedding = self.text_embedder.embed(transcript)
        with timed_operation(f"{label}: voice embedding"):
            voice_embedding = self.voice_embedder.embed(chunk)
        with timed_operation(f"{label}: database commit"):
            return self.store.append(
                meeting_id,
                sequence=sequence,
                speaker=turn.speaker,
                start=turn.start,
                end=turn.end,
                transcript=transcript,
                text_embedding=text_embedding,
                voice_embedding=voice_embedding,
            )
