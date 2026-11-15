import json
from types import SimpleNamespace

import pytest
import yaml
from test_fuzzball import ROOT

from shared import fuzzball_env


def test_dotenv_reads_only_required_values_without_execution_or_interpolation(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "HF_TOKEN='literal $TOKEN $(touch forbidden)'\n"
        "POSTGRES_PASSWORD='p@ss:word/$TOKEN'\n"
        "WEB_POSTGRES_PASSWORD=not-forwarded\n"
        "LOG_LEVEL=DEBUG\n"
        "VLLM_REASONING_EFFORT=\n"
    )
    values = fuzzball_env.read_values(path)
    assert values == {
        "HF_TOKEN": "literal $TOKEN $(touch forbidden)",
        "POSTGRES_PASSWORD": "p@ss:word/$TOKEN",
    }
    assert not (tmp_path / "forbidden").exists()


@pytest.mark.parametrize(
    ("name", "value"),
    [("HF_TOKEN", ""), ("POSTGRES_PASSWORD", "")],
)
def test_empty_required_values_are_rejected(name, value):
    with pytest.raises(ValueError) as caught:
        fuzzball_env.validate_value(name, value)
    assert name in str(caught.value)


def test_password_need_not_be_url_encoded():
    fuzzball_env.validate_value("POSTGRES_PASSWORD", "p@ss:word/$TOKEN")


def test_sync_pipes_only_required_secrets(monkeypatch, capsys):
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
                ]
            )
            if command[2] == "list"
            else ""
        )
        return SimpleNamespace(returncode=0, stdout=result)

    monkeypatch.setattr(fuzzball_env.subprocess, "run", run)
    fuzzball_env.sync_values(
        "fuzzball",
        {"HF_TOKEN": "private\nquoted'\"", "POSTGRES_PASSWORD": "safe-local"},
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
    assert calls[2][0][2] == "create"
    assert calls[2][0][3] == "secret://user/chi-ciq-postgres-password"
    assert json.loads(calls[2][1]["input"]) == {
        "type": "value",
        "secret": {"value": "safe-local"},
    }
    assert "private" not in capsys.readouterr().out
    assert "safe-local" not in repr([command for command, _ in calls])
    assert all(kwargs["capture_output"] for _, kwargs in calls)


def test_only_required_deployment_secrets_are_replaced(tmp_path):
    values = {"POSTGRES_PASSWORD": "pg-private-value", "HF_TOKEN": "private"}
    definition = fuzzball_env.configured_workflow(ROOT / "fuzzball.yaml", values)
    output = tmp_path / "workflow.yaml"
    fuzzball_env.write_workflow(output, definition)
    content = output.read_text()
    assert "private" not in content
    assert "pg-private-value" not in content
    assert output.stat().st_mode & 0o777 == 0o600
    assert "VLLM_REASONING_EFFORT" not in content
    assert "VLLM_REASONING_TOKENS" not in content
    assert "LOG_LEVEL" not in content
    assert "POSTGRES_PASSWORD=secret://user/chi-ciq-postgres-password" in content
    assert "HF_TOKEN=secret://user/chi-ciq-hf-token" in content
    assert "http://vllm:8000/v1" in content


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


def test_env_example_has_only_compose_inputs_and_secrets():
    values = {
        line.partition("=")[0]
        for line in (ROOT / ".env.example").read_text().splitlines()
        if line and not line.startswith("#")
    }
    assert values == {"POSTGRES_PASSWORD", "HF_TOKEN", "DOCUMENTS_INPUT_DIR"}
