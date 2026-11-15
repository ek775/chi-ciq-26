import importlib.util
import json
import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ROCM = ROOT / "ROCM"
spec = importlib.util.spec_from_file_location("rocm_smoke", ROCM / "scripts/smoke.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def definition():
    return yaml.safe_load((ROCM / "compose.yaml").read_text())


def test_all_original_dockerfiles_have_isolated_copies():
    originals = [ROOT / "Dockerfile", *sorted((ROOT / "src").rglob("Dockerfile"))]
    assert len(originals) == 10
    for original in originals:
        assert (ROCM / original.relative_to(ROOT)).is_file()
    for service in definition()["services"].values():
        if "build" in service:
            assert service["build"]["context"] == ".."
            assert service["build"]["dockerfile"].startswith("ROCM/")
            assert (ROOT / service["build"]["dockerfile"]).is_file()


def test_cpu_copies_preserve_existing_images_and_commands():
    gpu_paths = {"Dockerfile", "src/pipelines/transcription/Dockerfile"}
    originals = [ROOT / "Dockerfile", *sorted((ROOT / "src").rglob("Dockerfile"))]
    for original in originals:
        relative = original.relative_to(ROOT)
        if relative.as_posix() in gpu_paths:
            continue
        copied = ROCM / relative

        def active_lines(path):
            return [
                line
                for line in path.read_text().splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]

        assert active_lines(original) == active_lines(copied)


def test_gpu_dockerfiles_guard_rocm_dependencies_and_radeon_target():
    for path in (ROCM / "Dockerfile", ROCM / "src/pipelines/transcription/Dockerfile"):
        dockerfile = path.read_text()
        assert "torch==2.8.0+rocm6.4" in dockerfile
        assert "torchaudio==2.8.0+rocm6.4" in dockerfile
        assert "torchcodec==0.7.0" in dockerfile
        assert "pytorch-triton-rocm==3.4.0" in dockerfile
        assert "python -m pip check" in dockerfile
        assert "assert torch.version.hip" in dockerfile
    vllm = (ROCM / "src/vllm/Dockerfile").read_text()
    assert "rocm7.2.1_navi" in vllm
    assert "gfx1100" in vllm
    assert 'ENTRYPOINT ["vllm", "serve"]' in vllm


def test_analytics_export_is_a_host_copy_not_a_docker_volume():
    service = definition()["services"]["analytics-export"]
    assert service["image"] == definition()["services"]["analytics-schema"]["image"]
    assert "../data/rocm/analytics-backups:/exports" in service["volumes"]
    assert service["command"] == ["python", "/rocm/smoke.py", "export"]
    assert service["environment"]["ANALYTICS_DATABASE_URL"]


@pytest.mark.parametrize("valid,restore_status", [(True, 0), (False, 0), (True, 1)])
def test_export_validates_persisted_dump_and_propagates_failure(
    tmp_path, monkeypatch, valid, restore_status
):
    from databases.meetings import archive

    dump = tmp_path / "analytics-test.dump"
    dump.write_bytes(b"PGDMPcontents" if valid else b"invalid")
    exports = []
    commands = []

    def create_dump(url, directory, prefix):
        exports.append((url, directory, prefix))
        return dump

    def restore(command, **kwargs):
        commands.append(command)
        assert kwargs["check"] is True
        if restore_status:
            raise subprocess.CalledProcessError(restore_status, command)

    monkeypatch.setenv("ANALYTICS_DATABASE_URL", "test-url")
    monkeypatch.setattr(archive, "export_database", create_dump)
    monkeypatch.setattr(smoke.subprocess, "run", restore)
    if not valid:
        with pytest.raises(ValueError, match="custom-format"):
            smoke.export(tmp_path)
    elif restore_status:
        with pytest.raises(subprocess.CalledProcessError):
            smoke.export(tmp_path)
    else:
        smoke.export(tmp_path)
    assert exports == [("test-url", tmp_path, "analytics")]
    assert commands == ([["pg_restore", "--list", str(dump)]] if valid else [])
    assert dump.exists()


def test_only_gpu_services_use_manual_amd_passthrough():
    services = definition()["services"]
    for name, service in services.items():
        assert "gpus" not in service
        assert "privileged" not in service
        if name in {"app", "transcription", "vllm"}:
            assert service["devices"][0] == "/dev/kfd:/dev/kfd"
            assert "renderD128" in service["devices"][1]
            assert service["group_add"]
            assert service["environment"]["HIP_VISIBLE_DEVICES"]
        else:
            assert "devices" not in service
    assert services["analytics"]["environment"]["DOCUMENTS_DATABASE_URL"].startswith(
        "postgresql+psycopg://documents_reader:"
    )
    assert services["web"]["environment"]["DATABASE_URL"].startswith(
        "postgresql+psycopg://web_reader:"
    )
    assert services["web"]["environment"]["ANALYTICS_DATABASE_URL"].startswith(
        "postgresql+psycopg://analytics_reader:"
    )
    assert services["documents"]["command"][-2:] == ["--device", "cpu"]
    assert "ports" not in services["vllm"]
    assert all(
        "ports" not in services[name]
        for name in ("meetings-db", "documents-db", "analytics-db")
    )
    assert services["web"]["ports"][0].startswith("127.0.0.1:")
    assert definition()["name"] == "chi-ciq-rocm-local"


def test_local_stance_runs_without_fabricating_speaker_labels():
    services = definition()["services"]
    command = services["analytics"]["command"]
    assert command[command.index("--stance-scope") + 1] == "all"
    assert command[command.index("--roster-source") + 1] == "snapshot"
    assert command[command.index("--stance-limit") + 1] == "${ROCM_STANCE_LIMIT:-20}"
    assert services["analytics"]["environment"]["VLLM_REASONING_EFFORT"] == ""
    vllm = services["vllm"]["command"]
    assert vllm[vllm.index("--tensor-parallel-size") + 1] == "1"
    assert "0.5B-Instruct" in vllm[vllm.index("--model") + 1]


def test_selects_exactly_ten_established_unique_ids(tmp_path):
    from pipelines.downloads.vimeo import DEFAULT_IDS

    ids = smoke.selected_ids(DEFAULT_IDS, 10)
    assert len(ids) == len(set(ids)) == 10
    assert ids == DEFAULT_IDS.read_text().splitlines()[:10]
    source = tmp_path / "ids"
    source.write_text("# comment\n123\nhttps://vimeo.com/123\n456\n")
    assert smoke.selected_ids(source, 2) == ["123", "456"]
    with pytest.raises(ValueError, match="fewer"):
        smoke.selected_ids(source, 3)


@pytest.mark.parametrize(
    "name,maximum",
    [
        ("ROCM_AUDIO_COUNT", 20),
        ("ROCM_DOWNLOAD_WORKERS", 4),
        ("ROCM_DOCUMENT_COUNT", 10),
    ],
)
@pytest.mark.parametrize("invalid", ["0", "-1", "invalid", "9999"])
def test_sample_limits_are_enforced(monkeypatch, name, maximum, invalid):
    monkeypatch.setenv(name, invalid)
    with pytest.raises(ValueError):
        smoke.positive_setting(name, 1, maximum)


def prepare_fixture(tmp_path, monkeypatch):
    from pipelines.downloads import vimeo

    for name in (
        "ROCM_AUDIO_COUNT",
        "ROCM_DOWNLOAD_WORKERS",
        "ROCM_DOCUMENT_COUNT",
    ):
        monkeypatch.delenv(name, raising=False)
    work = tmp_path / "work"
    archive = tmp_path / "archive"
    archive.mkdir()
    for index in range(4):
        (archive / f"{index}.txt").write_text("Reference text " * 2000)
    downloads = []
    commands = []

    def download(arguments):
        downloads.append(arguments)
        output = Path(arguments[-1])
        state = output / ".vimeo"
        state.mkdir(exist_ok=True)
        for video in arguments[:10]:
            filename = f"2026-11-10_committee_{video}.mp4"
            original = output / filename
            original.write_bytes(b"original audio")
            (state / f"{video}.json").write_text(
                json.dumps(
                    {
                        "video_id": video,
                        "filename": filename,
                        "sha256": vimeo.digest(original),
                        "audio_only": True,
                    }
                )
            )
        return 0

    def unexpected_process(command, **kwargs):
        commands.append(command)
        raise AssertionError("Preparation must not clip or reencode audio")

    monkeypatch.setattr(vimeo, "main", download)
    monkeypatch.setattr(vimeo, "validate_media", lambda path, audio_only: None)
    monkeypatch.setattr(smoke.subprocess, "run", unexpected_process)
    return work, archive, downloads, commands


def test_preparation_uses_complete_audio_and_bounds_documents(tmp_path, monkeypatch):
    work, archive, downloads, commands = prepare_fixture(tmp_path, monkeypatch)
    smoke.prepare(work, archive)
    manifest = json.loads((work / "manifest.json").read_text())
    assert len(manifest["sources"]) == 10
    assert not (work / "clips").exists()
    assert len(list((work / "raw").glob("*.mp4"))) == 10
    assert len(list((work / "documents").glob("*.txt"))) == 3
    assert "--audio-only" in downloads[0]
    assert downloads[0][downloads[0].index("--workers") + 1] == "2"
    assert commands == []
    from pipelines.downloads.vimeo import digest

    for key, source in manifest["sources"].items():
        original = work / "raw" / source["original_filename"]
        assert original.read_bytes() == b"original audio"
        assert source["content_sha256"] == digest(original)
        assert key == f"rocm-smoke/{original.name}"
    assert definition()["services"]["transcription"]["command"][2] == "/work/raw"
    for document in (work / "documents").glob("*"):
        assert len(document.read_text()) == 12000
        assert len((archive / document.name).read_text()) > 12000
    smoke.prepare(work, archive)
    assert commands == []
    monkeypatch.setenv("ROCM_AUDIO_COUNT", "9")
    with pytest.raises(ValueError, match="settings changed"):
        smoke.prepare(work, archive)


def test_preparation_propagates_download_failure(tmp_path, monkeypatch):
    from pipelines.downloads import vimeo

    work, archive, _, _ = prepare_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(vimeo, "main", lambda arguments: 1)
    with pytest.raises(RuntimeError, match="acquisition failed"):
        smoke.prepare(work, archive)
    assert not (work / "manifest.json").exists()


def test_preparation_rejects_legacy_clipped_manifest(tmp_path, monkeypatch):
    work, archive, _, _ = prepare_fixture(tmp_path, monkeypatch)
    smoke.prepare(work, archive)
    path = work / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["settings"].pop("audio_mode")
    manifest["settings"]["clip_seconds"] = 120
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="settings changed"):
        smoke.prepare(work, archive)


