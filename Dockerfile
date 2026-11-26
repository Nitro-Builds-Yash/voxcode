FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends git ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first for better layer caching.
COPY pyproject.toml README.md ./
COPY voxcode ./voxcode
# CPU-only PyTorch keeps the image much smaller (no CUDA libraries); build with
# --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu128 for CUDA.
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir torch --index-url ${TORCH_INDEX} \
    && pip install --no-cache-dir ".[ml,mcp,analysis,otel]" faster-whisper

COPY . .

ENV HOST=0.0.0.0 PORT=8000 PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["voxcode", "serve"]
