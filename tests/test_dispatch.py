import json
import subprocess
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from pipelines.transcription import dispatch
from pipelines.transcription import pipeline as worker


@pytest.fixture
def recordings(tmp_path):
    folder = tmp_path / "inputs"
    folder.mkdir()
    for name in (
        "2026-11-10_budget (240p).mp4",
        "2026-11-11_public_comment.mp4",
    ):
        (folder / name).write_bytes(name.encode())
    return folder


def jobs(folder, **overrides):
    values = {
        "image": "docker://registry/transcription:commit",
        "namespace": "recordings",
        "worker_input_dir": "/inputs",
        "input_base_uri": "s3://recordings/mp4",
    }
    values.update(overrides)
    return dispatch.recording_jobs(folder, **values)


def template():
    # Tests rendering only; not asserted to be the cluster's Fuzzfile schema.
    return {
        "job": {
            "image": "${worker.image}",
            "command": "${worker.command}",
            "ingest": "${input.uri}",
        }
    }


def test_one_independent_job_per_file_with_safe_argv_and_remote_uri(recordings):
    prepared = jobs(recordings)
    assert len(prepared) == 2
    assert len({job["job.name"] for job in prepared}) == 2
    for job in prepared:
        assert job["worker.command"] == [
            "chi-ciq-ingest",
            "--input-file",
            f"/inputs/{job['input.filename']}",
            "--namespace",
            "recordings",
            "--expected-sha256",
            job["input.sha256"],
        ]
        assert str(recordings) not in json.dumps(job)
        assert job["source.key"] == f"recordings/{job['input.filename']}"
        assert job["input.uri"].startswith("s3://recordings/mp4/")
    assert "%20" in prepared[0]["input.uri"]
    assert "%28" in prepared[0]["input.uri"]
    assert jobs(recordings) == prepared


def test_shared_storage_force_and_content_changed_job_id(recordings):
    prepared = jobs(recordings, input_base_uri=None, force=True)
    assert prepared[0]["input.uri"] is None
    assert prepared[0]["worker.command"][-1] == "--force"
    (recordings / prepared[0]["input.filename"]).write_bytes(b"changed")
    assert jobs(recordings)[0]["job.name"] != prepared[0]["job.name"]


def test_vimeo_mapping_is_embedded_in_worker_job_and_identity(recordings):
    source = recordings / "vimeo-1200176629-audio-low.mp4"
    source.write_bytes(b"audio")
    mapping = {"1200176629": "2026-06-11_economic_development.mp4"}
    first = jobs(recordings, metadata=mapping)[-1]
    assert first["input.filename"] == source.name
    assert first["source.key"] == f"recordings/{source.name}"
    assert first["worker.command"][-2:] == ["--meeting-filename", mapping["1200176629"]]
    mapping["1200176629"] = "2026-06-12_budget.mp4"
    assert jobs(recordings, metadata=mapping)[-1]["job.name"] != first["job.name"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"image": ""},
        {"namespace": " "},
        {"worker_input_dir": "relative"},
        {"worker_input_dir": "/inputs/../secret"},
        {"input_base_uri": "file:///submitter/only"},
        {"input_base_uri": "https://server/files?token=secret"},
        {"input_base_uri": "https://user:password@server/files"},
    ],
)
def test_invalid_job_configuration_fails_before_creation(recordings, overrides):
    with pytest.raises(ValueError):
        jobs(recordings, **overrides)


def test_template_preserves_lists_and_rejects_missing_values(recordings):
    job = jobs(recordings)[0]
    rendered = dispatch.render_template(template(), job)
    assert rendered["job"]["command"] == job["worker.command"]
    assert rendered["job"]["image"] == job["worker.image"]
    with pytest.raises(ValueError, match="entire JSON value"):
        dispatch.render_template("prefix ${worker.image}", job)
    with pytest.raises(ValueError, match="Missing"):
        dispatch.render_template("${unknown}", job)
    with pytest.raises(ValueError, match="Missing"):
        dispatch.render_template(
            "${input.uri}", jobs(recordings, input_base_uri=None)[0]
        )