def test_preparation_rejects_changed_recordings(tmp_path, monkeypatch):
    from pipelines.downloads import vimeo

    work, archive, _, _ = prepare_fixture(tmp_path, monkeypatch)
    smoke.prepare(work, archive)
    original = next((work / "raw").glob("*.mp4"))
    original.write_bytes(b"changed complete recording")
    receipt_path = next(
        path
        for path in (work / "raw/.vimeo").glob("*.json")
        if json.loads(path.read_text())["filename"] == original.name
    )
    receipt = json.loads(receipt_path.read_text())
    receipt_path.write_text(json.dumps(receipt | {"sha256": vimeo.digest(original)}))
    monkeypatch.setattr(vimeo, "main", lambda arguments: 0)
    with pytest.raises(ValueError, match="input changed"):
        smoke.prepare(work, archive)


@pytest.mark.parametrize("hip,available", [(None, True), ("6.4", False)])
def test_gpu_preflight_refuses_cpu_or_cuda_build(monkeypatch, hip, available):
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            version=SimpleNamespace(hip=hip),
            cuda=SimpleNamespace(is_available=lambda: available),
        ),
    )
    with pytest.raises(RuntimeError, match="refusing CPU"):
        smoke.gpu()


def test_tracker_checks_exact_quote_and_unconfirmed_identity():
    segments = {("source", 0): {"text": "Exact transcript words."}}
    statement = {
        "published_source_key": "source",
        "segment_sequence": 0,
        "quote": "Exact transcript words.",
        "verified": True,
        "speaker": None,
        "person_id": None,
        "speaker_confidence": "unconfirmed",
    }
    dataset = {"statements": [statement], "rejected_candidates": {}}
    smoke.check_tracker(dataset, {"candidate": {}}, segments)
    for field, bad_value in (
        ("quote", "exact transcript words."),
        ("speaker", "Guessed Name"),
        ("speaker_confidence", "named"),
        ("verified", False),
    ):
        with pytest.raises(ValueError, match="contract violated"):
            smoke.check_tracker(
                dataset | {"statements": [statement | {field: bad_value}]},
                {"candidate": {}},
                segments,
            )


