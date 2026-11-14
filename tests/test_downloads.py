import argparse
import io
import json
import shutil
import subprocess
import wave
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from email.utils import formatdate
from threading import Barrier, Lock
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest

from pipelines.downloads import vimeo
from pipelines.transcription.dispatch import recording_jobs


@pytest.fixture
def metadata_http(monkeypatch):
    def response():
        return io.BytesIO(
            json.dumps(
                {
                    "video": {"title": "2026-11-10_budget"},
                    "request": {
                        "files": {
                            "hls": {
                                "default_cdn": "cdn",
                                "cdns": {"cdn": {"url": "https://cdn/playlist"}},
                            }
                        }
                    },
                }
            ).encode()
        )

    request = Mock(side_effect=lambda *args, **kwargs: response())
    sleeps = []
    monkeypatch.setattr(vimeo, "urlopen", request)
    monkeypatch.setattr(vimeo.time, "sleep", sleeps.append)
    monkeypatch.setattr(vimeo.random, "uniform", lambda start, end: 0)
    return SimpleNamespace(request=request, sleeps=sleeps, response=response)


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_metadata_retries_transient_http_errors(metadata_http, status, caplog):
    error = HTTPError("https://vimeo/config", status, "temporary", Message(), None)
    metadata_http.request.side_effect = [error, error, metadata_http.response()]
    assert vimeo.public_recording("123") == (
        "2026-11-10_budget",
        "https://cdn/playlist",
    )
    assert metadata_http.sleeps == [1, 2]
    assert metadata_http.request.call_count == 3
    assert f"123: Vimeo metadata request failed (HTTP {status})" in caplog.text


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_metadata_does_not_retry_permanent_http_errors(metadata_http, status):
    error = HTTPError("https://vimeo/config", status, "permanent", Message(), None)
    metadata_http.request.side_effect = error
    with pytest.raises(HTTPError) as raised:
        vimeo.public_recording("123")
    assert raised.value is error
    assert metadata_http.request.call_count == 1
    assert metadata_http.sleeps == []


@pytest.mark.parametrize(
    "error", [URLError("connection failed"), TimeoutError(), ConnectionResetError()]
)
def test_metadata_retries_network_errors(metadata_http, error):
    metadata_http.request.side_effect = [error, metadata_http.response()]
    assert vimeo.public_recording("123")[0] == "2026-11-10_budget"
    assert metadata_http.sleeps == [1]


def test_metadata_retry_exhaustion_is_bounded(metadata_http):
    error = HTTPError("https://vimeo/config", 429, "limited", Message(), None)
    metadata_http.request.side_effect = error
    with pytest.raises(HTTPError) as raised:
        vimeo.public_recording("123")
    assert raised.value is error
    assert metadata_http.request.call_count == 6
    assert metadata_http.sleeps == [1, 2, 4, 8, 16]


def test_metadata_network_retry_exhaustion_is_bounded(metadata_http):
    metadata_http.request.side_effect = URLError("unavailable")
    with pytest.raises(URLError):
        vimeo.public_recording("123")
    assert metadata_http.request.call_count == 6
    assert metadata_http.sleeps == [1, 2, 4, 8, 16]


def test_metadata_does_not_retry_invalid_json(metadata_http):
    metadata_http.request.side_effect = lambda *a, **kw: io.BytesIO(b"invalid")
    with pytest.raises(json.JSONDecodeError):
        vimeo.public_recording("123")
    assert metadata_http.request.call_count == 1
    assert metadata_http.sleeps == []


@pytest.mark.parametrize("header", ["120", "http-date"])
def test_metadata_honors_retry_after(metadata_http, monkeypatch, header):
    now = 1_800_000_000
    monkeypatch.setattr(vimeo.time, "time", lambda: now)
    headers = Message()
    headers["Retry-After"] = (
        formatdate(now + 120, usegmt=True) if header == "http-date" else header
    )
    error = HTTPError("https://vimeo/config", 429, "limited", headers, None)
    metadata_http.request.side_effect = [error, metadata_http.response()]
    vimeo.public_recording("123")
    assert metadata_http.sleeps == [120]


