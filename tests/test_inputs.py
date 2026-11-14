import pytest

from pipelines.transcription.inputs import discover_recordings


def test_discovery_is_sorted_case_insensitive_and_nonrecursive(tmp_path):
    for name in ("2026-11-11_budget.mp4", "2026-11-10_budget.MP4", "notes.txt"):
        (tmp_path / name).touch()
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested/invalid.mp4").touch()
    assert [p.name for p in discover_recordings(tmp_path)] == [
        "2026-11-10_budget.MP4",
        "2026-11-11_budget.mp4",
    ]


def test_discovery_rejects_missing_empty_and_invalid_inputs(tmp_path):
    with pytest.raises(NotADirectoryError):
        discover_recordings(tmp_path / "missing")
    with pytest.raises(ValueError, match="No MP4"):
        discover_recordings(tmp_path)
    (tmp_path / "invalid.mp4").touch()
    with pytest.raises(ValueError, match="Filename"):
        discover_recordings(tmp_path)
