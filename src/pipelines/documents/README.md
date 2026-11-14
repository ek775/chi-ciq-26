# Document pipeline

[Pipeline overview](../README.md) | [Documents database](../../databases/documents/README.md)

Finite, rerunnable document ingestion container. It recursively reads a mounted
directory tree and publishes extracted text plus local sentence-transformer
embeddings to a **separate** PostgreSQL/pgvector documents database.
Acquisition and scheduling remain upstream responsibilities.

The current repository corpus has 202 `.txt` files under
`data-reference/Transcripts`. For Fuzzball, stage that directory at
`/datasets/Transcripts` and run:

```sh
chi-ciq-ingest-documents --input-dir /datasets/Transcripts --namespace transcripts
```

The `.vtt` captions and database dumps elsewhere under `data/` are not inputs:
VTT is unsupported by the extractor, and database archives are for backup only.
The shared volume's contents could not be verified through the CLI because the
inspection workflow remained queued, so stage the transcript tree before
ingestion. [fuzzball.yaml](../../../fuzzball.yaml) deploys four CPU ingestion
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

Set `DOCUMENTS_POSTGRES_PASSWORD` and `DOCUMENTS_INPUT_DIR` in the root `.env`
(see [.env.example](../../../.env.example)). The default input is `data-reference`,
including its `Transcripts` directory. Use URL-safe development passwords.
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
  extractable text fail explicitly. OCR and visual/table-layout reconstruction
  are not included.
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
are skipped before model loading. Use `--force` after an in-place model revision
or dependency update if the model name and extraction version did not change.
Hashing and parsing use the same byte snapshot. File replacements are atomic:
old full text and chunks survive any extraction, inference, or database failure.
Concurrent workers serialize the same file using PostgreSQL advisory locks.
Directory table creation is also serialized and transactional.

Publication is per file, **not one transaction for the entire tree**. A failed
job exits nonzero; already-published earlier files remain. Re-running resumes
via unchanged-file checks. Removed files/directories are retained by design;
renaming creates a new source without deleting the old one. Namespace identifies
an input tree independently of its mount location. Use distinct namespaces for
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

Without a test database, parser/chunking tests run and database tests skip.
Integration tests use isolated schemas and prove table mirroring, vectors,
updates, rollback, namespace separation, CLI behavior, and concurrent runs.