@pytest.mark.parametrize(
    ("header", "expected"),
    [(None, 4), ("0", 4), ("invalid", 4), ("Thu, 01 Jan 1970 00:00:00 GMT", 4)],
)
def test_retry_delay_falls_back_to_exponential_backoff(
    metadata_http, header, expected, caplog
):
    assert vimeo.retry_delay(2, header) == expected
    if header == "invalid":
        assert "Invalid Vimeo Retry-After" in caplog.text


def test_retry_delay_adds_jitter_and_caps_local_delay(monkeypatch):
    monkeypatch.setattr(vimeo.random, "uniform", lambda start, end: end)
    assert vimeo.retry_delay(0, None) == 2
    assert vimeo.retry_delay(2, None) == 8
    assert vimeo.retry_delay(8, None) == 60


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
            audio_only = command[-1] in audio_paths
            return SimpleNamespace(
                stdout=json.dumps(
                    {
                        "streams": (
                            [{"codec_type": "audio"}]
                            if audio_only
                            else [{"codec_type": "video"}, {"codec_type": "audio"}]
                        )
                    }
                )
            )
        if "--simulate" not in command:
            output = command[command.index("--output") + 1]
            from pathlib import Path

            path = Path(output.replace("%(ext)s", "mp4"))
            path.write_bytes(b"complete-media")
            if command[command.index("--format") + 1].startswith("ba"):
                audio_paths.add(str(path))
        return SimpleNamespace(returncode=0)

    audio_paths = set()
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


def test_download_configures_exponential_retry_delays(tmp_path, downloader):
    vimeo.download("123", tmp_path)
    command = downloader[0]
    delays = [
        command[index + 1]
        for index, value in enumerate(command)
        if value == "--retry-sleep"
    ]
    assert delays == ["http:exp=1:60", "fragment:exp=1:60", "extractor:exp=1:60"]
    assert command[command.index("--retries") + 1] == "5"
    assert command[command.index("--fragment-retries") + 1] == "8"


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


@pytest.mark.parametrize("failure", ["write", "fsync", "replace"])
@pytest.mark.parametrize("existing", [False, True])
def test_receipt_failure_does_not_strand_recording(
    tmp_path, downloader, monkeypatch, failure, existing
):
    target = (
        vimeo.download("123", tmp_path)
        if existing
        else (
            tmp_path
            / vimeo.title_filename("2026_november_10_-_committee_on_budget (240p)")
        )
    )
    state_file = tmp_path / ".vimeo/123.json"
    original = target.read_bytes() if existing else None
    state = state_file.read_bytes() if existing else None

    if failure == "write":
        write_text = type(target).write_text

        def fail_write(path, *args, **kwargs):
            if path.name == "receipt.json":
                raise OSError("receipt write failed")
            return write_text(path, *args, **kwargs)

        monkeypatch.setattr(type(target), "write_text", fail_write)
    elif failure == "fsync":
        fsync = vimeo.os.fsync
        calls = 0

        def fail_receipt_fsync(descriptor):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("receipt fsync failed")
            return fsync(descriptor)

        monkeypatch.setattr(vimeo.os, "fsync", fail_receipt_fsync)
    else:
        replace = type(target).replace

        def fail_receipt_replace(path, destination):
            if path.name == "receipt.json":
                raise OSError("receipt replace failed")
            return replace(path, destination)

        monkeypatch.setattr(type(target), "replace", fail_receipt_replace)

    with pytest.raises(OSError, match="receipt"):
        vimeo.download("123", tmp_path, refresh=existing)

    if existing:
        assert target.read_bytes() == original
        assert state_file.read_bytes() == state
    else:
        assert not target.exists()
        assert not state_file.exists()
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
        lambda path, **kwargs: (_ for _ in ()).throw(ValueError("missing audio")),
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
    assert sorted(calls) == ["123", "456"]


