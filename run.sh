#!/usr/bin/env bash
# voxcode launcher for Linux / macOS

set -e
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

echo "=========================================="
echo "       Starting VoxCode Engine            "
echo "=========================================="

if [ ! -f .env ] && [ -f .env.example ]; then
    echo "Creating default .env from .env.example..."
    cp .env.example .env
fi

echo "Launching FastAPI backend server at http://127.0.0.1:8000 ..."
python -m backend.main
