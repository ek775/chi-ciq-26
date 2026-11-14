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
