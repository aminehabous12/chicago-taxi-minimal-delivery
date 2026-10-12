#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  printf '%s\n' "Docker CLI was not found. Install Docker Desktop and make sure it is running." >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  printf '%s\n' "Missing .env. Copy .env.example to .env and configure the source token." >&2
  exit 1
fi

docker compose build app
docker compose run --rm app pytest -q
docker compose run --rm app python -m chicago_taxi.ingestion.extract_raw_data "$@"
docker compose run --rm app python -m chicago_taxi.catalog.profile_data

printf '%s\n' "Pipeline completed. Catalog files are in data/catalog/."