def test_offline_cli_never_invokes_fuzzball(recordings, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(dispatch, "load_dotenv", lambda: None)
    monkeypatch.setattr(
        dispatch.subprocess,
        "run",
        lambda *a, **kw: pytest.fail("offline preparation contacted Fuzzball"),
    )
    dispatch.main(
        [
            "--input-dir",
            str(recordings),
            "--image",
            "docker://registry/image:commit",
            "--output-dir",
            str(tmp_path / "plans"),
        ]
    )
    plan = next((tmp_path / "plans").glob("*/plan.json"))
    assert len(json.loads(plan.read_text())["jobs"]) == 2
    assert "Not submitted" in capsys.readouterr().out


def test_render_preflight_and_no_overwrite(recordings, tmp_path):
    prepared = jobs(recordings)
    with pytest.raises(ValueError, match="worker.command"):
        dispatch.prepare_run(tmp_path / "bad", prepared, {"image": "${worker.image}"})
    assert not (tmp_path / "bad").exists()
    first, paths = dispatch.prepare_run(tmp_path, prepared, template())
    second, _ = dispatch.prepare_run(tmp_path, prepared, template())
    assert first != second
    assert len(paths) == 2
    assert first.stat().st_mode & 0o777 == 0o700
    assert isinstance(json.loads(paths[0].read_text())["job"]["command"], list)
    with pytest.raises(FileExistsError):
        dispatch.write_json(paths[0], {})


def test_submission_validates_every_job_before_asynchronous_start(
    recordings, tmp_path, monkeypatch
):
    run, paths = dispatch.prepare_run(tmp_path, jobs(recordings), template())
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        assert "shell" not in kwargs
        if command[2] == "start":
            kwargs["stdout"].write('{"workflow_id":"test"}')
            assert "--watch" not in command
        return SimpleNamespace(returncode=0, args=command)

    monkeypatch.setattr(dispatch.subprocess, "run", execute)
    dispatch.submit_run(run, paths, fuzzball="fuzzball", max_jobs=2)
    assert [command[2] for command in calls] == [
        "validate",
        "validate",
        "start",
        "start",
    ]
    assert len((run / "submissions.jsonl").read_text().splitlines()) == 2
    assert len(list(run.glob("*.receipt"))) == 2


def test_failed_validation_prevents_all_submissions(recordings, tmp_path, monkeypatch):
    run, paths = dispatch.prepare_run(tmp_path, jobs(recordings), template())
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(dispatch.subprocess, "run", execute)
    with pytest.raises(subprocess.CalledProcessError):
        dispatch.submit_run(run, paths, fuzzball="fuzzball", max_jobs=2)
    assert len(calls) == 1 and calls[0][2] == "validate"
    assert not (run / "submissions.jsonl").exists()


def test_partial_submission_failure_keeps_receipts(recordings, tmp_path, monkeypatch):
    run, paths = dispatch.prepare_run(tmp_path, jobs(recordings), template())
    started = []

    def execute(command, **kwargs):
        if command[2] == "start":
            started.append(command)
            kwargs["stdout"].write("accepted" if len(started) == 1 else "failed")
            return SimpleNamespace(
                returncode=0 if len(started) == 1 else 1, args=command
            )
        return SimpleNamespace(returncode=0, args=command)

    monkeypatch.setattr(dispatch.subprocess, "run", execute)
    with pytest.raises(subprocess.CalledProcessError):
        dispatch.submit_run(run, paths, fuzzball="fuzzball", max_jobs=2)
    rows = [
        json.loads(line)
        for line in (run / "submissions.jsonl").read_text().splitlines()
    ]
    assert [row["returncode"] for row in rows] == [0, 1]
    assert len(list(run.glob("*.receipt"))) == 2


def test_submission_limit_and_required_template(recordings, tmp_path, monkeypatch):
    run, paths = dispatch.prepare_run(tmp_path, jobs(recordings), template())
    monkeypatch.setattr(
        dispatch.subprocess, "run", lambda *a, **kw: pytest.fail("limit ignored")
    )
    with pytest.raises(ValueError, match="max-jobs"):
        dispatch.submit_run(run, paths, fuzzball="fuzzball", max_jobs=1)
    with pytest.raises(ValueError, match="template"):
        dispatch.submit_run(run, [], fuzzball="fuzzball", max_jobs=2)


def test_worker_only_processes_assigned_file(recordings, monkeypatch):
    assigned = min(recordings.iterdir())
    ingested = []
    monkeypatch.setattr(worker, "load_dotenv", lambda: None)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        worker, "connect_database", lambda url: SimpleNamespace(dispose=lambda: None)
    )
    monkeypatch.setattr(worker, "check_schema", lambda engine: None)
    monkeypatch.setattr(
        worker,
        "IngestionPipeline",
        lambda *a: SimpleNamespace(
            ingest=lambda path, **kw: ingested.append((path, kw))
        ),
    )
    digest = dispatch.content_digest(assigned)
    worker.main(["--input-file", str(assigned), "--expected-sha256", digest])
    assert ingested == [
        (
            assigned,
            {
                "namespace": "recordings",
                "force": False,
                "expected_sha256": digest,
                "metadata_filename": None,
            },
        )
    ]
    with pytest.raises(SystemExit):
        worker.main(["--input-file", str(assigned), "--input-dir", str(recordings)])
    with pytest.raises(SystemExit):
        worker.main(["--expected-sha256", "0" * 64])


def test_stale_assigned_content_fails_before_inference(recordings, monkeypatch):
    assigned = min(recordings.iterdir())
    monkeypatch.setattr(worker, "source_lock", lambda *a: nullcontext())
    pipeline = worker.IngestionPipeline(
        None, lambda store: pytest.fail("stale input started inference")
    )
    with pytest.raises(ValueError, match="changed since dispatch"):
        pipeline.ingest(assigned, expected_sha256="0" * 64)


def test_content_changes_during_staging_are_rejected(recordings, monkeypatch):
    assigned = min(recordings.iterdir())
    expected = dispatch.content_digest(assigned)
    monkeypatch.setattr(worker, "source_lock", lambda *a: nullcontext())
    pipeline = worker.IngestionPipeline(
        None, lambda store: pytest.fail("changed snapshot started inference")
    )
    monkeypatch.setattr(pipeline.store, "is_unchanged", lambda *a: False)
    monkeypatch.setattr(
        worker.shutil, "copyfile", lambda source, target: target.write_bytes(b"changed")
    )
    with pytest.raises(ValueError, match="changed while staging"):
        pipeline.ingest(assigned, expected_sha256=expected)
