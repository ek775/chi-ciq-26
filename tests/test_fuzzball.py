import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

from pipelines.analytics.pipeline import STAGES, AnalyticsConfig
from shared.sharding import shard_paths

ROOT = Path(__file__).resolve().parents[1]


def workflow():
    return yaml.safe_load((ROOT / "fuzzball.yaml").read_text())


def dependencies(job):
    return {dependency["name"] for dependency in job.get("depends-on", [])}


def test_transcription_does_not_require_filename_metadata(tmp_path):
    executable = tmp_path / "chi-ciq-ingest"
    executable.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n")
    executable.chmod(0o700)
    script = workflow()["jobs"]["transcription"]["script"]
    result = subprocess.run(
        ["sh", "-c", script],
        env=os.environ
        | {
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "FB_TASK_ID": "1",
            "SHARD_COUNT": "128",
        },
        text=True,
        capture_output=True,
        check=True,
    )
    arguments = result.stdout.splitlines()
    assert "--metadata-file" not in arguments
    assert not result.stderr
    assert arguments[-4:] == ["--shard-index", "0", "--shard-count", "128"]


def test_backups_use_team_storage_separate_from_input_datasets():
    definition = workflow()
    assert definition["volumes"]["database-backups"] == {
        "use": "team8",
        "name": "database-backups",
    }
    for database in ("documents", "analytics"):
        for phase in ("before", "after"):
            job = definition["jobs"][f"{database}-backup-{phase}"]
            assert job["mounts"] == {"/backups": {"volume": "database-backups"}}
            assert job["command"][-2:] == [
                "--output-dir",
                f"/backups/{database}",
            ]
    for name in ("documents", "analytics-roster"):
        assert definition["jobs"][name]["mounts"] == {
            "/datasets": {"volume": "datasets"}
        }


def test_deployment_dag_is_acyclic_and_fail_closed():
    definition = workflow()
    assert definition["version"] == "v4"
    nodes = definition["jobs"] | definition["services"]
    remaining = set(nodes)
    while remaining:
        ready = {
            name for name in remaining if not dependencies(nodes[name]) & remaining
        }
        assert ready, "Deployment dependencies contain a cycle"
        for name in ready:
            assert dependencies(nodes[name]) <= nodes.keys()
            assert all(
                dependency["status"]
                == (
                    "RUNNING"
                    if dependency["name"] in {"postgres", "vllm"}
                    else "FINISHED"
                )
                for dependency in nodes[name].get("depends-on", [])
            )
        remaining -= ready
    assert dependencies(nodes["postgres-provision"]) == {"postgres"}
    assert dependencies(nodes["vllm"]) == {"transcription", "documents"}
    assert dependencies(nodes["meetings-migrate"]) == {"postgres-provision"}
    assert not any(name.startswith("meetings-backup") for name in nodes)
    for database in ("documents", "analytics"):
        assert dependencies(nodes[f"{database}-backup-before"]) == {
            "postgres-provision"
        }
    assert dependencies(nodes["analytics-stance"]) == {
        "analytics-topics",
        "analytics-attribute",
        "vllm",
    }
    assert dependencies(nodes["web"]) == {"web-check"}
    assert dependencies(nodes["web-check"]) == {
        "transcription",
        "documents-backup-after",
        "analytics-backup-after",
    }
    assert nodes["web"]["persist"] is True
    assert nodes["web"]["network"]["endpoints"][0]["scope"] == "user"
    assert nodes["web"]["readiness-probe"]["http-get"]["path"] == "/ready"


def test_vllm_is_an_internal_gpu_service():
    definition = workflow()
    service = definition["services"]["vllm"]
    command = service["command"]
    assert service["persist"] is True
    assert service["resource"]["devices"] == {"nvidia.com/gpu": 1}
    assert command[command.index("--model") + 1] == "openai/gpt-oss-20b"
    assert command[command.index("--tensor-parallel-size") + 1] == "1"
    assert command[command.index("--served-model-name") + 1] == "local-ai"
    assert service["mounts"]["/root/.cache/huggingface"]["volume"] == (
        "vllm-model-cache"
    )
    assert definition["volumes"]["vllm-model-cache"]["name"] == "vllm-model-cache"
    assert service["network"]["ports"] == [
        {"name": "http", "port": 8000, "protocol": "tcp"}
    ]
    assert service["readiness-probe"]["http-get"] == {
        "path": "/health",
        "port": 8000,
    }
    assert (
        "VLLM_BASE_URL=http://vllm:8000/v1"
        in definition["jobs"]["analytics-stance"]["env"]
    )


def test_ingestion_arrays_have_consistent_disjoint_shards(tmp_path):
    jobs = workflow()["jobs"]
    for name, count, concurrency in (
        ("transcription", 128, 2),
        ("documents", 4, 4),
    ):
        job = jobs[name]
        assert job["task-array"] == {
            "start": 1,
            "end": count,
            "concurrency": concurrency,
        }
        assert f"SHARD_COUNT={count}" in job["env"]
        assert "$((" in job["script"]
        assert '--shard-count "$SHARD_COUNT"' in job["script"]
        assert dependencies(job) == {
            "meetings-migrate" if name == "transcription" else "documents-migrate"
        }
    paths = [tmp_path / f"folder/{index}.txt" for index in range(100)]
    shards = [
        set(shard_paths(paths, tmp_path, index=index, count=4)) for index in range(4)
    ]
    assert all(shards)
    assert set.union(*shards) == set(paths)
    assert sum(map(len, shards)) == len(paths)
    for index, shard in enumerate(shards):
        assert (
            set(shard_paths(list(reversed(paths)), tmp_path, index=index, count=4))
            == shard
        )
        assert (
            set(
                shard_paths(
                    paths + [tmp_path / "new.txt"], tmp_path, index=index, count=4
                )
            )
            & set(paths)
            == shard
        )
    assert shard_paths(paths, tmp_path) == paths


