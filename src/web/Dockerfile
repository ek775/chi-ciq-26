FROM python:3.12-slim-trixie

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml ./
COPY src/shared ./src/shared
COPY src/web ./src/web
COPY src/databases/__init__.py ./src/databases/__init__.py
COPY src/databases/meetings ./src/databases/meetings
COPY src/databases/analytics ./src/databases/analytics
RUN python -m pip install ".[web]"

EXPOSE 8080
CMD ["chi-ciq-web"]
