import json
from types import SimpleNamespace

import pytest
import yaml
from test_fuzzball import ROOT

from shared import fuzzball_env


def test_dotenv_is_parsed_without_shell_execution_or_interpolation(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "HF_TOKEN='literal $TOKEN $(touch forbidden)'\n"
        "WEB_POSTGRES_PASSWORD=not-forwarded\n"
        "LOG_LEVEL=DEBUG\n"
        "VLLM_REASONING_EFFORT=\n"
    )
    values = fuzzball_env.read_values(path)
    assert values["HF_TOKEN"] == "literal $TOKEN $(touch forbidden)"
    assert values["LOG_LEVEL"] == "DEBUG"
    assert values["VLLM_REASONING_EFFORT"] == ""
    assert "WEB_POSTGRES_PASSWORD" not in values
    assert "VLLM_BASE_URL" not in values
    assert not (tmp_path / "forbidden").exists()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("VLLM_BASE_URL", "http://owner:private@localhost/v1"),
        ("VLLM_BASE_URL", "http://[::1]:8000/v1"),
        ("VLLM_BASE_URL", "http://0.0.0.0/v1"),
        ("VLLM_BASE_URL", "http://owner:private@vllm:not-port/v1"),
        ("VLLM_BASE_URL", "postgresql://owner:private@vllm/v1"),
        ("HF_TOKEN", ""),
        ("POSTGRES_PASSWORD", ""),
        ("LOG_LEVEL", "broken"),
        ("VLLM_REASONING_TOKENS", "-1"),
    ],
)
def test_invalid_values_fail_without_disclosing_credentials(name, value):
    with pytest.raises(ValueError) as caught:
        fuzzball_env.validate_value(name, value)
    assert "private" not in str(caught.value)
    assert value not in str(caught.value) or not value


def test_sync_pipes_values_and_updates_only_existing_user_value_secrets(
    monkeypatch, capsys
):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        result = (
            json.dumps(
                [
                    {
                        "reference": "secret://user/chi-ciq-hf-token",
                        "type": "SECRET_TYPE_VALUE",
                    },
                    {
                        "reference": "secret://group/chi-ciq-log-level",
                        "type": "SECRET_TYPE_VALUE",
                    },
                ]
            )
            if command[2] == "list"
            else ""
        )
        return SimpleNamespace(returncode=0, stdout=result)

    monkeypatch.setattr(fuzzball_env.subprocess, "run", run)
    fuzzball_env.sync_values(
        "fuzzball",
        {"HF_TOKEN": "private\nquoted'\"", "LOG_LEVEL": "DEBUG"},
    )
    assert calls[1][0] == [
        "fuzzball",
        "secret",
        "update",
        "secret://user/chi-ciq-hf-token",
    ]
    assert json.loads(calls[1][1]["input"]) == {
        "type": "value",
        "secret": {"value": "private\nquoted'\""},
    }
    assert calls[2][0][-2:] == ["--type", "value"]
    assert json.loads(calls[2][1]["input"]) == {
        "type": "value",
        "secret": {"value": "DEBUG"},
    }
    assert calls[2][0][2] == "create"
    assert "private" not in capsys.readouterr().out
    assert "private" not in repr([command for command, _ in calls])
    assert all(kwargs["capture_output"] for _, kwargs in calls)


def test_empty_optional_settings_are_inline_and_credentials_stay_references(tmp_path):
    values = {"VLLM_REASONING_EFFORT": "", "HF_TOKEN": "private"}
    definition = fuzzball_env.configured_workflow(ROOT / "fuzzball.yaml", values)
    output = tmp_path / "workflow.yaml"
    fuzzball_env.write_workflow(output, definition)
    content = output.read_text()
    assert "private" not in content
    assert output.stat().st_mode & 0o777 == 0o600
    assert (
        "export VLLM_REASONING_EFFORT=''"
        in definition["jobs"]["analytics-sync"]["script"]
    )
    assert all(
        not entry.endswith("=") for entry in definition["jobs"]["analytics-sync"]["env"]
    )
    assert "HF_TOKEN=secret://user/chi-ciq-hf-token" in content
    configured = fuzzball_env.configured_workflow(
        ROOT / "fuzzball.yaml", {"VLLM_REASONING_EFFORT": "high"}
    )
    assert "VLLM_BASE_URL=http://vllm:8000/v1" in configured["jobs"][
        "analytics-sync"
    ]["env"]


def test_cli_failure_does_not_echo_secret_payload(monkeypatch):
    monkeypatch.setattr(
        fuzzball_env.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stdout="private", stderr="private"
        ),
    )
    with pytest.raises(RuntimeError) as caught:
        fuzzball_env.run_cli("fuzzball", ["secret", "create"], payload="private")
    assert "private" not in str(caught.value)


def test_check_never_contacts_cluster(tmp_path, monkeypatch, capsys):
    path = tmp_path / ".env"
    path.write_text("HF_TOKEN=private")
    monkeypatch.setattr(
        fuzzball_env.subprocess,
        "run",
        lambda *a, **kw: pytest.fail("check contacted Fuzzball"),
    )
    fuzzball_env.main(["--env-file", str(path), "--only", "HF_TOKEN", "--check"])
    assert "private" not in capsys.readouterr().out


def test_incomplete_configuration_does_not_upload_partial_secrets(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / ".env"
    path.write_text("HF_TOKEN=private")
    monkeypatch.setattr(
        fuzzball_env.subprocess,
        "run",
        lambda *a, **kw: pytest.fail("incomplete config contacted Fuzzball"),
    )
    with pytest.raises(SystemExit) as caught:
        fuzzball_env.main(["--env-file", str(path)])
    assert caught.value.code == 1
    assert "Missing POSTGRES_PASSWORD" in capsys.readouterr().err


def test_dotenv_mapping_covers_every_workflow_secret():
    definition = yaml.safe_load((ROOT / "fuzzball.yaml").read_text())
    nodes = definition["jobs"] | definition["services"]
    env = definition["defaults"]["job"]["env"] + [
        value for node in nodes.values() for value in node.get("env", [])
    ]
    references = {
        value.partition("=")[2] for value in env if "=secret://user/" in value
    }
    assert references == {
        f"secret://user/chi-ciq-{suffix}" for suffix in fuzzball_env.VARIABLES.values()
    }
