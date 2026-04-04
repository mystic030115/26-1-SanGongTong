#!/usr/bin/env bash
# Python 배치: trips 전체(또는 env 제한) 처리 → cache 갱신 → trips_with_transit.xlsx
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec .venv/bin/python -m src.run "$@"
