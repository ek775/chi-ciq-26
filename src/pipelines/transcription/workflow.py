import logging
import math
import subprocess
import wave
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from queue import Empty, Full, Queue
from tempfile import TemporaryDirectory
from threading import Event
from uuid import UUID

import numpy as np

from databases.meetings.metadata import MeetingMetadata

from .backends import (
    SAMPLE_RATE,
    Audio,
    DiarizedChunk,
    Diarizer,
    SpeakerTurn,
    TextEmbedder,
    TranscribedWord,
    Transcriber,
    VoiceEmbedder,
)
from .database import TranscriptStore
from .models import TranscriptSegment
from .progress import timed_operation

logger = logging.getLogger(__name__)
DEFAULT_STREAM_CHUNK_SECONDS = 60
DIARIZATION_BUFFER_CHUNKS = 2


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
        max_chunk_seconds: float | None = None,
    ):
        if max_chunk_seconds is not None and not (
            math.isfinite(max_chunk_seconds) and max_chunk_seconds >= 1 / SAMPLE_RATE
        ):
            raise ValueError(
                "max_chunk_seconds must be None or finite and at least one audio sample"
            )
        if not callable(getattr(diarizer, "diarize_chunks", None)):
            raise TypeError(
                "Workflow diarizers must implement diarize_chunks; use "
                "StreamingPyannoteDiarizer, not the offline PyannoteDiarizer"
            )
        self.store = store
        self.diarizer = diarizer
        self.transcriber = transcriber
        self.text_embedder = text_embedder
        self.voice_embedder = voice_embedder
        self.max_chunk_frames = (
            None if max_chunk_seconds is None else int(max_chunk_seconds * SAMPLE_RATE)
        )

    def transcribe(self, source: str | Path) -> Iterator[TranscriptSegment]:
        """Commit each segment before yielding it; close the iterator if stopping early."""
        source = Path(source)
        metadata = MeetingMetadata(source.name, None, None)
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
                    yield from self._transcribe_stream(
                        meeting_id, audio_path, audio_file, frame_count
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
            "Meeting %s: recording duration %.2fs; chunk limit %s",
            meeting_id,
            frame_count / SAMPLE_RATE,
            (
                f"{DEFAULT_STREAM_CHUNK_SECONDS}s (streaming default)"
                if self.max_chunk_frames is None
                else f"{self.max_chunk_frames / SAMPLE_RATE:.2f}s"
            ),
        )
        return frame_count

    def _validate_chunk(
        self, chunk: DiarizedChunk, expected_start: int, frame_count: int, limit: int
    ) -> None:
        if not (
            isinstance(chunk.start, int)
            and isinstance(chunk.end, int)
            and chunk.start == expected_start
            and chunk.start < chunk.end <= frame_count
            and chunk.end - chunk.start <= limit
        ):
            raise ValueError("Diarizer returned invalid or non-contiguous chunk bounds")
        for turn in chunk.turns:
            if not (
                math.isfinite(turn.start)
                and math.isfinite(turn.end)
                and chunk.start / SAMPLE_RATE <= turn.start < turn.end
                and turn.end <= chunk.end / SAMPLE_RATE + 1 / SAMPLE_RATE
            ):
                raise ValueError(f"Diarizer returned invalid timestamps: {turn}")
            if not turn.speaker.strip():
                raise ValueError("Diarizer returned an empty speaker label")
        chunk.turns.sort(key=lambda turn: (turn.start, turn.end, turn.speaker))

    def _transcribe_stream(
        self,
        meeting_id: UUID,
        audio_path: Path,
        audio_file: wave.Wave_read,
        frame_count: int,
    ) -> Iterator[TranscriptSegment]:
        chunk_frames = (
            self.max_chunk_frames or DEFAULT_STREAM_CHUNK_SECONDS * SAMPLE_RATE
        )
        queue: Queue[DiarizedChunk] = Queue(maxsize=DIARIZATION_BUFFER_CHUNKS)
        cancelled = Event()
        finished = Event()

        def produce() -> None:
            expected_start = 0
            try:
                with timed_operation(f"Meeting {meeting_id}: streaming diarization"):
                    chunks = iter(
                        self.diarizer.diarize_chunks(str(audio_path), chunk_frames)
                    )
                    try:
                        for chunk in chunks:
                            if cancelled.is_set():
                                return
                            self._validate_chunk(
                                chunk, expected_start, frame_count, chunk_frames
                            )
                            expected_start = chunk.end
                            while not cancelled.is_set():
                                try:
                                    queue.put(chunk, timeout=0.05)
                                    break
                                except Full:
                                    continue
                            if cancelled.is_set():
                                return
                        if expected_start != frame_count:
                            raise ValueError(
                                "Diarization stream ended before audio EOF"
                            )
                    finally:
                        close = getattr(chunks, "close", None)
                        if close is not None:
                            close()
            finally:
                finished.set()

        def consume() -> Iterator[DiarizedChunk]:
            while True:
                try:
                    yield queue.get(timeout=0.05)
                except Empty:
                    if finished.is_set():
                        # A final put can race with get's timeout.
                        if not queue.empty():
                            continue
                        producer.result()
                        return

        with ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="diarization"
        ) as executor:
            producer = executor.submit(produce)
            try:
                with closing(consume()) as chunks:
                    yield from self._transcribe_chunks(
                        meeting_id, audio_file, chunk_frames, chunks
                    )
            finally:
                cancelled.set()
                # Joining must precede deletion of the worker's audio file.
                executor.shutdown(wait=True, cancel_futures=True)
                error = None if producer.cancelled() else producer.exception()
                if error is not None:
                    logger.error(
                        "Meeting %s: diarization worker failed",
                        meeting_id,
                        exc_info=error,
                    )

    def _transcribe_chunks(
        self,
        meeting_id: UUID,
        audio_file: wave.Wave_read,
        chunk_frames: int,
        chunks: Iterator[DiarizedChunk],
    ) -> Iterator[TranscriptSegment]:
        sequence = 0
        for diarized in chunks:
            start, end, active = diarized.start, diarized.end, diarized.turns
            if not active:
                continue
            chunk = read_audio(audio_file, start, end)
            label = (
                f"Meeting {meeting_id}: window {start // chunk_frames + 1} "
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
                "Meeting %s: unresolved speaker for words at %.3f-%.3fs; "
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
