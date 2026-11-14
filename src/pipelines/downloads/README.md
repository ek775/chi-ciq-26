# Public Vimeo acquisition

[Pipeline overview](../README.md) | [Transcription handoff](../transcription/README.md)

Run shell examples from the repository root, not this module directory.

## Public Vimeo acquisition

The moved ID list is packaged at `src/pipelines/downloads/vimeo-ids.txt`.
`chi-ciq-download-vimeo` uses it by default when no positional IDs or explicit
`--batch-file` are supplied. The moved `download-vimeo.py` path remains a wrapper;
the importable implementation is `pipelines.downloads.vimeo`.

```sh
docker compose --profile downloads run --build --rm downloads
# Once acquisition succeeds, prepare the worker fan-out:
chi-ciq-dispatch --input-dir data/mp4 --output-dir data/dispatch
```

The downloads container uses CPU, FFmpeg/ffprobe and `.[downloads]` (yt-dlp),
without inference models or database access. It shares `data/mp4` with the
transcription input mount. It downloads only public Vimeo recordings, retaining
the original 240p-or-lower selection and optional `--best-audio`.
`--dry-run` contacts Vimeo and reports formats but publishes no recording; it
is **not** an offline test.

Vimeo titles must contain the actual meeting date and type in the existing
filename format. Whitespace is normalized to underscores; missing meeting
metadata is an error, not inferred from the upload date. For titles that do not
conform, supply `--metadata-file` containing a JSON object such as
`{"1234158910": "2026-11-10_committee_on_budget.mp4"}`. Mount this file into the
downloads container and pass its container path. All mappings are validated
before downloading, and two IDs cannot own the same output filename.

Downloads stay in private scratch subdirectories until yt-dlp succeeds and
ffprobe confirms both audio and video. Only then is the complete MP4 atomically
published; the dispatcher ignores scratch subdirectories. A `.vimeo/` receipt
records each ID's stable filename and SHA-256. Repeated acquisition skips managed
files matching their receipts. `--refresh` explicitly redownloads managed files
and atomically replaces them, leaving the previous file intact on download or
media-validation failure. Unmanaged files and files owned by another ID are
never overwritten. Invalid receipts fail visibly, rather than treating arbitrary
nonempty files as completed downloads.
Locally modified managed files require `--refresh`; they are not silently
overwritten merely because their checksum differs.

The output filesystem must support POSIX locks and atomic rename. Acquisitions
sharing this output are serialized; start dispatch only after the acquisition
command succeeds. Failures are logged with video IDs, the batch continues, and
the command exits nonzero if any failed. Previously successful files remain.
No Vimeo network download or Fuzzball cluster execution was tested during the
hackathon networking restriction.
