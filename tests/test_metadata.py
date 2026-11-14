from datetime import date

import pytest

from pipelines.transcription import parse_filename


@pytest.mark.parametrize(
    "filename, expected_date, kind",
    [
        (
            "data/mp4/2026_november_10_-_committee_on_budget_and_government_operations (240p).mp4",
            date(2026, 11, 10),
            "committee on budget and government operations",
        ),
        ("2026-11-11_team-standup.mp4", date(2026, 11, 11), "team-standup"),
        ("2024_FEBRUARY_29_-_budget.MP4", date(2024, 2, 29), "budget"),
    ],
)
def test_filename_metadata(filename, expected_date, kind):
    metadata = parse_filename(filename)
    assert metadata.meeting_date == expected_date
    assert metadata.meeting_type == kind


@pytest.mark.parametrize(
    "filename",
    [
        "meeting.mp4",
        "2026_nonesuch_10_-_budget.mp4",
        "2026_february_30_-_budget.mp4",
        "2026-11-11___.mp4",
        "2026-11-11_budget.wav",
    ],
)
def test_invalid_metadata_is_explicit(filename):
    with pytest.raises(ValueError):
        parse_filename(filename)
