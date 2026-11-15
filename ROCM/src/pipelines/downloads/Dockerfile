# CPU-only downloads: no GPU-specific changes.
FROM python:3.12-slim-trixie

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY src/shared ./src/shared
COPY src/pipelines/__init__.py ./src/pipelines/__init__.py
COPY src/pipelines/downloads ./src/pipelines/downloads
COPY src/databases/__init__.py ./src/databases/__init__.py
COPY src/databases/meetings ./src/databases/meetings
RUN python -m pip install ".[downloads]"

CMD ["chi-ciq-download-vimeo", "--output-dir", "/outputs"]
