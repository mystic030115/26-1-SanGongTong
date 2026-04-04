#!/usr/bin/env bash
# Vite 개발 서버 (터미널에서 run_web_api.sh 가 먼저 떠 있어야 /api 동작)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/frontend"
# 의존성 누락 시(recharts 등) Vite가 import 해석에 실패하므로 dev 전에 맞춤
npm install
exec npm run dev
