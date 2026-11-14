FROM python:3.12-slim-trixie

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml ./
COPY src/shared ./src/shared
COPY src/databases ./src/databases
COPY src/pipelines/__init__.py ./src/pipelines/__init__.py
COPY src/pipelines/analytics ./src/pipelines/analytics
COPY src/pipelines/tracker ./src/pipelines/tracker
RUN python -m pip install .

CMD ["chi-ciq-tracker", "--help"]
