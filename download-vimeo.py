#!/usr/bin/env python3
"""Save public Vimeo recordings at 240p or lower as MP4s with audio.

Requires Python 3, yt-dlp and FFmpeg on PATH.
macOS: brew install yt-dlp ffmpeg

Examples:
  python3 download-vimeo.py "https://vimeo.com/showcase/8928495?video=1234158910"
  python3 download-vimeo.py 1234158910 --output-dir recordings
  python3 download-vimeo.py 1234158910 --dry-run
  python3 download-vimeo.py --batch-file videos.txt --output-dir recordings
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen


def vimeo_id(value):
    if re.fullmatch(r"[0-9]+", value):
        return value
    url = urlsplit(value)
    if url.scheme != "https" or url.hostname not in {
        "vimeo.com", "www.vimeo.com", "player.vimeo.com"
    }:
        raise argparse.ArgumentTypeError("Enter a public Vimeo URL or numeric video ID.")
    video = parse_qs(url.query).get("video", [""])[0]
    if re.fullmatch(r"[0-9]+", video):
        return video
    match = re.search(r"(?:^/|/videos?/)([0-9]+)(?:/|$)", url.path)
    if match and not url.path.startswith("/showcase/"):
        return match[1]
    raise argparse.ArgumentTypeError("Use a specific video link rather than a showcase or profile URL.")


def public_playlist(video):
    request = Request(
        f"https://player.vimeo.com/video/{video}/config",
        headers={"Referer": "https://vimeo.com/", "User-Agent": "Mozilla/5.0"},
    )
    with urlopen(request, timeout=30) as response:
        config = json.load(response)
    hls = config["request"]["files"]["hls"]
    return hls["cdns"][hls["default_cdn"]]["url"]


def download(video, output_dir, dry_run, best_audio):
    target = output_dir / f"vimeo-{video}-low.mp4"
    if target.is_file() and target.stat().st_size > 0 and not dry_run:
        print(f"{video}: already saved", flush=True)
        return True

    try:
        playlist = public_playlist(video)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"{video}: could not load the public Vimeo playlist: {error}", flush=True)
        return False

    formats = "bv[height<=240]+ba/b[height<=240]"
    if not best_audio:
        formats = "bv[height<=240]+ba[format_id^=audio-low]/bv[height<=240]+wa/b[height<=240]"
    command = [
        "yt-dlp", "--ignore-config", "--no-playlist", "--continue", "--no-overwrites",
        "--concurrent-fragments", "6", "--retries", "5", "--fragment-retries", "8",
        "--abort-on-unavailable-fragments", "--merge-output-format", "mp4",
        "--remux-video", "mp4", "--format", formats,
        "--output", str(target.with_suffix(".%(ext)s")),
    ]
    if dry_run:
        command += ["--simulate", "--print", "%(resolution)s | %(format_id)s"]
    result = subprocess.run([*command, playlist], check=False)
    return result.returncode == 0 and (dry_run or (target.is_file() and target.stat().st_size > 0))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("videos", nargs="*", type=vimeo_id, help="Public Vimeo links or numeric IDs")
    parser.add_argument("--batch-file", type=Path, help="UTF-8 file with one link or ID per line")
    parser.add_argument("--output-dir", type=Path, default=Path("."), help="Directory for the MP4s")
    parser.add_argument("--dry-run", action="store_true", help="Report formats without downloading")
    parser.add_argument("--best-audio", action="store_true", help="Prefer higher-quality audio")
    args = parser.parse_args()

    videos = list(args.videos)
    if args.batch_file:
        try:
            lines = args.batch_file.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError) as error:
            parser.error(f"{args.batch_file}: {error}")
        for number, line in enumerate(lines, 1):
            entry = line.strip()
            if entry and not entry.startswith("#"):
                try:
                    videos.append(vimeo_id(entry))
                except (argparse.ArgumentTypeError, ValueError) as error:
                    parser.error(f"{args.batch_file}:{number}: {error}")

    videos = list(dict.fromkeys(videos))
    if not videos:
        parser.error("Supply at least one video link or ID, or use --batch-file.")
    for tool in ("yt-dlp", "ffmpeg"):
        if shutil.which(tool) is None:
            parser.error(f"{tool} is required on PATH. On macOS, run brew install yt-dlp ffmpeg.")
    if not args.dry_run:
        args.output_dir.mkdir(parents=True, exist_ok=True)

    failed = []
    for index, video in enumerate(videos, 1):
        print(f"[{index}/{len(videos)}] Vimeo {video}", flush=True)
        if not download(video, args.output_dir, args.dry_run, args.best_audio):
            failed.append(video)
    if failed:
        print("Failed video IDs:", ", ".join(failed), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
