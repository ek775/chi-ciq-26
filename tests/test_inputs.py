import json

import pytest

from pipelines.transcription.inputs import discover_recordings, load_metadata_file


def test_discovery_is_sorted_case_insensitive_and_nonrecursive(tmp_path):
    for name in ("2026-11-11_budget.mp4", "2026-11-10_budget.MP4", "notes.txt"):
        (tmp_path / name).touch()
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested/invalid.mp4").touch()
    assert [p.name for p in discover_recordings(tmp_path)] == [
        "2026-11-10_budget.MP4",
        "2026-11-11_budget.mp4",
    ]


def test_discovery_rejects_missing_empty_but_accepts_opaque_filenames(tmp_path):
    with pytest.raises(NotADirectoryError):
        discover_recordings(tmp_path / "missing")
    with pytest.raises(ValueError, match="No MP4"):
        discover_recordings(tmp_path)
    (tmp_path / "invalid.mp4").touch()
    assert discover_recordings(tmp_path) == [tmp_path / "invalid.mp4"]


def test_vimeo_audio_discovery_accepts_optional_valid_mapping(tmp_path):
    source = tmp_path / "vimeo-1200176629-audio-low.mp4"
    source.touch()
    (tmp_path / "2025-11-12_Budget.mp4").touch()
    assert len(discover_recordings(tmp_path)) == 2
    metadata_file = tmp_path / "mapping.json"
    metadata_file.write_text(
        json.dumps({"1200176629": "2026-06-11_economic_development.mp4"})
    )
    metadata = load_metadata_file(metadata_file)
    assert len(discover_recordings(tmp_path, metadata)) == 2
    metadata_file.write_text(json.dumps({"1200176629": "../2026-06-11_budget.mp4"}))
    with pytest.raises(ValueError, match="directory components"):
        load_metadata_file(metadata_file)
    metadata_file.write_text(json.dumps({"1200176629": 123}))
    with pytest.raises(TypeError, match="Invalid metadata filename"):
        load_metadata_file(metadata_file)