def verification_fixture(tmp_path, monkeypatch):
    from pipelines.tracker import database, statements

    work = tmp_path / "work"
    smoke.write_json(
        work / "manifest.json",
        {
            "settings": {"ids": ["123"], "document_count": 1},
            "sources": {"rocm-smoke/vimeo-123.mp4": {"content_sha256": "snapshot"}},
        },
    )
    smoke.write_json(
        work / "tracker-inputs/candidates.json", {"candidates": {"candidate": {}}}
    )
    for name in ("meetings", "documents", "analytics"):
        for phase in ("before", "after"):
            directory = work / "backups" / f"{name}-{phase}"
            directory.mkdir(parents=True)
            (directory / "example.dump").write_bytes(b"archive")
    statement = {
        "published_source_key": "rocm-smoke/vimeo-123.mp4",
        "segment_sequence": 0,
        "quote": "Exact words.",
        "verified": True,
        "speaker": None,
        "person_id": None,
        "speaker_confidence": "unconfirmed",
    }
    summary = {
        "meetings": 1,
        "documents": 1,
        "refreshed_at": "now",
        "last_run": {"status": "completed"},
    }
    counts = {
        "units": 2,
        "sentiments": 2,
        "emotions": 2,
        "relevance": 2,
        "stances": 1,
        "attributions": 0,
    }
    monkeypatch.setattr(
        smoke,
        "get_json",
        lambda path: {
            "/api/meetings?limit=200": [{"id": "meeting"}],
            "/api/meetings/meeting/segments?limit=200": [{"text": "Exact words."}],
            "/ready": {"status": "ready"},
            "/api/analytics/summary": summary,
            "/api/analytics/topics": [{"key": "topic"}],
            "/api/analytics/representatives": [{"id": "representative"}],
            "/api/analytics/statements": [],
        }[path],
    )
    monkeypatch.setattr(
        smoke, "urlopen", lambda *args, **kwargs: BytesIO(b"<html>page")
    )
    monkeypatch.setattr(
        smoke,
        "read_rows",
        lambda url, sql: [
            {
                "evidence": "Exact words.",
                "evidence_verified": True,
                "text": "Exact words.",
            }
        ]
        if "SELECT s.evidence" in sql
        else [counts]
        if "analysis_units" in sql
        else [
            {
                "key": "rocm-smoke/vimeo-123.mp4",
                "content_sha256": "snapshot",
                "segments": 1,
                "text_dimensions": 384,
                "voice_dimensions": 256,
            }
        ],
    )
    monkeypatch.setattr(
        database,
        "load_batch",
        lambda *args: {
            "statements": [statement],
            "rejected_candidates": {},
        },
    )
    monkeypatch.setattr(
        statements,
        "read_segments",
        lambda *args: {
            ("rocm-smoke/vimeo-123.mp4", 0): {"text": "Exact words."},
        },
    )
    for name in (
        "DATABASE_URL",
        "ANALYTICS_DATABASE_URL",
        "MEETINGS_DATABASE_URL",
        "TRACKER_ANALYTICS_DATABASE_URL",
    ):
        monkeypatch.setenv(name, "unused-test-url")
    return work, counts, summary