def test_packaged_batch_list_default_is_used(monkeypatch):
    monkeypatch.setattr(vimeo, "load_dotenv", lambda: None)
    monkeypatch.setattr(vimeo.shutil, "which", lambda tool: tool)
    calls = []
    monkeypatch.setattr(vimeo, "download", lambda video, *a, **kw: calls.append(video))
    assert vimeo.main([]) == 0
    assert len(calls) == len(set(vimeo.DEFAULT_IDS.read_text().splitlines()))
    assert len(calls) > 0


@pytest.mark.parametrize("best_audio", [False, True])
def test_audio_only_download_and_receipt(tmp_path, downloader, best_audio):
    target = vimeo.download("123", tmp_path, audio_only=True, best_audio=best_audio)
    command = downloader[0]
    assert command[command.index("--format") + 1] == (
        "ba" if best_audio else "ba[format_id^=audio-low]/wa"
    )
    assert target.suffix == ".mp4"
    assert command[command.index("--remux-video") + 1] == "mp4"
    state = json.loads((tmp_path / ".vimeo/123.json").read_text())
    assert state["audio_only"] is True
    assert vimeo.download("123", tmp_path, audio_only=True) == target
    assert len(downloader) == 2
    assert (
        recording_jobs(
            tmp_path, image="worker", namespace="recordings", worker_input_dir="/inputs"
        )[0]["input.sha256"]
        == state["sha256"]
    )


@pytest.mark.parametrize("audio_only", [False, True])
def test_changing_download_mode_requires_refresh(tmp_path, downloader, audio_only):
    target = vimeo.download("123", tmp_path, audio_only=audio_only)
    state_file = tmp_path / ".vimeo/123.json"
    state = json.loads(state_file.read_text())
    if not audio_only:
        del state["audio_only"]  # Receipts from before audio-only support.
        state_file.write_text(json.dumps(state))
    with pytest.raises(ValueError, match="requires --refresh"):
        vimeo.download("123", tmp_path, audio_only=not audio_only)
    assert len(downloader) == 2
    assert (
        vimeo.download("123", tmp_path, audio_only=not audio_only, refresh=True)
        == target
    )
    assert json.loads(state_file.read_text())["audio_only"] is not audio_only


@pytest.mark.parametrize(
    ("streams", "audio_only", "valid"),
    [
        (["audio"], True, True),
        (["audio", "video"], True, False),
        (["video"], True, False),
        ([], True, False),
        (["audio"], False, False),
        (["audio", "video"], False, True),
    ],
)
def test_media_stream_validation(monkeypatch, tmp_path, streams, audio_only, valid):
    monkeypatch.setattr(
        vimeo.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=json.dumps({"streams": [{"codec_type": kind} for kind in streams]})
        ),
    )
    if valid:
        vimeo.validate_media(tmp_path / "input.mp4", audio_only=audio_only)
    else:
        with pytest.raises(ValueError, match="audio"):
            vimeo.validate_media(tmp_path / "input.mp4", audio_only=audio_only)


