"""Acquire public Vimeo recordings and atomically publish validated MP4 inputs."""

import argparse
import fcntl
import hashlib
import json
import logging
import os
import random
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from datetime import UTC
from email.utils import parsedate_to_datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

from dotenv import load_dotenv

from databases.meetings.metadata import parse_filename
from shared.logging import configure_logging

logger = logging.getLogger(__name__)
DEFAULT_IDS = Path(__file__).with_name("vimeo-ids.txt")
METADATA_RETRIES = 5


def retry_delay(attempt: int, retry_after: str | None) -> float:
    delay = min(60.0, 2**attempt + random.uniform(0, 2**attempt))
    if retry_after is not None:
        try:
            if retry_after.strip().isdigit():
                requested = float(retry_after)
            else:
                date = parsedate_to_datetime(retry_after)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=UTC)
                requested = date.timestamp() - time.time()
            delay = max(delay, requested)
        except (ValueError, TypeError, OverflowError):
            logger.warning("Invalid Vimeo Retry-After header: %r", retry_after)
    return delay


def vimeo_id(value: str) -> str:
    if re.fullmatch(r"[0-9]+", value):
        return value
    url = urlsplit(value)
    if url.scheme != "https" or url.hostname not in {
        "vimeo.com",
        "www.vimeo.com",
        "player.vimeo.com",
    }:
        raise argparse.ArgumentTypeError(
            "Enter a public Vimeo URL or numeric video ID."
        )
    video = parse_qs(url.query).get("video", [""])[0]
    if re.fullmatch(r"[0-9]+", video):
        return video
    match = re.search(r"(?:^/|/videos?/)([0-9]+)(?:/|$)", url.path)
    if match and not url.path.startswith("/showcase/"):
        return match[1]
    raise argparse.ArgumentTypeError(
        "Use a specific public video link, not a showcase."
    )


def public_recording(video: str) -> tuple[str, str]:
    request = Request(
        f"https://player.vimeo.com/video/{video}/config",
        headers={"Referer": "https://vimeo.com/", "User-Agent": "Mozilla/5.0"},
    )
    for attempt in range(METADATA_RETRIES + 1):
        retry_after = None
        try:
            with urlopen(request, timeout=30) as response:
                config = json.load(response)
            break
        except HTTPError as error:
            error.close()
            if error.code not in {408, 429, 500, 502, 503, 504}:
                raise
            if attempt == METADATA_RETRIES:
                raise
            retry_after = error.headers.get("Retry-After")
            reason = f"HTTP {error.code}"
        except (URLError, TimeoutError, ConnectionError) as error:
            if attempt == METADATA_RETRIES:
                raise
            reason = str(error)
        delay = retry_delay(attempt, retry_after)
        logger.warning(
            "%s: Vimeo metadata request failed (%s); retry %d/%d in %.1fs",
            video,
            reason,
            attempt + 1,
            METADATA_RETRIES,
            delay,
        )
        time.sleep(delay)
    hls = config["request"]["files"]["hls"]
    playlist = hls["cdns"][hls["default_cdn"]]["url"]
    title = config["video"]["title"]
    if not isinstance(title, str) or not isinstance(playlist, str):
        raise TypeError("Vimeo returned invalid title or playlist")
    return title, playlist


def validate_filename(filename: str) -> str:
    if Path(filename).name != filename or "\\" in filename:
        raise ValueError("Recording filename must not contain directory components")
    parse_filename(filename)
    return filename


def title_filename(title: str) -> str:
    filename = re.sub(r"\s+", "_", title.strip())
    filename = re.sub(r"_\((\d+p)\)$", r" (\1)", filename) + ".mp4"
    try:
        return validate_filename(filename)
    except ValueError as error:
        raise ValueError(
            f"Vimeo title is not meeting metadata: {title!r}; "
            "provide --metadata-file mapping this ID to a dated meeting filename"
        ) from error


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_media(path: Path, *, audio_only: bool = False) -> None:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    kinds = {item["codec_type"] for item in json.loads(result.stdout)["streams"]}
    if audio_only:
        if "audio" not in kinds or "video" in kinds:
            raise ValueError(
                "Downloaded audio-only MP4 must contain audio and no video"
            )
    elif not {"audio", "video"} <= kinds:
        raise ValueError("Downloaded MP4 must contain both video and audio")


