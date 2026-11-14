import argparse
import json
import subprocess
from types import SimpleNamespace

import pytest

from pipelines.downloads import vimeo
from pipelines.transcription.dispatch import recording_jobs


@pytest.fixture
def downloader(monkeypatch):
    calls = []
    monkeypatch.setattr(
        vimeo,
        "public_recording",
        lambda video: (
            "2026_november_10_-_committee_on_budget (240p)",
            "https://cdn/playlist",
        ),
    )

    def run(command, **kwargs):
        calls.append(command)
        if command[0] == "ffprobe":
            return SimpleNamespace(
                stdout=json.dumps(
                    {"streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}
                )
            )
        if "--simulate" not in command:
            output = command[command.index("--output") + 1]
            from pathlib import Path

            Path(output.replace("%(ext)s", "mp4")).write_bytes(b"complete-media")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(vimeo.subprocess, "run", run)
    return calls


@pytest.mark.parametrize(
    "value",
    [
        "12345",
        "https://vimeo.com/12345",
        "https://player.vimeo.com/video/12345",
        "https://vimeo.com/showcase/8928495?video=12345",
    ],
)
def test_public_video_id_parsing(value):
    assert vimeo.vimeo_id(value) == "12345"


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/123",
        "https://vimeo.com/showcase/123",
        "http://vimeo.com/123",
        "not-an-id",
    ],
)
def test_nonpublic_or_collection_links_rejected(value):
    with pytest.raises(argparse.ArgumentTypeError):
        vimeo.vimeo_id(value)


def test_download_publishes_transcription_ready_files(tmp_path, downloader):
    target = vimeo.download("123", tmp_path)
    assert target.read_bytes() == b"complete-media"
    assert len(list(tmp_path.glob("*.mp4"))) == 1
    state = json.loads((tmp_path / ".vimeo/123.json").read_text())
    assert state["filename"] == target.name
    jobs = recording_jobs(
        tmp_path,
        image="docker://worker:commit",
        namespace="recordings",
        worker_input_dir="/inputs",
    )
    assert jobs[0]["input.sha256"] == state["sha256"]
    assert jobs[0]["input.filename"] == target.name
    assert not list(tmp_path.glob(".vimeo-*"))
    assert vimeo.download("123", tmp_path) == target
    assert len(downloader) == 2


def test_refresh_failure_preserves_last_completed_file(
    tmp_path, downloader, monkeypatch
):
    target = vimeo.download("123", tmp_path)
    original = target.read_bytes()
    state = (tmp_path / ".vimeo/123.json").read_bytes()

    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(vimeo.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        vimeo.download("123", tmp_path, refresh=True)
    assert target.read_bytes() == original
    assert (tmp_path / ".vimeo/123.json").read_bytes() == state
    assert not list(tmp_path.glob(".vimeo-*"))


def test_no_inference_ready_partial_file_visible_during_download(
    tmp_path, downloader, monkeypatch
):
    original = vimeo.subprocess.run

    def run(command, **kwargs):
        assert not list(tmp_path.glob("*.mp4"))
        return original(command, **kwargs)

    monkeypatch.setattr(vimeo.subprocess, "run", run)
    vimeo.download("123", tmp_path)


def test_invalid_title_needs_explicit_metadata(tmp_path, downloader, monkeypatch):
    monkeypatch.setattr(
        vimeo, "public_recording", lambda video: ("Budget", "https://cdn")
    )
    with pytest.raises(ValueError, match="metadata-file"):
        vimeo.download("123", tmp_path)
    target = vimeo.download("123", tmp_path, filename="2026-11-10_budget.mp4")
    assert target.name == "2026-11-10_budget.mp4"


def test_existing_files_and_other_ids_are_not_overwritten(tmp_path, downloader):
    target = tmp_path / "2026-11-10_budget.mp4"
    target.write_bytes(b"user-owned")
    with pytest.raises(FileExistsError):
        vimeo.download("123", tmp_path, filename=target.name)
    assert target.read_bytes() == b"user-owned"
    first = vimeo.download("123", tmp_path)
    with pytest.raises(ValueError, match="another Vimeo"):
        vimeo.download("456", tmp_path)
    with pytest.raises(ValueError, match="established filename"):
        vimeo.download("123", tmp_path, filename="2026-11-11_budget.mp4")
    assert first.read_bytes() == b"complete-media"


def test_dry_run_does_not_publish_recording_or_receipt(tmp_path, downloader):
    assert vimeo.download("123", tmp_path, dry_run=True) is None
    assert "--simulate" in downloader[0]
    assert not list(tmp_path.glob("*.mp4"))
    assert not list((tmp_path / ".vimeo").glob("*.json"))


def test_modified_managed_file_requires_explicit_refresh(tmp_path, downloader):
    target = vimeo.download("123", tmp_path)
    target.write_bytes(b"locally-edited")
    with pytest.raises(ValueError, match="refresh"):
        vimeo.download("123", tmp_path)
    assert target.read_bytes() == b"locally-edited"
    assert (
        vimeo.download("123", tmp_path, refresh=True).read_bytes() == b"complete-media"
    )


def test_missing_audio_is_not_published(tmp_path, downloader, monkeypatch):
    monkeypatch.setattr(
        vimeo,
        "validate_media",
        lambda path: (_ for _ in ()).throw(ValueError("missing audio")),
    )
    with pytest.raises(ValueError, match="audio"):
        vimeo.download("123", tmp_path)
    assert not list(tmp_path.glob("*.mp4"))


def test_cli_deduplicates_batch_and_reports_failure(tmp_path, monkeypatch):
    batch = tmp_path / "ids.txt"
    batch.write_text("# videos\n123\n123\n456\n")
    monkeypatch.setattr(vimeo, "load_dotenv", lambda: None)
    monkeypatch.setattr(vimeo.shutil, "which", lambda tool: tool)
    calls = []

    def download(video, *a, **kw):
        calls.append(video)
        if video == "456":
            raise ValueError("unavailable")

    monkeypatch.setattr(vimeo, "download", download)
    assert vimeo.main(["--batch-file", str(batch), "--output-dir", str(tmp_path)]) == 1
    assert calls == ["123", "456"]


def test_packaged_batch_list_default_is_used(monkeypatch):
    monkeypatch.setattr(vimeo, "load_dotenv", lambda: None)
    monkeypatch.setattr(vimeo.shutil, "which", lambda tool: tool)
    calls = []
    monkeypatch.setattr(vimeo, "download", lambda video, *a, **kw: calls.append(video))
    assert vimeo.main([]) == 0
    assert len(calls) == len(set(vimeo.DEFAULT_IDS.read_text().splitlines()))
    assert len(calls) > 0
