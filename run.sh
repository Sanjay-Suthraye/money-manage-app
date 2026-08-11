#!/bin/sh
# Start the Money Manager app (port 8000 is occupied by an SSH tunnel here).
cd "$(dirname "$0")"
exec "$HOME/miniconda3/envs/money/bin/uvicorn" backend.main:app --host 0.0.0.0 --port "${PORT:-8010}"
