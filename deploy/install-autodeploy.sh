#!/usr/bin/env bash
# Deploy script for Mission Architect (Team Turbobits).
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p data
docker compose up -d --build 2>&1 | tail -3
docker image prune -f >/dev/null 2>&1 || true
echo "Mission Architect successfully deployed by Team Turbobits"