def test_verifier_writes_report_only_after_complete_outputs(tmp_path, monkeypatch):
    work, _, _ = verification_fixture(tmp_path, monkeypatch)
    smoke.verify(work)
    report = json.loads((work / "report.json").read_text())
    assert report["status"] == "passed"
    assert report["recordings"] == 1


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("units", 0),
        ("sentiments", 1),
        ("emotions", 1),
        ("relevance", 0),
        ("stances", 0),
        ("attributions", 1),
    ],
)
def test_verifier_rejects_incomplete_stages_or_guessed_identity(
    tmp_path, monkeypatch, field, bad_value
):
    work, counts, _ = verification_fixture(tmp_path, monkeypatch)
    counts[field] = bad_value
    with pytest.raises(ValueError, match="stage output or unreviewed identity"):
        smoke.verify(work)
    assert not (work / "report.json").exists()


def test_verifier_requires_exact_web_coverage(tmp_path, monkeypatch):
    work, _, summary = verification_fixture(tmp_path, monkeypatch)
    summary["meetings"] = 0
    with pytest.raises(ValueError, match="dashboard publication"):
        smoke.verify(work)
    assert not (work / "report.json").exists()


@pytest.mark.parametrize(
    "evidence,verified", [("exact words.", True), ("Exact words.", False)]
)
def test_verifier_rejects_nonverbatim_stance_evidence(
    tmp_path, monkeypatch, evidence, verified
):
    work, _, _ = verification_fixture(tmp_path, monkeypatch)
    read_rows = smoke.read_rows
    monkeypatch.setattr(
        smoke,
        "read_rows",
        lambda url, sql: [
            {
                "evidence": evidence,
                "evidence_verified": verified,
                "text": "Exact words.",
            }
        ]
        if "SELECT s.evidence" in sql
        else read_rows(url, sql),
    )
    with pytest.raises(ValueError, match="verbatim verified"):
        smoke.verify(work)
    assert not (work / "report.json").exists()


