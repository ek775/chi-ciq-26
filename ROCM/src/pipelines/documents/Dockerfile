# CPU-only document ingestion: no GPU-specific changes.
FROM python:3.12-slim-trixie

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml ./
COPY src/shared ./src/shared
COPY src/pipelines/__init__.py ./src/pipelines/__init__.py
COPY src/pipelines/documents ./src/pipelines/documents
COPY src/databases ./src/databases
RUN python -m pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && python -m pip install ".[documents]"

CMD ["chi-ciq-ingest-documents", "--input-dir", "/inputs", "--namespace", "documents"]
