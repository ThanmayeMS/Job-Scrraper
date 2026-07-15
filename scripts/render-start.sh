#!/usr/bin/env sh
set -eu

echo "Running database migrations..."
alembic upgrade head

echo "Starting JobRadar on port ${PORT:-8000}..."
exec uvicorn jobradar.main:app --host 0.0.0.0 --port "${PORT:-8000}"
