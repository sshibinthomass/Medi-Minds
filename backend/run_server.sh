#!/bin/bash
# Script to run the FastAPI server

# Check if uv is available
if command -v uv &> /dev/null; then
    echo "Starting server with uv..."
    uv run uvicorn api_server:app --reload --host 0.0.0.0 --port 8000
else
    echo "Starting server with python..."
    python -m uvicorn api_server:app --reload --host 0.0.0.0 --port 8000
fi