def download(
    video: str,
    output_dir: Path,
    *,
    filename: str | None = None,
    dry_run: bool = False,
    best_audio: bool = False,
    audio_only: bool = False,
    refresh: bool = False,
) -> Path | None:
    output_dir.mkdir(parents=True, exist_ok=True)
    state_dir = output_dir / ".vimeo"
    state_dir.mkdir(exist_ok=True)
    state_file = state_dir / f"{video}.json"
    # Shared output storage must support POSIX locks and atomic rename.
    with ExitStack() as locks:
        lock = locks.enter_context((state_dir / f".{video}.lock").open("a"))
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = None
        if state_file.exists():
            state = json.loads(state_file.read_text(encoding="utf-8"))
            previous = validate_filename(state["filename"])
            if filename is not None and filename != previous:
                raise ValueError(
                    f"{video}: changing its established filename is unsafe"
                )
            filename = previous
            target = output_dir / filename
            if (
                not refresh
                and not dry_run
                and state.get("audio_only", False) != audio_only
            ):
                raise ValueError(
                    f"{video}: changing audio-only mode requires --refresh"
                )
            if not refresh and not dry_run and target.is_file():
                if digest(target) != state["sha256"]:
                    raise ValueError(
                        f"{video}: managed file differs from receipt; "
                        "use --refresh to explicitly replace it"
                    )
                logger.info("%s: already published %s", video, filename)
                return target
        title, playlist = public_recording(video)
        filename = validate_filename(filename) if filename else title_filename(title)
        target = output_dir / filename
        # Different IDs may resolve to the same title; serialize that filename too.
        name_hash = hashlib.sha256(filename.encode("utf-8")).hexdigest()
        name_lock = locks.enter_context(
            (state_dir / f".filename-{name_hash}.lock").open("a")
        )
        fcntl.flock(name_lock, fcntl.LOCK_EX)
        for other in state_dir.glob("*.json"):
            if other != state_file:
                owner = json.loads(other.read_text(encoding="utf-8"))
                if owner["filename"] == filename:
                    raise ValueError(f"{filename} is already owned by another Vimeo ID")
        if target.exists() and state is None:
            raise FileExistsError(f"Unmanaged recording would be overwritten: {target}")
        formats = "bv[height<=240]+ba/b[height<=240]"
        if not best_audio:
            formats = (
                "bv[height<=240]+ba[format_id^=audio-low]/"
                "bv[height<=240]+wa/b[height<=240]"
            )
        if audio_only:
            formats = "ba" if best_audio else "ba[format_id^=audio-low]/wa"
        with TemporaryDirectory(prefix=".vimeo-", dir=output_dir) as directory:
            temporary = Path(directory) / "recording.mp4"
            command = [
                "yt-dlp",
                "--ignore-config",
                "--no-playlist",
                "--concurrent-fragments",
                "6",
                "--retries",
                "5",
                "--fragment-retries",
                "8",
                "--retry-sleep",
                "http:exp=1:60",
                "--retry-sleep",
                "fragment:exp=1:60",
                "--retry-sleep",
                "extractor:exp=1:60",
                "--abort-on-unavailable-fragments",
                "--merge-output-format",
                "mp4",
                "--remux-video",
                "mp4",
                "--format",
                formats,
                "--output",
                str(temporary.with_suffix(".%(ext)s")),
            ]
            if dry_run:
                command += ["--simulate", "--print", "%(resolution)s | %(format_id)s"]
            subprocess.run([*command, playlist], check=True)
            if dry_run:
                logger.info("%s: would publish %s", video, filename)
                return None
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise RuntimeError(f"{video}: yt-dlp produced no complete MP4")
            validate_media(temporary, audio_only=audio_only)
            sha256 = digest(temporary)
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            receipt = Path(directory) / "receipt.json"
            receipt.write_text(
                json.dumps(
                    {
                        "video_id": video,
                        "filename": filename,
                        "sha256": sha256,
                        "audio_only": audio_only,
                    }
                ),
                encoding="utf-8",
            )
            with receipt.open("rb") as stream:
                os.fsync(stream.fileno())
            backup = Path(directory) / "previous.mp4"
            had_target = target.is_file()
            if had_target:
                shutil.copyfile(target, backup)
                with backup.open("rb") as stream:
                    os.fsync(stream.fileno())
            temporary.replace(target)
            try:
                receipt.replace(state_file)
            except Exception:
                if had_target:
                    backup.replace(target)
                else:
                    target.unlink(missing_ok=True)
                raise
            logger.info("%s: published %s", video, filename)
            return target


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("videos", nargs="*", type=vimeo_id)
    parser.add_argument("--batch-file", type=Path)
    parser.add_argument(
        "--metadata-file", type=Path, help="JSON mapping IDs to MP4 filenames"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(os.environ.get("TRANSCRIPTION_INPUT_DIR", "data/mp4")),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--best-audio", action="store_true")
    parser.add_argument(
        "--audio-only",
        action="store_true",
        help="Download only audio, remuxed into an MP4 for transcription",
    )
    parser.add_argument(
        "--workers", type=int, default=4, help="Concurrent recordings (default: 4)"
    )
    parser.add_argument(
        "--refresh", action="store_true", help="Redownload managed recordings"
    )
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    configure_logging("downloads")
    videos = list(args.videos)
    batch = args.batch_file or (DEFAULT_IDS if not videos else None)
    if batch is not None:
        for number, line in enumerate(
            batch.read_text(encoding="utf-8-sig").splitlines(), 1
        ):
            entry = line.strip()
            if entry and not entry.startswith("#"):
                try:
                    videos.append(vimeo_id(entry))
                except argparse.ArgumentTypeError as error:
                    parser.error(f"{batch}:{number}: {error}")
    videos = list(dict.fromkeys(videos))
    if not videos:
        parser.error("Supply at least one public Vimeo ID or link")
    names = {}
    if args.metadata_file is not None:
        names = json.loads(args.metadata_file.read_text(encoding="utf-8"))
        if not isinstance(names, dict):
            parser.error("--metadata-file must contain a JSON object")
        for video, filename in names.items():
            if vimeo_id(video) != video or not isinstance(filename, str):
                parser.error(
                    "Metadata keys must be numeric Vimeo IDs and values filenames"
                )
            validate_filename(filename)
        if len(set(names.values())) != len(names):
            parser.error("Metadata filenames must be unique per Vimeo ID")
    for tool in ("yt-dlp", "ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            parser.error(
                f"{tool} is required; use the downloads container or install it"
            )
    failures = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                download,
                video,
                args.output_dir,
                filename=names.get(video),
                dry_run=args.dry_run,
                best_audio=args.best_audio,
                audio_only=args.audio_only,
                refresh=args.refresh,
            ): video
            for video in videos
        }
        for future in as_completed(futures):
            video = futures[future]
            try:
                future.result()
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                RuntimeError,
                subprocess.SubprocessError,
            ):
                logger.exception("Vimeo %s acquisition failed", video)
                failures.append(video)
    if failures:
        logger.error("Failed video IDs: %s", ", ".join(failures))
        return 1
    return 0
