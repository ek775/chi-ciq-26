"""Map the audio inventory to local Clerk manifests without network requests."""
import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


def video_ids(links):
    return set(re.findall(r"(?:video=|vimeo.com/(?:manage/videos/)?)(\d+)", " ".join(links)))


def normalize(body):
    return re.sub(r"[^a-z0-9]", "", body.lower().removeprefix("Committee on ".lower()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="Local chicago-city-council-archive directory")
    args = parser.parse_args()
    reference = Path(__file__).resolve().parents[1] / "data-reference"
    manifests = []
    by_video = defaultdict(list)
    for path in sorted((args.archive / "Meetings").rglob("manifest.json")):
        meeting = json.loads(path.read_text())
        meeting["manifest_path"] = path.relative_to(args.archive).as_posix()
        manifests.append(meeting)
        for vid in video_ids(meeting.get("video_links", [])):
            by_video[vid].append(meeting)
    if not manifests:
        parser.error("No meeting manifests found")
    filenames = (reference / "fuzzball-audio-filenames.txt").read_text().splitlines()
    assert len(filenames) == len(set(filenames)), "Duplicate audio filenames"
    rows = []
    for filename in filenames:
        match = re.fullmatch(r"vimeo-(\d+)-(?:audio-)?low\.mp4", filename)
        if match:
            vid = match[1]
            hits = by_video[vid]
            method = "vimeo_id"
        else:
            vid = None
            date, _, body = Path(filename).stem.partition("_")
            hits = [m for m in manifests if m["date"] == date and normalize(m["body"]) == normalize(body)]
            method = "filename_date_and_body"
        status = "unmatched" if not hits else ("matched" if len(hits) == 1 else "multiple_clerk_records")
        rows.append({
            "audio_filename": filename,
            "vimeo_id_from_filename": vid,
            "match_method": method,
            "status": status,
            "meetings": [{
                "meeting_id": m["id"], "date": m["date"], "body": m["body"],
                "meeting_url": m["source"], "video_urls": m.get("video_links", []),
                "archive_manifest": m["manifest_path"],
            } for m in hits],
        })
    counts = dict(Counter(row["status"] for row in rows))
    output = {
        "audio_inventory": "fuzzball-audio-filenames.txt",
        "archive_root": "chicago-city-council-archive",
        "notes": "Matches reflect archived metadata, not verification of audio contents. Preserve all Clerk records sharing a recording; filename-based matches need content verification.",
        "counts": counts, "audio_files": rows,
    }
    (reference / "audio-meeting-map.json").write_text(json.dumps(output, indent=2) + "\n")
    lines = ["# Audio files and council meetings", "",
             "Source: local `chicago-city-council-archive/Meetings/**/manifest.json` and `fuzzball-audio-filenames.txt`.", "",
             "Vimeo matches use the recording ID in the filename and the archive video URL. Filename matches use date and committee name only; audio contents have not been verified. Multiple Clerk records are retained rather than choosing one arbitrarily.", "",
             f"Audio files: {len(rows)}. Status counts: {json.dumps(counts, sort_keys=True)}.", "",
             "Regenerate: `python3 scripts/build_audio_meeting_map.py /path/to/chicago-city-council-archive`.", "",
             "| Audio file | Meeting date | Council body | Clerk meeting links | Match evidence |", "| --- | --- | --- | --- | --- |"]
    for row in rows:
        hits = row["meetings"]
        dates = "; ".join(sorted({m["date"] for m in hits})) or "—"
        bodies = "; ".join(sorted({m["body"] for m in hits})) or "—"
        links = "<br>".join(f'[{m["meeting_id"]}]({m["meeting_url"]})' for m in hits) or "Unmatched"
        evidence = f'Vimeo {row["vimeo_id_from_filename"]}' if row["match_method"] == "vimeo_id" else "Filename date + committee (unverified contents)"
        if len(hits) > 1:
            evidence += f"; {len(hits)} Clerk records"
        lines.append(f'| `{row["audio_filename"]}` | {dates} | {bodies} | {links} | {evidence} |')
    (reference / "audio-meeting-map.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"audio_files": len(rows), **counts}, sort_keys=True))


if __name__ == "__main__":
    main()
