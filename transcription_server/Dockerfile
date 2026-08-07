# ABOUTME: Docker image for the WhisperX transcription server.
# ABOUTME: Python slim base with ffmpeg + uv. PyTorch bundles its own CUDA runtime.

FROM python:3.12-slim

# ffmpeg is required by whisperx for audio decoding
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies (frozen = use lockfile exactly)
RUN uv sync --frozen

# Copy application code
COPY server.py ./

EXPOSE 8642

CMD ["uv", "run", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8642"]
