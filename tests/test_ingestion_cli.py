import json
from types import SimpleNamespace

import pytest

from pipelines.transcription import pipeline


def test_directory_cli_defaults_overrides_and_stop_on_failure(tmp_path, monkeypatch):
    defaults = tmp_path / "default"
    override = tmp_path / "override"
    for folder in (defaults, override):
        folder.mkdir()
        for name in ("2026-11-10_budget.mp4", "2026-11-11_budget.mp4"):
            (folder / name).touch()
    monkeypatch.setattr(pipeline, "load_dotenv", lambda: None)
    monkeypatch.setenv("TRANSCRIPTION_INPUT_DIR", str(defaults))
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    disposed = []
    monkeypatch.setattr(
        pipeline,
        "connect_database",
        lambda url: SimpleNamespace(dispose=lambda: disposed.append(True)),
    )
    monkeypatch.setattr(pipeline, "check_schema", lambda engine: None)
    processed = []
    fail = False

    def ingest(recording, **kwargs):
        processed.append(recording)
        if fail:
            raise RuntimeError("inference failed")

    monkeypatch.setattr(
        pipeline,
        "IngestionPipeline",
        lambda engine, factory: SimpleNamespace(ingest=ingest),
    )
    pipeline.main([])
    pipeline.main(["--input-dir", str(override)])
    assert processed == sorted(defaults.iterdir()) + sorted(override.iterdir())
    assert disposed == [True, True]
    fail = True
    with pytest.raises(RuntimeError, match="inference failed"):
        pipeline.main([])
    assert processed[-1] == min(defaults.iterdir())
    assert len(processed) == 5
    assert disposed == [True, True, True]


def test_vimeo_directory_mapping_is_preflighted_and_passed_to_ingestion(
    tmp_path, monkeypatch
):
    source = tmp_path / "vimeo-1200176629-audio-low.mp4"
    source.touch()
    metadata_file = tmp_path / "metadata.json"
    monkeypatch.setattr(pipeline, "load_dotenv", lambda: None)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    calls = []
    monkeypatch.setattr(
        pipeline,
        "connect_database",
        lambda url: SimpleNamespace(dispose=lambda: None),
    )
    monkeypatch.setattr(pipeline, "check_schema", lambda engine: None)
    monkeypatch.setattr(
        pipeline,
        "IngestionPipeline",
        lambda engine, factory: SimpleNamespace(
            ingest=lambda recording, **kwargs: calls.append((recording, kwargs))
        ),
    )
    pipeline.main(["--input-dir", str(tmp_path)])
    assert calls[0][0] == source
    assert calls[0][1]["metadata_filename"] is None
    calls.clear()
    metadata_file.write_text(
        json.dumps({"1200176629": "2026-06-11_economic_development.mp4"})
    )
    pipeline.main(["--input-dir", str(tmp_path), "--metadata-file", str(metadata_file)])
    assert calls[0][0] == source
    assert calls[0][1]["metadata_filename"] == "2026-06-11_economic_development.mp4"
    pipeline.main(
        [
            "--input-file",
            str(source),
            "--meeting-filename",
            "2026-06-12_budget.mp4",
        ]
    )
    assert calls[1][1]["metadata_filename"] == "2026-06-12_budget.mp4"


def test_audio_map_is_passed_to_ingestion_by_exact_input_filename(tmp_path, monkeypatch):
    source = tmp_path / "vimeo-1200176629-low.mp4"
    source.touch()
    map_path = tmp_path / "audio-map.json"
    map_path.write_text(
        json.dumps(
            {
                "format_version": 2,
                "schema_mapping": {
                    "target": "databases.meetings.models.Meeting",
                    "fields": {
                        "meeting_date": "audio_files[].database_metadata.meeting_date",
                        "meeting_type": "audio_files[].database_metadata.meeting_type",
                    },
                    "unknown_value": None,
                },
                "audio_files": [
                    {
                        "audio_filename": source.name,
                        "database_metadata": {
                            "meeting_date": "2025-11-12",
                            "meeting_type": "Committee on Finance",
                        },
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(pipeline, "load_dotenv", lambda: None)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    calls = []
    monkeypatch.setattr(
        pipeline,
        "connect_database",
        lambda url: SimpleNamespace(dispose=lambda: None),
    )
    monkeypatch.setattr(pipeline, "check_schema", lambda engine: None)
    monkeypatch.setattr(
        pipeline,
        "IngestionPipeline",
        lambda engine, factory: SimpleNamespace(
            ingest=lambda recording, **kwargs: calls.append((recording, kwargs))
        ),
    )

    pipeline.main(
        ["--input-dir", str(tmp_path), "--audio-meeting-map", str(map_path)]
    )
    assert calls[0][0] == source
    metadata = calls[0][1]["meeting_metadata"]
    assert metadata.filename == source.name
    assert metadata.meeting_date.isoformat() == "2025-11-12"
    assert metadata.meeting_type == "Committee on Finance"


def test_directory_shards_process_every_recording_once(tmp_path, monkeypatch):
    for index in range(12):
        (tmp_path / f"2026-11-{index + 1:02}_budget.mp4").touch()
    monkeypatch.setattr(pipeline, "load_dotenv", lambda: None)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        pipeline,
        "connect_database",
        lambda url: SimpleNamespace(dispose=lambda: None),
    )
    monkeypatch.setattr(pipeline, "check_schema", lambda engine: None)
    processed = []
    monkeypatch.setattr(
        pipeline,
        "IngestionPipeline",
        lambda engine, factory: SimpleNamespace(
            ingest=lambda recording, **kwargs: processed.append(
                (recording, kwargs["namespace"], kwargs["metadata_filename"])
            )
        ),
    )
    for index in range(4):
        pipeline.main(
            [
                "--input-dir",
                str(tmp_path),
                "--shard-index",
                str(index),
                "--shard-count",
                "4",
            ]
        )
    assert len(processed) == 12
    assert {recording for recording, _, _ in processed} == set(tmp_path.iterdir())
    assert all(
        namespace == "recordings" and filename is None
        for recording, namespace, filename in processed
    )


@pytest.mark.parametrize(
    "arguments",
    [
        ["--shard-count", "0"],
        ["--shard-index", "-1"],
        ["--shard-count", "2", "--shard-index", "2"],
        ["--input-file", "2026-11-01_budget.mp4", "--shard-count", "2"],
    ],
)
def test_invalid_shards_fail_before_database_access(arguments, monkeypatch):
    monkeypatch.setattr(pipeline, "load_dotenv", lambda: None)
    monkeypatch.setattr(
        pipeline,
        "connect_database",
        lambda url: pytest.fail("Invalid sharding reached the database"),
    )
    with pytest.raises(SystemExit):
        pipeline.main(arguments)
