import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from shared.logging import JsonFormatter

ROOT = Path(__file__).resolve().parents[1]


def test_json_formatter_preserves_message_and_traceback_in_one_line():
    try:
        raise ValueError("bad\ninput")
    except ValueError:
        record = logging.LogRecord(
            "worker",
            logging.ERROR,
            __file__,
            1,
            "Failed %s",
            ("meeting",),
            sys.exc_info(),
        )
    line = JsonFormatter("transcription").format(record)
    event = json.loads(line)
    assert "\n" not in line
    assert event["service"] == "transcription"
    assert event["logger"] == "worker"
    assert event["level"] == "ERROR"
    assert event["message"] == "Failed meeting"
    assert event["timestamp"].endswith("+00:00")
    assert "ValueError: bad\ninput" in event["exception"]


def run_python(source, **environment):
    env = {**os.environ, "LOG_LEVEL": "INFO", "LOG_SERVICE_NAME": "test-service"}
    return subprocess.run(
        [sys.executable, "-c", source],
        check=False,
        capture_output=True,
        text=True,
        env={**env, **environment},
    )


def test_configuration_captures_warnings_and_does_not_duplicate_handlers():
    result = run_python(
        """
import logging
import warnings
from shared.logging import configure_logging
configure_logging("first")
configure_logging("second")
logging.getLogger("worker").debug("hidden")
logging.getLogger("worker").info("visible")
logging.getLogger("uvicorn.access").info("GET /health 200")
warnings.warn("test warning")
print("CLI result")
"""
    )
    assert result.returncode == 0
    assert result.stdout == "CLI result\n"
    events = [json.loads(line) for line in result.stderr.splitlines()]
    assert len(events) == 3
    assert all(event["service"] == "test-service" for event in events)
    assert events[0]["message"] == "visible"
    assert events[1]["logger"] == "uvicorn.access"
    assert events[2]["logger"] == "py.warnings"


def test_uncaught_failure_is_json_and_keeps_nonzero_exit():
    result = run_python(
        """
from shared.logging import configure_logging
configure_logging("worker")
raise RuntimeError("job failed")
"""
    )
    assert result.returncode == 1
    event = json.loads(result.stderr)
    assert event["level"] == "CRITICAL"
    assert "RuntimeError: job failed" in event["exception"]


def test_log_level_is_configurable_and_invalid_levels_fail():
    source = """
import logging
from shared.logging import configure_logging
configure_logging("worker")
logging.info("hidden")
logging.error("visible")
"""
    result = run_python(source, LOG_LEVEL="warning")
    assert result.returncode == 0
    assert json.loads(result.stderr)["message"] == "visible"
    invalid = run_python(source, LOG_LEVEL="typo")
    assert invalid.returncode != 0
    assert "Invalid LOG_LEVEL" in invalid.stderr


def test_web_keeps_shared_logging_configuration(monkeypatch):
    import dotenv
    import uvicorn

    from web import app

    services = []
    invocations = []
    application = object()
    monkeypatch.setattr(dotenv, "load_dotenv", lambda: False)
    monkeypatch.setattr(app, "configure_logging", services.append)
    monkeypatch.setattr(app, "create_app", lambda: application)
    monkeypatch.setattr(
        uvicorn, "run", lambda *args, **kwargs: invocations.append((args, kwargs))
    )
    app.main()
    assert services == ["web"]
    assert invocations == [
        ((application,), {"host": "0.0.0.0", "port": 8080, "log_config": None})
    ]


@pytest.fixture
def log_command(tmp_path):
    executable = tmp_path / "fake-fuzzball"
    executable.write_text(
        '#!/bin/sh\nprintf "<%s>\\n" "$@"\n'
        'echo "job output" >&2\nexit "${MOCK_EXIT_CODE:-0}"\n'
    )
    executable.chmod(0o700)

    def run(*args, exit_code="0"):
        return subprocess.run(
            ["bash", str(ROOT / "scripts/logs.sh"), *args],
            check=False,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "FUZZBALL_BIN": str(executable),
                "LOG_DIR": str(tmp_path / "logs with spaces"),
                "MOCK_EXIT_CODE": exit_code,
            },
        )

    return run, tmp_path / "logs with spaces"


def test_host_helper_follows_and_appends_both_streams(log_command):
    run, folder = log_command
    first = run("workflow-123", "transcribe")
    assert first.returncode == 0
    assert first.stdout.splitlines() == [
        "<--color>",
        "<off>",
        "<workflow>",
        "<log>",
        "<workflow-123>",
        "<transcribe>",
        "<--follow>",
        "job output",
    ]
    path = folder / "workflow-123" / "transcribe.log"
    assert path.read_text() == first.stdout
    assert path.stat().st_mode & 0o777 == 0o600
    second = run("workflow-123", "transcribe", "--lines", "-1")
    assert second.returncode == 0
    assert "<--lines>\n<-1>" in second.stdout
    assert path.read_text() == first.stdout + second.stdout


def test_host_helper_preserves_cli_failure_and_saved_diagnostics(log_command):
    run, folder = log_command
    result = run("workflow", "web", exit_code="7")
    assert result.returncode == 7
    assert "job output" in result.stdout
    assert (folder / "workflow" / "web.log").read_text() == result.stdout


@pytest.mark.parametrize(
    "args",
    [(), ("workflow",), ("../escape", "job"), ("workflow", "../job"), ("workflow", "")],
)
def test_host_helper_rejects_missing_or_unsafe_names(log_command, args):
    run, folder = log_command
    assert run(*args).returncode == 2
    assert not folder.exists()
