"""TMAP 호출량(=캐시 누적 행 수) 조회.

기존에는 `record_tmap_call()`로 HTTP 응답 기준 카운트를 누적했으나,
실제 배치 작업에서는 **캐시 CSV에 기록된 행 수**가 곧 API 호출(또는 memo 재사용 포함한 처리량)의
가장 직관적인 지표라서, `/api/usage`는 `tmap_by_district`의 누적 행 수로 반환한다.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USAGE_PATH = ROOT / "data" / "cache" / "tmap_usage.json"
TMAP_BY_DISTRICT_DIR = ROOT / "data" / "cache" / "tmap_by_district"
UTC = timezone.utc

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[misc, assignment]

_file_lock = threading.Lock()


def _normalize_and_apply_delta(data: dict, count_delta: int) -> dict:
    # backward compatibility:
    # - old format: {"kst_date": "...", "count": N}
    # - new format: {"count_total": N, "last_updated_utc": "..."}
    if "count_total" not in data and "count" in data:
        data = {"count_total": int(data.get("count") or 0)}
    data["count_total"] = int(data.get("count_total", 0)) + int(count_delta)
    data["last_updated_utc"] = datetime.now(UTC).isoformat()
    return data


def _read_write_usage(count_delta: int = 0) -> dict:
    USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)

    def parse_body(body: str) -> dict:
        if not body.strip():
            return {"count_total": 0, "last_updated_utc": datetime.now(UTC).isoformat()}
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"count_total": 0, "last_updated_utc": datetime.now(UTC).isoformat()}

    if fcntl is not None:
        USAGE_PATH.touch(exist_ok=True)
        with open(USAGE_PATH, "r+", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                body = f.read()
                data = _normalize_and_apply_delta(parse_body(body), count_delta)
                f.seek(0)
                f.truncate()
                f.write(json.dumps(data, ensure_ascii=False))
                f.flush()
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    else:
        with _file_lock:
            body = USAGE_PATH.read_text(encoding="utf-8") if USAGE_PATH.exists() else ""
            data = _normalize_and_apply_delta(parse_body(body), count_delta)
            USAGE_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    return {
        "count": int(data.get("count_total", 0)),
        "last_updated_utc": str(data.get("last_updated_utc") or ""),
    }


def get_tmap_usage() -> dict:
    """
    `tmap_by_district/*_tmap_pairs.csv`의 누적 행 수를 반환.
    (헤더 1줄 제외, 파일이 없으면 0)
    """
    total_rows = 0
    file_rows = {}
    if TMAP_BY_DISTRICT_DIR.exists():
        for fp in sorted(TMAP_BY_DISTRICT_DIR.glob("*_tmap_pairs.csv")):
            if not fp.is_file():
                continue
            try:
                with fp.open("r", encoding="utf-8", newline="") as f:
                    n = sum(1 for _ in f) - 1  # drop header
                n = max(0, int(n))
            except Exception:
                n = 0
            file_rows[fp.name] = n
            total_rows += n
    return {
        "count": int(total_rows),
        "by_file": file_rows,
        "last_updated_utc": datetime.now(UTC).isoformat(),
    }


def record_tmap_call() -> None:
    """TMAP API 요청이 Response를 반환한 직후 호출(상태코드 무관, 본문 파싱 전)."""
    _read_write_usage(1)

