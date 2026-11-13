import logging

import pytest

from transcription import progress


@pytest.mark.parametrize("fails", [False, True])
def test_timer_reports_elapsed_on_success_and_failure(monkeypatch, caplog, fails):
    readings = iter([100.0, 165.25])
    monkeypatch.setattr(progress, "perf_counter", lambda: next(readings))
    caplog.set_level(logging.INFO)
    if fails:
        with (
            pytest.raises(ValueError, match="empty text"),
            progress.timed_operation("Video"),
        ):
            raise ValueError("empty text")
    else:
        with progress.timed_operation("Video"):
            pass
    assert "Video: started" in caplog.text
    assert "0:01:05 (65.25s)" in caplog.text
    assert ("failed or interrupted" if fails else "completed") in caplog.text
