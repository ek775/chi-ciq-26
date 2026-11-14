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
# Audio-only acquisition, with four recordings downloading at once:
docker compose --profile downloads run --build --rm downloads --audio-only --workers 4
# Once acquisition succeeds, prepare the worker fan-out:
chi-ciq-dispatch --input-dir data/mp4 --output-dir data/dispatch
```

The downloads container uses CPU, FFmpeg/ffprobe and `.[downloads]` (yt-dlp),
without inference models or database access. It shares `data/mp4` with the
transcription input mount. It downloads only public Vimeo recordings. By default,
it retains the original 240p-or-lower video selection. `--audio-only` selects
an audio-only stream directly, without downloading video or transcoding audio;
it fails if Vimeo offers no audio-only stream. Audio is remuxed into an MP4
container (not an M4A filename) so existing discovery, meeting metadata, and
transcription audio extraction continue to work unchanged.
Both modes prefer low-bandwidth audio; `--best-audio` selects the best available
audio instead. `--best-audio` alone still downloads video.
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
ffprobe confirms both audio and video, or audio without video in `--audio-only`
mode. Only then is the complete MP4 atomically published; the dispatcher ignores
scratch subdirectories. A `.vimeo/` receipt records each ID's stable filename,
SHA-256, and audio-only mode. Repeated acquisition skips managed files matching
their receipts. `--refresh` explicitly redownloads managed files
and atomically replaces them, leaving the previous file intact on download or
media-validation failure. Unmanaged files and files owned by another ID are
never overwritten. Invalid receipts fail visibly, rather than treating arbitrary
nonempty files as completed downloads.
Locally modified managed files require `--refresh`; they are not silently
overwritten merely because their checksum differs.
Switching an existing recording between video and audio-only mode also requires
`--refresh`. Older receipts without a mode are treated as video downloads.

The output filesystem must support POSIX locks and atomic rename. Acquisitions
run in parallel with `--workers` (default: 4; use 1 for sequential acquisition).
Each recording still uses up to six concurrent yt-dlp fragment requests, so
reduce workers if the connection or CDN is overloaded. Locks serialize requests
for the same ID or output filename, including across downloader processes,
while unrelated recordings download concurrently. Start dispatch only after
the acquisition command succeeds. Failures are logged with video IDs, the batch
continues, and the command exits nonzero if any failed. Previously successful
files remain.

Transient metadata failures (HTTP 408, 429, 500, 502, 503, 504 and connection
errors/timeouts) are retried up to five times with exponential backoff and jitter.
Metadata requests honor `Retry-After` seconds or HTTP dates when they request a
longer wait. Other HTTP errors and invalid metadata fail immediately. Retries
are logged with the recording ID and delay; exhausted retries fail the recording.
yt-dlp HTTP, fragment, and extractor retries use exponential delays starting at
one second and capped at 60 seconds (five HTTP and eight fragment retries).
Backoff is per request, not a batch-wide rate limiter; lower `--workers` if
Vimeo continues to throttle concurrent downloads.
