import json
from pathlib import Path

import pytest

from pipelines.transcription.inputs import (
    discover_recordings,
    load_audio_meeting_map,
    load_metadata_file,
)

ROOT = Path(__file__).resolve().parents[1]


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


def test_audio_meeting_map_matches_inventory_to_meeting_schema():
    mapping = load_audio_meeting_map(ROOT / "data-reference/audio-meeting-map.json")
    assert len(mapping) == 204
    budget = mapping["2025-11-12_Budget_and_Government_Operations.mp4"]
    assert budget.meeting_date.isoformat() == "2025-11-12"
    assert budget.meeting_type == "Committee on Budget and Government Operations"
    vimeo = mapping["vimeo-1135462361-audio-low.mp4"]
    assert vimeo.meeting_date.isoformat() == "2025-11-12"
    assert vimeo.meeting_type == "Committee on Economic, Capital and Technology Development"


def test_audio_meeting_map_rejects_schema_drift_and_duplicate_filenames(tmp_path):
    map_path = tmp_path / "audio-map.json"
    expected = {
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
                "audio_filename": "vimeo-1-low.mp4",
                "database_metadata": {
                    "meeting_date": None,
                    "meeting_type": None,
                },
            }
        ],
    }
    map_path.write_text(json.dumps(expected))
    assert load_audio_meeting_map(map_path)["vimeo-1-low.mp4"].meeting_date is None
    expected["audio_files"].append(expected["audio_files"][0])
    map_path.write_text(json.dumps(expected))
    with pytest.raises(ValueError, match="Duplicate audio filename"):
        load_audio_meeting_map(map_path)
    expected["schema_mapping"]["fields"]["meeting_type"] = "meeting.body"
    map_path.write_text(json.dumps(expected))
    with pytest.raises(ValueError, match="does not match the meetings schema"):
        load_audio_meeting_map(map_path)
