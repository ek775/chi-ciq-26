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
    with pytest.raises(ValueError, match="Missing meeting metadata"):
        pipeline.main(["--input-dir", str(tmp_path)])
    assert calls == []
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
