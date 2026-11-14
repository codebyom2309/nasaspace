#!/usr/bin/env bash
# Deploy Mission Architect (Team Turbobits).
set -euo pipefail
cd "$(dirname "$0")/.."
echo "Deploying Mission Architect container..."
mkdir -p data
docker compose up -d --build
echo "Deployment completed successfully."