def test_cli_downloads_in_parallel_with_bounded_workers(
    tmp_path, downloader, monkeypatch
):
    monkeypatch.setattr(vimeo.shutil, "which", lambda tool: tool)
    metadata = tmp_path / "metadata.json"
    metadata.write_text(
        json.dumps(
            {
                video: f"2026-11-{int(video):02d}_budget.mp4"
                for video in ("1", "2", "3", "4")
            }
        )
    )
    barrier = Barrier(2, timeout=5)
    lock = Lock()
    active = peak = 0
    original = vimeo.subprocess.run

    def run(command, **kwargs):
        nonlocal active, peak
        if command[0] != "yt-dlp":
            return original(command, **kwargs)
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            barrier.wait()
            return original(command, **kwargs)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(vimeo.subprocess, "run", run)
    assert (
        vimeo.main(
            [
                "1",
                "2",
                "3",
                "4",
                "--workers",
                "2",
                "--audio-only",
                "--best-audio",
                "--metadata-file",
                str(metadata),
                "--output-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert peak == 2
    assert len(list(tmp_path.glob("*.mp4"))) == 4
    assert len(list((tmp_path / ".vimeo").glob("*.json"))) == 4
    for command in downloader:
        if command[0] == "yt-dlp":
            assert command[command.index("--format") + 1] == "ba"


@pytest.mark.parametrize("same_id", [False, True])
def test_concurrent_downloads_preserve_ownership(tmp_path, downloader, same_id):
    barrier = Barrier(2, timeout=5)

    def download(video):
        barrier.wait()
        return vimeo.download(video, tmp_path)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(download, "123")
        second = executor.submit(download, "123" if same_id else "456")
        if same_id:
            assert first.result(timeout=10) == second.result(timeout=10)
        else:
            results = []
            for future in (first, second):
                try:
                    results.append(future.result(timeout=10))
                except ValueError as error:
                    assert "another Vimeo" in str(error)
            assert len(results) == 1
    assert len(downloader) == 2
    assert len(list(tmp_path.glob("*.mp4"))) == 1
    assert len(list((tmp_path / ".vimeo").glob("*.json"))) == 1


@pytest.mark.parametrize("workers", ["0", "-1"])
def test_cli_rejects_nonpositive_workers(workers):
    with pytest.raises(SystemExit) as error:
        vimeo.main(["123", "--workers", workers])
    assert error.value.code == 2


@pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in ("yt-dlp", "ffmpeg", "ffprobe")),
    reason="Requires yt-dlp, FFmpeg and ffprobe",
)
@pytest.mark.parametrize("best_audio", [False, True])
def test_real_audio_only_download(tmp_path, monkeypatch, best_audio):
    from pipelines.transcription.workflow import extract_audio

    media = tmp_path / "media"
    media.mkdir()
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000",
            "-t",
            "1",
            "-c:a",
            "aac",
            str(media / "audio.m4a"),
        ],
        check=True,
    )
    info = media / "info.json"
    info.write_text(
        json.dumps(
            {
                "id": "123",
                "title": "2026-11-10_budget",
                "extractor": "generic",
                "webpage_url": "https://example.invalid/fixture",
                "formats": [
                    {
                        "format_id": "audio-low",
                        "url": (media / "audio.m4a").as_uri(),
                        "ext": "m4a",
                        "vcodec": "none",
                        "acodec": "aac",
                    },
                    {
                        "format_id": "video",
                        "url": (media / "must-not-download.mp4").as_uri(),
                        "ext": "mp4",
                        "vcodec": "h264",
                        "acodec": "none",
                        "height": 240,
                    },
                ],
            }
        )
    )
    monkeypatch.setattr(
        vimeo,
        "public_recording",
        lambda video: (
            "2026-11-10_budget",
            "https://example.invalid/fixture",
        ),
    )
    run = subprocess.run

    def run_local(command, **kwargs):
        if command[0] == "yt-dlp":
            command = [
                command[0],
                "--enable-file-urls",
                "--load-info-json",
                str(info),
                *command[1:-1],
            ]
        return run(command, **kwargs)

    monkeypatch.setattr(vimeo.subprocess, "run", run_local)
    target = vimeo.download(
        "123", tmp_path / "output", audio_only=True, best_audio=best_audio
    )
    vimeo.validate_media(target, audio_only=True)
    audio = tmp_path / "extracted.wav"
    extract_audio(target, audio)
    with wave.open(str(audio)) as stream:
        assert stream.getframerate() == 16000
        assert stream.getnchannels() == 1
        assert stream.getnframes() >= 16000
