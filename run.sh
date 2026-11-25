#!/usr/bin/env bash
# VoxCode launcher for Linux / macOS
set -e
cd "$(dirname "${BASH_SOURCE[0]}")"

if [ ! -f .env ] && [ -f .env.example ]; then
    echo "Creating .env from .env.example"
    cp .env.example .env
fi

echo "Starting VoxCode at http://127.0.0.1:${PORT:-8000}"
exec python -m backend.main
