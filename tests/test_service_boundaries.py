import subprocess
import sys


def test_database_and_web_do_not_import_inference():
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import databases.meetings.schema; "
                "import databases.documents.schema; import web.app; "
                "assert not {'pipelines.transcription', 'torch', 'scipy', "
                "'pyannote', 'whisper', 'sentence_transformers'} "
                "& sys.modules.keys()"
            ),
        ],
        check=True,
    )


def test_pipeline_uses_the_meetings_database_contract():
    from databases.meetings.models import Meeting as SharedMeeting
    from databases.meetings.store import TranscriptStore as SharedStore
    from pipelines.transcription import Meeting, TranscriptStore
    from pipelines.transcription.database import TranscriptStore as LegacyStore
    from pipelines.transcription.models import Meeting as LegacyMeeting

    assert TranscriptStore is LegacyStore is SharedStore
    assert Meeting is LegacyMeeting is SharedMeeting


def test_dispatcher_does_not_load_inference_dependencies():
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import pipelines.transcription.dispatch; "
                "assert not {'torch', 'scipy', 'pyannote', 'whisper'} "
                "& sys.modules.keys()"
            ),
        ],
        check=True,
    )


def test_source_layout_matches_container_boundaries():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src"
    for path in (
        "pipelines/transcription",
        "pipelines/downloads",
        "pipelines/documents",
        "pipelines/analytics",
        "web",
        "vllm",
        "databases/meetings",
        "databases/documents",
    ):
        assert (root / path / "__init__.py").is_file()
    assert not (root / "chi_ciq").exists()
    assert not (root / "transcription").exists()
