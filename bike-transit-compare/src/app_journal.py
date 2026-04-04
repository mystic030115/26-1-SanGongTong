"""
프론트 없이도 추적할 수 있도록 JSON Lines 일지와 배치 요약 JSON을 남깁니다.

- data/logs/journal_YYYY-MM-DD.jsonl — 이벤트 한 줄씩 append (ODsay 호출 결과, 웹 출발·도착 조회/배치 등)
- data/output/last_run_summary.json — python -m src.run 배치가 끝날 때 마지막 실행 요약

기록 실패 시에도 본 로직은 계속되도록 예외는 삼킵니다.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data" / "logs"
LAST_RUN_SUMMARY_PATH = ROOT / "data" / "output" / "last_run_summary.json"

_lock = threading.Lock()

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[misc, assignment]


def _utc_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(event: Dict[str, Any]) -> None:
    """하루 단위 파일에 JSON 한 줄 append."""
    payload = dict(event)
    payload["ts"] = _utc_ts()
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = LOG_DIR / f"journal_{day}.jsonl"
        if fcntl is not None:
            with open(path, "a", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.write(line)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        else:
            with _lock:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line)
    except OSError:
        pass


def write_last_run_summary(data: Dict[str, Any]) -> None:
    """배치 파이프라인 종료 시 덮어쓰기."""
    try:
        LAST_RUN_SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
        body = dict(data)
        body["written_at"] = _utc_ts()
        text = json.dumps(body, ensure_ascii=False, indent=2)
        if fcntl is not None:
            with open(LAST_RUN_SUMMARY_PATH, "w", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.write(text)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        else:
            with _lock:
                LAST_RUN_SUMMARY_PATH.write_text(text, encoding="utf-8")
    except OSError:
        pass


def log_build_pair_cache_done(
    *,
    pairs_in_run: int,
    fetch_path_attempts: int,
    skipped_cached_ok: int,
    source: str,
) -> None:
    append_jsonl(
        {
            "kind": "build_pair_cache_done",
            "source": source,
            "pairs_in_run": pairs_in_run,
            "fetch_path_attempts": fetch_path_attempts,
            "skipped_cached_ok": skipped_cached_ok,
        }
    )
