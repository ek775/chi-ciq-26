"""Acquire public Vimeo recordings and atomically publish validated MP4 inputs."""

import argparse
import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

from dotenv import load_dotenv

from databases.meetings.metadata import parse_filename

logger = logging.getLogger(__name__)
DEFAULT_IDS = Path(__file__).with_name("vimeo-ids.txt")


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
    with urlopen(request, timeout=30) as response:
        config = json.load(response)
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
    filename = re.sub(r"\s+", "_", title.strip()) + ".mp4"
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


def validate_media(path: Path) -> None:
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
    if not {"audio", "video"} <= kinds:
        raise ValueError("Downloaded MP4 must contain both video and audio")


def download(
    video: str,
    output_dir: Path,
    *,
    filename: str | None = None,
    dry_run: bool = False,
    best_audio: bool = False,
    refresh: bool = False,
) -> Path | None:
    output_dir.mkdir(parents=True, exist_ok=True)
    state_dir = output_dir / ".vimeo"
    state_dir.mkdir(exist_ok=True)
    state_file = state_dir / f"{video}.json"
    # Shared output storage must support POSIX locks and atomic rename.
    with (state_dir / ".download.lock").open("a") as lock:
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
            validate_media(temporary)
            sha256 = digest(temporary)
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            temporary.replace(target)
            receipt = Path(directory) / "receipt.json"
            receipt.write_text(
                json.dumps({"video_id": video, "filename": filename, "sha256": sha256}),
                encoding="utf-8",
            )
            with receipt.open("rb") as stream:
                os.fsync(stream.fileno())
            receipt.replace(state_file)
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
        "--refresh", action="store_true", help="Redownload managed recordings"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
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
    for video in videos:
        try:
            download(
                video,
                args.output_dir,
                filename=names.get(video),
                dry_run=args.dry_run,
                best_audio=args.best_audio,
                refresh=args.refresh,
            )
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            logger.exception("Vimeo %s acquisition failed", video)
            failures.append(video)
    if failures:
        logger.error("Failed video IDs: %s", ", ".join(failures))
        return 1
    return 0
