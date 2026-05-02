#!/bin/bash
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR" && source venv/bin/activate
echo "Starting AI Trader Pro... http://localhost:8000"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
