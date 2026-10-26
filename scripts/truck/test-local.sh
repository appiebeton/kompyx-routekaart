#!/usr/bin/env bash
# Regressietests op echte NL-tegels. Vereist een tegelbouw (build-nl-tiles.sh) in $WORK (standaard .truck-build).
set -euo pipefail
WORK="${WORK:-$PWD/.truck-build}"
PORT="${PORT:-8012}"
node --import tsx scripts/truck/local-service.mjs "$WORK/tiles.tar" "$WORK/venv/bin" "$PORT" > /tmp/kompyx-truck-service.log 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null || true; pkill -f "kompyx-truck-valhalla-$PORT" 2>/dev/null || true' EXIT
for i in $(seq 1 60); do curl -sf "http://127.0.0.1:$PORT/status" > /dev/null && break; sleep 1; done
VALHALLA_URL="http://127.0.0.1:$PORT" TRUCK_AUX="$WORK/restrictions.json" node --import tsx --test test/truckScenarios.test.ts
