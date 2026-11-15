# Document pipeline

[Pipeline overview](../README.md) | [Documents database](../../databases/documents/README.md)

Finite, rerunnable document ingestion container. It recursively reads a mounted
directory tree and publishes extracted text plus local sentence-transformer
embeddings to a **separate** PostgreSQL/pgvector documents database.
Acquisition and scheduling remain upstream responsibilities.

The current repository corpus has 202 `.txt` files under
`data-reference/Transcripts`. For Fuzzball, the verified archive is on
`team8/volume_test`, mounted at `/volume`, with documents and transcripts nested
under each meeting. Run:

```sh
chi-ciq-ingest-documents --input-dir /volume/chicago-city-council-archive/Meetings --namespace transcripts
```

The `.vtt` captions and database dumps elsewhere under `data/` are not inputs:
VTT is unsupported by the extractor, and database archives are for backup only.
This recursively ingests supported files from all meeting subdirectories,
including `Documents`, `Transcripts`, and `Legislation`. The existing namespace
`transcripts` is retained for stable source identities. macOS `._` resource-fork
sidecars are ignored, including descendants of sidecar directories.
There is no top-level `/datasets/Transcripts` directory.
[fuzzball.yaml](../../../fuzzball.yaml) deploys four CPU ingestion
tasks alongside transcription, then analytics and the final web service.
See the [deployment guide](../../../README.md#fuzzball-deployment).

For parallel ingestion, pass `--shard-index INDEX --shard-count COUNT`.
Indices are zero-based and must cover `0..COUNT-1`; all workers use the same
root and namespace. Relative-path hashing assigns each file to exactly one
worker without changing its database key. Directory creation remains locked
and preserves empty directories. The default count of one preserves unsharded
behavior; empty shards succeed. Freeze the input tree during the array and
protect inputs with cluster filesystem ACLs.

## Local run

Set the shared `POSTGRES_PASSWORD` and, optionally, `DOCUMENTS_INPUT_DIR` in
the root `.env` (see [.env.example](../../../.env.example)). The default input
is `data-reference`, including its `Transcripts` directory. Passwords are passed
separately from database URLs, so they do not need URL encoding.
Run from the repository root:

```sh
docker compose --profile documents up -d documents-db
docker compose --profile documents run --build --rm documents-backup
docker compose --profile documents run --build --rm documents-migrate
docker compose --profile documents run --build --rm documents
docker compose --profile documents run --build --rm documents-backup
```

Select jobs explicitly; do not start all profile services simultaneously.
The input mount is read-only. PostgreSQL data and model cache use dedicated
volumes, separate from transcription. The image installs CPU-only PyTorch wheels.
CPU inference is the default; no Whisper,
pyannote, FFmpeg, or gated models are required. First-time builds/model downloads
require network access. GPU use requires an explicitly configured GPU container.

To run against the archive mounted on this machine, set the Compose host path
to the `Meetings` directory (not the archive root):

```sh
DOCUMENTS_INPUT_DIR="/run/media/ek224/New Volume/chicago-city-council-archive/Meetings" \
  docker compose --profile documents run --build --rm documents \
  chi-ciq-ingest-documents --input-dir /inputs --namespace clerk-archive
```

The archive's meeting-level `manifest.json` files pair each PDF with its
`text_path` sidecar and original byte count/SHA-256. The pipeline uses that
sidecar (which includes OCR text where the archive produced it), checks the
original PDF against the manifest, and does not index the sidecar as a second
document. If a prior run indexed that sidecar separately, it is removed after
the PDF is safely published. Sidecar changes trigger reprocessing. PDFs without
a sidecar use the native PDF extractor; manifest-listed zero-byte PDFs are
retained with empty text and a warning. A sidecar containing only page labels
is likewise retained without chunks. Archive OCR covers scanned meeting
attachments, not all linked legislation, so some PDFs intentionally have no
searchable text.

```sh
# Override the container command, retaining the tree mount:
docker compose --profile documents run --build --rm documents \
  chi-ciq-ingest-documents --input-dir /inputs --namespace civic \
  --chunk-characters 1000 --text-model sentence-transformers/all-MiniLM-L6-v2

# Host installation and execution:
python -m pip install -e ".[documents]"
chi-ciq-documents-database migrate
chi-ciq-ingest-documents --input-dir data-reference --namespace documents
```

Host commands use `DOCUMENTS_DATABASE_URL`, falling back to `DATABASE_URL` only
when absent. Always point them at the documents database, not meetings.

## Formats and content

- TXT, Markdown (`.md`, `.markdown`): UTF-8, optional BOM, original text retained.
- PDF: embedded text extracted in page order; encrypted PDFs and PDFs without
  extractable text fail explicitly unless an archive manifest supplies a
  companion text sidecar. OCR and visual/table-layout reconstruction are not
  included in the generic PDF fallback.
- DOCX: body paragraphs and tables in document order. Headers, footers, images,
  and embedded objects are not extracted.
- HTML: text extracted without executing scripts or fetching linked resources;
  script, style, and template content is excluded.

Suffix matching is case-insensitive. Other files are ignored. Symlinks,
malformed documents, invalid UTF-8 plain text, and NUL-containing text fail the
job rather than silently publishing incomplete results. For binary formats the
stored content is **extracted text**, not an archive of the original file.

Every directory, including the root and empty directories, has a mapped chunk
table. Every supported file has a `documents` record with full text and its
relative path. Empty text files are preserved with zero chunks and no vector.
Text chunks retain start-inclusive/end-exclusive character offsets into the
extracted text. `--chunk-characters` bounds each chunk (default 1000), preferring
whitespace boundaries. Long embedding inputs use the **same shared embedder as
transcription**: token-aware windows are weighted and normalized instead of
silently truncating. Default model: `sentence-transformers/all-MiniLM-L6-v2`
(384 dimensions). Model name and actual vector dimensions are recorded.

## Update contract

Re-run the same command and namespace for initial builds and periodic updates.
Byte-identical files with the same model, extraction version, and chunk settings
are skipped before model loading; archive PDFs also include their sidecar hash
in the processing signature. Use `--force` after an in-place model revision or
dependency update if the model name and extraction version did not change.
Source PDF bytes are checked against the archive manifest, and the text
sidecar's hash makes its changes trigger reprocessing. File replacements are
atomic: old full text and chunks survive any extraction, inference, or database
failure. Concurrent workers serialize the PDF and its associated sidecar using
PostgreSQL advisory locks. Directory table creation is also serialized and
transactional.

Publication is per file, **not one transaction for the entire tree**. A failed
job exits nonzero; already-published earlier files remain. Re-running resumes
via unchanged-file checks. Removed files/directories are retained by design,
except for a PDF's obsolete separately indexed archive text sidecar; renaming
creates a new source without deleting the old one. Namespace identifies an
input tree independently of its mount location. Use distinct namespaces for
distinct roots. No polling daemon, scheduler, document web API, or Fuzzball
dispatcher is included.

## Validation

```sh
python -m pip install -e ".[dev,documents]"
TEST_DOCUMENTS_DATABASE_URL="$DOCUMENTS_DATABASE_URL" \
  python -m pytest -q tests/test_documents.py
# Optional real-model check (downloads the default model; confirms 384-D vectors):
RUN_DOCUMENT_EMBEDDING_TESTS=1 TEST_DOCUMENTS_DATABASE_URL="$DOCUMENTS_DATABASE_URL" \
  python -m pytest -q tests/test_documents.py -k real_embeddings
```

Without a test database, parser/chunking and archive-manifest tests run and
database tests skip.
Integration tests use isolated schemas and prove table mirroring, vectors,
updates, rollback, namespace separation, CLI behavior, and concurrent runs.
