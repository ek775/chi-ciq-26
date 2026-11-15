FROM python:3.12-slim-trixie

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg postgresql-client-17 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY pyproject.toml ./
COPY src ./src
# Whisper's "triton" distribution overlaps the ROCm Triton files; restore the
# PyTorch ROCm wheel last and verify its installed files against their wheel hashes.
RUN python -m pip install --index-url https://download.pytorch.org/whl/rocm6.4 \
        "torch==2.8.0+rocm6.4" "torchaudio==2.8.0+rocm6.4" \
    && python -m pip install --editable ".[dev,ingestion,web]" \
        "torch==2.8.0+rocm6.4" "torchaudio==2.8.0+rocm6.4" \
        "torchcodec==0.7.0" "pyannote.audio==4.0.4" "openai-whisper==20250625" "triton==3.4.0" \
    && python -m pip install --force-reinstall --no-deps \
        --index-url https://download.pytorch.org/whl/rocm6.4 "pytorch-triton-rocm==3.4.0" \
    && python -m pip check \
    && python -c "import base64, hashlib, importlib.metadata as m; import torch, torchaudio, triton.backends.amd, whisper; from torchcodec.decoders import AudioDecoder; files = [f for f in m.files('pytorch-triton-rocm') if str(f).startswith('triton/') and f.hash]; assert files and all(base64.urlsafe_b64encode(hashlib.sha256(f.locate().read_bytes()).digest()).decode().rstrip('=') == f.hash.value for f in files), 'ROCm Triton files changed'; assert torch.version.hip, 'PyTorch is not a ROCm build'; print('ROCm:', torch.version.hip)"

CMD ["sleep", "infinity"]
