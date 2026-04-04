"""ODsay HTTP 호출 누적. KST 달력일 기준으로 자정에 카운트를 0으로 리셋."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USAGE_PATH = ROOT / "data" / "cache" / "odsay_usage.json"
KST = timezone(timedelta(hours=9))

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[misc, assignment]

_file_lock = threading.Lock()


def _kst_today() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def _next_kst_midnight_iso() -> str:
    now = datetime.now(KST)
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    nxt = today0 + timedelta(days=1)
    return nxt.replace(tzinfo=KST).isoformat()


def _normalize_and_apply_delta(data: dict, count_delta: int) -> dict:
    today = _kst_today()
    if data.get("kst_date") != today:
        data = {"kst_date": today, "count": 0}
    data["count"] = int(data.get("count", 0)) + int(count_delta)
    return data


def _read_write_usage(count_delta: int = 0) -> dict:
    USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)

    def parse_body(body: str) -> dict:
        if not body.strip():
            return {"kst_date": _kst_today(), "count": 0}
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"kst_date": _kst_today(), "count": 0}

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
        "count": int(data["count"]),
        "kst_date": str(data["kst_date"]),
        "next_reset_kst": _next_kst_midnight_iso(),
    }


def get_odsay_usage() -> dict:
    """현재 KST 기준 누적 호출 수. 날짜가 바뀌었으면 파일을 0으로 맞춤."""
    return _read_write_usage(0)


def record_odsay_call() -> None:
    """ODsay에 대한 HTTP 요청이 끝난 직후 호출(응답 본문 파싱 전에 호출해도 됨)."""
    _read_write_usage(1)
