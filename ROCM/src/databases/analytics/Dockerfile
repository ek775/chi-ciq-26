# CPU-only database client: no GPU-specific changes.
FROM python:3.12-slim-trixie

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-17 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY src/shared ./src/shared
COPY src/databases ./src/databases
RUN python -m pip install ".[database]"

CMD ["chi-ciq-analytics-database", "check"]
