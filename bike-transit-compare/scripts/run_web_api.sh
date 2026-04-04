#!/usr/bin/env bash
# 웹 API 서버 (프런트는 frontend/ 에서 npm run dev → http://localhost:5173)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec .venv/bin/python -m uvicorn src.web_api:app --reload --host 127.0.0.1 --port 8000
