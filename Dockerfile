FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY pyproject.toml ./
COPY src ./src
RUN python -m pip install --editable ".[dev]"

CMD ["sleep", "infinity"]