@pytest.mark.parametrize(("index", "count"), [(-1, 2), (2, 2), (0, 0)])
def test_invalid_shards_fail_even_for_empty_inputs(tmp_path, index, count):
    with pytest.raises(ValueError, match="Shard count"):
        shard_paths([], tmp_path, index=index, count=count)


def test_analytics_fanout_and_join_cover_every_stage_once():
    jobs = workflow()["jobs"]
    seen = []
    for job in jobs.values():
        command = job.get("command", [])
        if command[:2] == ["chi-ciq-analytics", "run"]:
            stages = (command[command.index("--stages") + 1],)
            parallel = "--parallel-stage" in command
            AnalyticsConfig(stages=stages, parallel_stage=parallel)
            seen.extend(stages)
            if parallel and stages != ("stance",):
                assert dependencies(job) == {"analytics-roster"}
    assert "run --stages roster" in jobs["analytics-roster"]["script"]
    seen.append("roster")
    for stage in ("sentiment", "emotion", "stance"):
        job = jobs[f"analytics-{stage}"]
        assert job["task-array"] == {"start": 1, "end": 32, "concurrency": 4}
        assert f"--stages {stage} --parallel-stage" in job["script"]
        assert '--shard-index "$((FB_TASK_ID - 1))" --shard-count 32' in job["script"]
        AnalyticsConfig(stages=(stage,), parallel_stage=True, shard_count=32)
        seen.append(stage)
    assert set(seen) == set(STAGES)
    assert len(seen) == len(STAGES)
    assert dependencies(jobs["analytics-sync"]) == {
        "analytics-migrate",
        "transcription",
        "documents",
    }
    assert dependencies(jobs["analytics-stance"]) == {
        "analytics-topics",
        "analytics-attribute",
        "vllm",
    }
    assert dependencies(jobs["analytics-refresh"]) == {
        f"analytics-{stage}"
        for stage in (
            "attribute",
            "mentions",
            "sentiment",
            "emotion",
            "topics",
            "stance",
        )
    }


def test_database_urls_use_the_workflow_service_and_carry_no_password():
    definition = workflow()
    nodes = definition["jobs"] | definition["services"]
    credentials = {"PGPASSWORD", "POSTGRES_PASSWORD", "HF_TOKEN", "VLLM_API_KEY"}
    for node in nodes.values():
        for value in node.get("env", []):
            key, _, setting = value.partition("=")
            if key.endswith("DATABASE_URL"):
                url = urlsplit(setting)
                assert (url.hostname, url.port) == ("postgres", 5432)
                assert url.password is None
            if key in credentials:
                assert setting.startswith("secret://user/chi-ciq-")
    password = "PGPASSWORD=secret://user/chi-ciq-postgres-password"
    assert password in definition["defaults"]["job"]["env"]
    assert password in nodes["web"]["env"]
    assert not nodes["web"].get("mounts")
    web_users = {
        urlsplit(value.partition("=")[2]).username
        for value in nodes["web"]["env"]
        if value.partition("=")[0].endswith("DATABASE_URL")
    }
    assert web_users == {"web_reader", "analytics_reader"}
    assert not (ROOT / "fuzzball.draft.yaml").exists()


def test_workstation_resource_budget_and_bounded_vllm_requests():
    jobs = workflow()["jobs"]

    def memory_gib(job):
        size = job["resource"]["memory"]["size"]
        assert size.endswith("GiB")
        return int(size.removesuffix("GiB")) * job.get("task-array", {}).get(
            "concurrency", 1
        )

    transcription = jobs["transcription"]
    assert transcription["task-array"]["concurrency"] == 2
    assert transcription["resource"]["devices"] == {"nvidia.com/gpu": 1}
    assert transcription["resource"]["memory"]["size"] == "64GiB"
    # Two GPU workers plus document ingestion fit 40 cores / 256 GB.
    assert (
        sum(
            jobs[name]["resource"]["cpu"]["cores"]
            * jobs[name]["task-array"]["concurrency"]
            for name in ("transcription", "documents")
        )
        == 32
    )
    assert sum(memory_gib(jobs[name]) for name in ("transcription", "documents")) == 160
    analytics = [
        job
        for name, job in jobs.items()
        if name.startswith("analytics-")
        and name.rsplit("-", 1)[1]
        in {"attribute", "mentions", "sentiment", "emotion", "topics", "stance"}
    ]
    assert (
        sum(
            job["resource"]["cpu"]["cores"]
            * job.get("task-array", {}).get("concurrency", 1)
            for job in analytics
        )
        == 32
    )
    assert sum(memory_gib(job) for job in analytics) == 96
    stance = jobs["analytics-stance"]
    assert "--stance-concurrency 2" in stance["script"]
    assert stance["task-array"]["concurrency"] * 2 == 8
    for stage in ("sentiment", "emotion"):
        assert "OMP_NUM_THREADS=2" in jobs[f"analytics-{stage}"]["env"]
