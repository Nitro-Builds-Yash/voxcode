# --- Rust acceleration (voxcode-native) -------------------------------------
FROM rust:1-slim AS native
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/maturin && /opt/maturin/bin/pip install --no-cache-dir "maturin>=1.7,<2"
COPY native /src/native
RUN /opt/maturin/bin/maturin build --release --manifest-path /src/native/Cargo.toml -o /wheels

# --- Application ------------------------------------------------------------
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
COPY --from=native /wheels /tmp/wheels
RUN pip install --no-cache-dir /tmp/wheels/*.whl && rm -rf /tmp/wheels

COPY . .

ENV HOST=0.0.0.0 PORT=8000 PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["voxcode", "serve"]