def runner_fixture(tmp_path):
    runner = tmp_path / "run.sh"
    runner.write_text(
        (ROCM / "run.sh")
        .read_text()
        .replace("test -c /dev/kfd", ":")
        .replace("$HERE/../data", "$HERE/data")
    )
    (tmp_path / ".env").write_text("HF_TOKEN=validation\n")
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['DOCKER_LOG'], 'a') as stream:\n"
        "    stream.write(json.dumps(args) + '\\n')\n"
        "if 'run' in args:\n"
        "    index = args.index('--no-deps') + 1\n"
        "    if args[index] == '--user':\n"
        "        index += 2\n"
        "    service = args[index]\n"
        "    if service == os.environ.get('FAIL_JOB'):\n"
        "        sys.exit(7)\n"
    )
    docker.chmod(0o700)
    log = tmp_path / "docker.log"
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "DOCKER_LOG": str(log),
    }
    return runner, log, env


def test_runner_orders_gpu_ownership_and_full_publication(tmp_path):
    runner, log, env = runner_fixture(tmp_path)
    result = subprocess.run(
        ["sh", str(runner)], env=env, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    jobs = [call[call.index("--no-deps") + 1 :] for call in calls if "run" in call]
    assert jobs[:6] == [
        ["meetings-role"],
        ["documents-role"],
        ["analytics-role"],
        ["meetings"],
        ["documents-schema"],
        ["analytics-schema"],
    ]
    assert jobs[9:16] == [
        ["downloads"],
        ["app"],
        ["transcription", "python", "/rocm/smoke.py", "gpu"],
        ["transcription"],
        ["documents"],
        ["analytics"],
        ["tracker"],
    ]
    assert jobs[-1] == ["verify"]
    export_call = next(call for call in calls if call[-1] == "analytics-export")
    assert export_call[-3:] == [
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "analytics-export",
    ]
    assert (tmp_path / "data/rocm/analytics-backups").is_dir()
    assert "Analytics database dumps:" in result.stdout
    transcribe = next(
        index
        for index, call in enumerate(calls)
        if call[-1] == "transcription" and "run" in call
    )
    start_vllm = next(
        index for index, call in enumerate(calls) if call[-1] == "vllm" and "up" in call
    )
    analytics = next(
        index
        for index, call in enumerate(calls)
        if call[-1] == "analytics" and "run" in call
    )
    stop_vllm = next(
        index
        for index, call in enumerate(calls)
        if index > analytics and call[-2:] == ["stop", "vllm"]
    )
    assert transcribe < start_vllm < analytics < stop_vllm
    assert "PASSED" in result.stdout


def test_runner_fails_closed_and_stops_model_service(tmp_path):
    runner, log, env = runner_fixture(tmp_path)
    result = subprocess.run(
        ["sh", str(runner)],
        env=env | {"FAIL_JOB": "transcription"},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 7
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[-1][-2:] == ["stop", "vllm"]
    assert not any(
        "run" in call and call[-1] in {"analytics", "verify"} for call in calls
    )
    assert "FAILED" in result.stderr
    assert "PASSED" not in result.stdout


def test_runner_requires_host_export_before_success(tmp_path):
    runner, log, env = runner_fixture(tmp_path)
    result = subprocess.run(
        ["sh", str(runner)],
        env=env | {"FAIL_JOB": "analytics-export"},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 7
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert not any("run" in call and call[-1] == "verify" for call in calls)
    assert "PASSED" not in result.stdout
    assert "FAILED" in result.stderr
