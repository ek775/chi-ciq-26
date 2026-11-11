import math
import subprocess
import wave
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from .backends import SAMPLE_RATE, Diarizer, TextEmbedder, Transcriber, VoiceEmbedder
from .database import TranscriptStore
from .metadata import parse_filename
from .models import TranscriptSegment


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


class TranscriptionWorkflow:
    def __init__(
        self,
        store: TranscriptStore,
        diarizer: Diarizer,
        transcriber: Transcriber,
        text_embedder: TextEmbedder,
        voice_embedder: VoiceEmbedder,
        *,
        max_chunk_seconds: float = 20,
    ):
        if not math.isfinite(max_chunk_seconds) or max_chunk_seconds < 1 / SAMPLE_RATE:
            raise ValueError(
                "max_chunk_seconds must be finite and at least one audio sample"
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
        try:
            with TemporaryDirectory(prefix="chi-ciq-") as directory:
                audio_path = Path(directory) / "audio.wav"
                extract_audio(source, audio_path)
                with wave.open(str(audio_path), "rb") as audio_file:
                    if (
                        audio_file.getframerate() != SAMPLE_RATE
                        or audio_file.getnchannels() != 1
                        or audio_file.getsampwidth() != 2
                    ):
                        raise ValueError(
                            "Expected mono, 16 kHz, signed 16-bit PCM audio"
                        )
                    frame_count = audio_file.getnframes()
                    self.store.set_recording_duration(
                        meeting_id, frame_count / SAMPLE_RATE
                    )
                    turns = self.diarizer.diarize(str(audio_path))
                    sequence = 0
                    for turn in turns:
                        if not (
                            math.isfinite(turn.start)
                            and math.isfinite(turn.end)
                            and 0 <= turn.start < turn.end
                            and turn.end <= frame_count / SAMPLE_RATE + 1 / SAMPLE_RATE
                        ):
                            raise ValueError(
                                f"Diarizer returned invalid timestamps: {turn}"
                            )
                        first = round(turn.start * SAMPLE_RATE)
                        last = min(round(turn.end * SAMPLE_RATE), frame_count)
                        if first >= last:
                            raise ValueError(
                                f"Diarizer turn contains no audio samples: {turn}"
                            )
                        for start in range(first, last, self.max_chunk_frames):
                            end = min(start + self.max_chunk_frames, last)
                            audio_file.setpos(start)
                            pcm = audio_file.readframes(end - start)
                            chunk = (
                                np.frombuffer(pcm, dtype="<i2").astype(np.float32)
                                / 32768
                            )
                            transcript = self.transcriber.transcribe(chunk)
                            if not transcript.strip():
                                raise ValueError(
                                    f"Whisper returned empty text at {start / SAMPLE_RATE}s"
                                )
                            segment = self.store.append(
                                meeting_id,
                                sequence=sequence,
                                speaker=turn.speaker,
                                start=start / SAMPLE_RATE,
                                end=end / SAMPLE_RATE,
                                transcript=transcript,
                                text_embedding=self.text_embedder.embed(transcript),
                                voice_embedding=self.voice_embedder.embed(chunk),
                            )
                            sequence += 1
                            yield segment
            completed = True
        finally:
            self.store.finish(meeting_id, completed=completed)
