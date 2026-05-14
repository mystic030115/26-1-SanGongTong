import os
import sys
import time
import warnings
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd
import requests
from dotenv import load_dotenv
from requests.exceptions import (
    ChunkedEncodingError,
    HTTPError,
    JSONDecodeError,
    RequestException,
)

from .app_journal import (
    append_jsonl,
    log_build_pair_cache_done,
    write_last_run_summary,
)
from .tmap_usage import record_tmap_call

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

TMAP_APP_KEY = os.getenv("TMAP_APP_KEY")
TMAP_TRANSIT_URL = "https://apis.openapi.sk.com/transit/routes"


def _tmap_http_disabled() -> bool:
    """TMAP_DISABLED=1(또는 true/yes/on)이면 SK TMAP으로 HTTP를 보내지 않습니다."""
    v = (os.getenv("TMAP_DISABLED") or "").strip().lower()
    return v in ("1", "true", "yes", "on")

DATA_RAW = ROOT / "data" / "raw"
DATA_CACHE = ROOT / "data" / "cache"
DATA_OUT = ROOT / "data" / "output"
TRIPS_CSV = DATA_RAW / "trips.csv"
STATIONS_XLSX = DATA_RAW / "stations.xlsx"
TRANSIT_PAIRS_CSV = DATA_CACHE / "transit_pairs.csv"
TRIPS_OUT_XLSX = DATA_OUT / "trips_with_transit.xlsx"

# 시작·종료 대여소 번호가 모두 이 구간에 있을 때만 사용 (stations.xlsx 구간과 맞춤)
STATION_ID_RANGE: Tuple[int, int] = (2102, 2199)

# 캐시에 넣어도 되는 결과 (실패는 좌표 재사용 캐시에 넣지 않음 → 다른 쌍 오염 방지)
_COORD_CACHEABLE_STATUSES = frozenset({"OK", "NO_PATH_OR_TOO_CLOSE"})
# 다음 실행 때 다시 API 호출해 덮어쓸 상태
_RETRY_STATUSES = frozenset({"API_ERROR", "ERROR"})


def _read_trips_csv(path: Path) -> pd.DataFrame:
    for enc in ("cp949", "utf-8-sig", "utf-8"):
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path, encoding="cp949", encoding_errors="replace")


def norm_station_id(x):
    if pd.isna(x):
        return None
    s = str(x).strip()
    if s.lower() in ("nan", "none", "#n/a", ""):
        return None
    if s.strip().upper() in ("\\N", "N/A", "NA", "NONE"):
        return None
    if s.endswith(".0"):
        s = s[:-2]
    if not s.replace(".", "", 1).isdigit():
        return None
    return s.split(".")[0].zfill(5)


def station_id_int(x) -> Optional[int]:
    sid = norm_station_id(x)
    if sid is None:
        return None
    try:
        return int(sid)
    except ValueError:
        return None


def filter_trips_station_range(trips: pd.DataFrame) -> pd.DataFrame:
    lo, hi = STATION_ID_RANGE
    s = trips["대여 대여소번호"].map(station_id_int)
    e = trips["반납대여소번호"].map(station_id_int)
    m = s.notna() & e.notna() & (s >= lo) & (s <= hi) & (e >= lo) & (e <= hi)
    return trips.loc[m].reset_index(drop=True)


def _detect_trips_csv_encoding(path: Path) -> str:
    for enc in ("cp949", "utf-8-sig", "utf-8"):
        try:
            pd.read_csv(path, encoding=enc, nrows=5)
            return enc
        except UnicodeDecodeError:
            continue
    return "cp949"


def rewrite_trips_csv_station_range(path: Path) -> Tuple[int, int]:
    """
    대여·반납 대여소가 STATION_ID_RANGE 안에 있는 행만 남기고 path를 덮어씀.
    반환: (남긴 행 수, 삭제한 행 수)
    """
    enc = _detect_trips_csv_encoding(path)
    lo, hi = STATION_ID_RANGE
    parts = []
    total_in = 0
    for chunk in pd.read_csv(path, encoding=enc, chunksize=200_000):
        total_in += len(chunk)
        s = chunk["대여 대여소번호"].map(station_id_int)
        e = chunk["반납대여소번호"].map(station_id_int)
        m = s.notna() & e.notna() & (s >= lo) & (s <= hi) & (e >= lo) & (e <= hi)
        parts.append(chunk.loc[m])

    if parts:
        out = pd.concat(parts, ignore_index=True)
    else:
        out = pd.read_csv(path, encoding=enc, nrows=0)

    out.to_csv(path, index=False, encoding=enc)
    kept = len(out)
    return kept, total_in - kept


def pair_key(start_id, end_id):
    return (norm_station_id(start_id), norm_station_id(end_id))


def od_coord_key(slon, slat, elon, elat) -> tuple:
    """동일 출발·도착 좌표면 API 한 번만 쓰기 위한 키."""
    return (
        round(float(slon), 6),
        round(float(slat), 6),
        round(float(elon), 6),
        round(float(elat), 6),
    )


def _parse_station_block(path: Path, sheet_name: str) -> Optional[pd.DataFrame]:
    head = pd.read_excel(path, sheet_name=sheet_name, header=None, nrows=8)
    if head.shape[0] < 6:
        return None
    v = head.iloc[5, 0]
    if pd.isna(v):
        return None
    if not isinstance(v, (int, float)) and not (
        isinstance(v, str) and str(v).strip().isdigit()
    ):
        return None

    raw = pd.read_excel(path, sheet_name=sheet_name, header=None, skiprows=5)
    if raw.shape[1] < 6:
        return None
    out = pd.DataFrame(
        {
            "대여소번호": raw.iloc[:, 0],
            "위도": pd.to_numeric(raw.iloc[:, 4], errors="coerce"),
            "경도": pd.to_numeric(raw.iloc[:, 5], errors="coerce"),
        }
    )
    out = out.dropna(subset=["대여소번호"], how="all")
    return out if not out.empty else None


def load_stations_excel(path: Path) -> pd.DataFrame:
    xl = pd.ExcelFile(path)
    parts = []
    for name in xl.sheet_names:
        if name.strip() == "안내":
            continue
        block = _parse_station_block(path, name)
        if block is not None:
            parts.append(block)

    if not parts:
        raise ValueError(
            f"대여소 좌표 시트를 찾을 수 없습니다. {path} 의 시트를 확인하세요."
        )

    return pd.concat(parts, ignore_index=True).drop_duplicates(
        subset=["대여소번호"], keep="last"
    )


def load_data():
    trips = _read_trips_csv(TRIPS_CSV)
    trips = filter_trips_station_range(trips)
    max_rows = os.getenv("TRIPS_MAX_ROWS")
    if max_rows:
        trips = trips.head(int(max_rows))

    stations = load_stations_excel(STATIONS_XLSX)

    trips["start_station_id"] = trips["대여 대여소번호"].apply(norm_station_id)
    trips["end_station_id"] = trips["반납대여소번호"].apply(norm_station_id)
    trips["bike_time_min"] = pd.to_numeric(trips["이용시간(분)"], errors="coerce")

    stations["station_id"] = stations["대여소번호"].apply(norm_station_id)
    stations["lat"] = pd.to_numeric(stations["위도"], errors="coerce")
    stations["lon"] = pd.to_numeric(stations["경도"], errors="coerce")

    station_map = stations[["station_id", "lat", "lon"]].drop_duplicates()

    start_map = station_map.rename(
        columns={
            "station_id": "start_station_id",
            "lat": "start_lat",
            "lon": "start_lon",
        }
    )
    end_map = station_map.rename(
        columns={
            "station_id": "end_station_id",
            "lat": "end_lat",
            "lon": "end_lon",
        }
    )

    trips = trips.merge(start_map, on="start_station_id", how="left")
    trips = trips.merge(end_map, on="end_station_id", how="left")

    n_miss = int((trips["start_lat"].isna() | trips["end_lat"].isna()).sum())
    if n_miss:
        warnings.warn(
            f"{n_miss}건은 대여소 좌표가 없습니다. "
            "trips의 대여소번호가 stations.xlsx에 모두 포함되는지 확인하세요.",
            stacklevel=2,
        )
    return trips


def _tmap_legs_list(raw_legs):
    if raw_legs is None:
        return []
    if isinstance(raw_legs, list):
        return [l for l in raw_legs if isinstance(l, dict)]
    if isinstance(raw_legs, dict):
        return [raw_legs]
    return []


def _tmap_journal_return(ck: tuple, out: dict) -> dict:
    ad = out.get("api_detail")
    http_status = out.get("http_status")
    body_head = out.get("body_head")
    append_jsonl(
        {
            "kind": "tmap_http",
            "source": "fetch_transit_time",
            "coord_key": list(ck),
            "transit_status": str(out.get("transit_status", "")),
            "api_detail": (str(ad)[:240] if ad is not None and str(ad) != "nan" else ""),
            "http_status": int(http_status) if isinstance(http_status, (int, float)) else None,
            "body_head": str(body_head)[:400] if body_head is not None else None,
        }
    )
    return out


def fetch_transit_time(start_lon, start_lat, end_lon, end_lat):
    ck = od_coord_key(start_lon, start_lat, end_lon, end_lat)
    if _tmap_http_disabled():
        return _tmap_journal_return(
            ck,
            {
                "transit_total_min": pd.NA,
                "transit_riding_min": pd.NA,
                "transit_total_dist_m": pd.NA,
                "transit_status": "API_ERROR",
                "api_detail": "TMAP HTTP disabled (TMAP_DISABLED=1 in .env or environment)",
            },
        )
    if not TMAP_APP_KEY:
        return _tmap_journal_return(
            ck,
            {
                "transit_total_min": pd.NA,
                "transit_riding_min": pd.NA,
                "transit_total_dist_m": pd.NA,
                "transit_status": "API_ERROR",
                "api_detail": "TMAP_APP_KEY missing in .env",
            },
        )
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "appKey": TMAP_APP_KEY,
    }
    body = {
        "startX": str(start_lon),
        "startY": str(start_lat),
        "endX": str(end_lon),
        "endY": str(end_lat),
        "format": "json",
        "lang": 0,
        "count": 1,
    }

    try:
        resp = requests.post(TMAP_TRANSIT_URL, headers=headers, json=body, timeout=25)
    except RequestException as e:
        append_jsonl(
            {
                "kind": "tmap_http",
                "source": "fetch_transit_time",
                "phase": "request",
                "coord_key": list(ck),
                "error": str(e)[:400],
            }
        )
        raise

    # TMAP 서버가 응답 헤더/본문을 돌려준 뒤에만 1회 적립(연결 실패·DNS 등은 제외).
    try:
        record_tmap_call()
    except OSError as e:
        append_jsonl(
            {
                "kind": "tmap_usage_write_failed",
                "source": "fetch_transit_time",
                "error": str(e)[:300],
            }
        )

    if not getattr(resp, "ok", False):
        # 4xx/5xx는 웹 API를 500으로 터뜨리지 말고 캐시에 API_ERROR로 남긴다.
        status = getattr(resp, "status_code", None)
        body = ""
        try:
            body = (resp.text or "")[:400]
        except Exception:
            body = ""
        append_jsonl(
            {
                "kind": "tmap_http",
                "source": "fetch_transit_time",
                "phase": "http",
                "coord_key": list(ck),
                "http_status": status,
                "body_head": body,
            }
        )
        return _tmap_journal_return(
            ck,
            {
                "transit_total_min": pd.NA,
                "transit_riding_min": pd.NA,
                "transit_total_dist_m": pd.NA,
                "transit_status": "API_ERROR",
                "api_detail": f"http_status={status}",
                "http_status": status,
                "body_head": body,
            },
        )

    try:
        data = resp.json()
    except JSONDecodeError:
        return _tmap_journal_return(
            ck,
            {
                "transit_total_min": pd.NA,
                "transit_riding_min": pd.NA,
                "transit_total_dist_m": pd.NA,
                "transit_status": "API_ERROR",
                "api_detail": "response is not valid JSON",
            },
        )
    if not isinstance(data, dict):
        return _tmap_journal_return(
            ck,
            {
                "transit_total_min": pd.NA,
                "transit_riding_min": pd.NA,
                "transit_total_dist_m": pd.NA,
                "transit_status": "API_ERROR",
                "api_detail": "response root is not a JSON object",
            },
        )

    # TMAP Transit: metaData.plan.itineraries[*]
    md = data.get("metaData") if isinstance(data.get("metaData"), dict) else {}
    plan = md.get("plan") if isinstance(md.get("plan"), dict) else {}
    its = plan.get("itineraries")
    if isinstance(its, dict):
        itineraries = [its]
    elif isinstance(its, list):
        itineraries = [i for i in its if isinstance(i, dict)]
    else:
        itineraries = []

    if not itineraries:
        # docs: "거리 가까움/정류장 매핑 실패" 등은 200으로 오고 경로가 없을 수 있음
        return _tmap_journal_return(
            ck,
            {
                "transit_total_min": pd.NA,
                "transit_riding_min": pd.NA,
                "transit_total_dist_m": pd.NA,
                "transit_status": "NO_PATH_OR_TOO_CLOSE",
                "api_detail": "",
            },
        )

    def _it_total_time_sec(it: dict) -> float:
        v = it.get("totalTime")
        try:
            return float(v)
        except (TypeError, ValueError):
            return 10**18

    best = min(itineraries, key=_it_total_time_sec)
    total_sec = _it_total_time_sec(best)
    if total_sec >= 10**17:
        return _tmap_journal_return(
            ck,
            {
                "transit_total_min": pd.NA,
                "transit_riding_min": pd.NA,
                "transit_total_dist_m": pd.NA,
                "transit_status": "API_ERROR",
                "api_detail": "missing itineraries.totalTime",
            },
        )

    dist_m = best.get("totalDistance")
    try:
        dist_m_f = float(dist_m) if dist_m is not None else None
    except (TypeError, ValueError):
        dist_m_f = None

    # riding: WALK 제외한 leg sectionTime 합 (초)
    legs = _tmap_legs_list(best.get("legs"))
    ride_sec = 0.0
    for leg in legs:
        mode = str(leg.get("mode", "")).upper()
        if mode == "WALK":
            continue
        try:
            ride_sec += float(leg.get("sectionTime", 0) or 0)
        except (TypeError, ValueError):
            continue

    return _tmap_journal_return(
        ck,
        {
            # 기존 컬럼명이 _min이지만 실제 의미는 "초"였던 레거시(ODsay/ TM{A}P totalTime도 초).
            # 여기서는 일관되게 '초'를 저장하고, 표시/통계 쪽에서 분으로 변환한다.
            "transit_total_min": total_sec,
            "transit_riding_min": ride_sec,
            "transit_total_dist_m": dist_m_f if dist_m_f is not None else pd.NA,
            "transit_status": "OK",
            "api_detail": "",
        },
    )


def _row_from_cache(r) -> dict:
    d = {
        "start_station_id": r["start_station_id"],
        "end_station_id": r["end_station_id"],
        "transit_total_min": r["transit_total_min"],
        "transit_riding_min": r["transit_riding_min"],
        "transit_total_dist_m": r["transit_total_dist_m"]
        if "transit_total_dist_m" in r.index
        else pd.NA,
        "transit_status": r["transit_status"],
    }
    for c in ("start_lon", "start_lat", "end_lon", "end_lat"):
        if c in r.index and pd.notna(r[c]):
            d[c] = r[c]
    if "api_detail" in r.index and pd.notna(r["api_detail"]) and str(r["api_detail"]).strip():
        d["api_detail"] = r["api_detail"]
    return d


def _put_coord_cache(coord_transit_cache: dict, ck: tuple, out: dict) -> None:
    st = str(out.get("transit_status", ""))
    if st in _COORD_CACHEABLE_STATUSES:
        coord_transit_cache[ck] = {
            "transit_total_min": out["transit_total_min"],
            "transit_riding_min": out["transit_riding_min"],
            "transit_total_dist_m": out.get("transit_total_dist_m", pd.NA),
            "transit_status": out["transit_status"],
        }


def build_pair_cache(
    trips,
    *,
    top_pairs_limit: Optional[int] = None,
    force_refresh: bool = False,
    return_summary: bool = False,
    journal_source: str = "run.build_pair_cache",
) -> Optional[Dict[str, Any]]:
    # 대여소 쌍당 한 행만 (좌표 부동소수 차이로 행이 나뉘면 API가 중복 호출됨)
    pairs = (
        trips[
            [
                "start_station_id",
                "end_station_id",
                "start_lon",
                "start_lat",
                "end_lon",
                "end_lat",
            ]
        ]
        .drop_duplicates(subset=["start_station_id", "end_station_id"], keep="first")
        .reset_index(drop=True)
    )

    if top_pairs_limit is not None:
        top_by_freq = str(int(top_pairs_limit))
    else:
        top_by_freq = os.getenv("TOP_OD_PAIRS")
    if top_by_freq:
        n = int(top_by_freq)
        freq = (
            trips.groupby(["start_station_id", "end_station_id"], dropna=False)
            .size()
            .reset_index(name="_trip_count")
        )
        pairs = pairs.merge(
            freq, on=["start_station_id", "end_station_id"], how="left"
        )
        pairs["_trip_count"] = pairs["_trip_count"].fillna(0).astype(int)
        pairs = (
            pairs.sort_values("_trip_count", ascending=False)
            .head(n)
            .drop(columns=["_trip_count"])
            .reset_index(drop=True)
        )

    max_pairs = os.getenv("MAX_OD_PAIRS")
    if max_pairs:
        pairs = pairs.head(int(max_pairs))

    cache_map = {}
    # 동일 (출발·도착) 좌표 → 이미 알려진 대중교통 결과 재사용 (다른 대여소 번호 쌍이어도)
    coord_transit_cache = {}

    api_calls = 0
    skipped_cached_ok = 0

    if TRANSIT_PAIRS_CSV.exists():
        prev = pd.read_csv(TRANSIT_PAIRS_CSV)
        for _, r in prev.iterrows():
            k = pair_key(r["start_station_id"], r["end_station_id"])
            cache_map[k] = _row_from_cache(r)
            cols = ("start_lon", "start_lat", "end_lon", "end_lat")
            if all(c in prev.columns for c in cols):
                if (
                    pd.notna(r["start_lon"])
                    and pd.notna(r["start_lat"])
                    and pd.notna(r["end_lon"])
                    and pd.notna(r["end_lat"])
                ):
                    ck = od_coord_key(
                        r["start_lon"],
                        r["start_lat"],
                        r["end_lon"],
                        r["end_lat"],
                    )
                    ts = r["transit_status"]
                    st = "" if pd.isna(ts) else str(ts).strip()
                    if st in _COORD_CACHEABLE_STATUSES:
                        coord_transit_cache[ck] = {
                            "transit_total_min": r["transit_total_min"],
                            "transit_riding_min": r["transit_riding_min"],
                            "transit_status": r["transit_status"],
                        }

    for _, row in pairs.iterrows():
        k = pair_key(row["start_station_id"], row["end_station_id"])
        cached = cache_map.get(k)
        if cached is not None and not force_refresh:
            pst = str(cached.get("transit_status", "")).strip()
            if pst not in _RETRY_STATUSES:
                skipped_cached_ok += 1
                continue

        did_call_api = False
        if pd.isna(row["start_lon"]) or pd.isna(row["end_lon"]):
            out = {
                "start_station_id": row["start_station_id"],
                "end_station_id": row["end_station_id"],
                "transit_total_min": pd.NA,
                "transit_riding_min": pd.NA,
                "transit_status": "MISSING_COORD",
                "api_detail": "",
            }
        else:
            ck = od_coord_key(
                row["start_lon"],
                row["start_lat"],
                row["end_lon"],
                row["end_lat"],
            )
            if not force_refresh and ck in coord_transit_cache:
                base = coord_transit_cache[ck]
                out = {
                    "start_station_id": row["start_station_id"],
                    "end_station_id": row["end_station_id"],
                    "transit_total_min": base["transit_total_min"],
                    "transit_riding_min": base["transit_riding_min"],
                    "transit_status": base["transit_status"],
                    "api_detail": "",
                    "start_lon": row["start_lon"],
                    "start_lat": row["start_lat"],
                    "end_lon": row["end_lon"],
                    "end_lat": row["end_lat"],
                }
            else:
                err_out = {
                    "transit_total_min": pd.NA,
                    "transit_riding_min": pd.NA,
                    "transit_status": "ERROR",
                    "api_detail": "",
                }
                try:
                    out = fetch_transit_time(
                        row["start_lon"],
                        row["start_lat"],
                        row["end_lon"],
                        row["end_lat"],
                    )
                    did_call_api = True
                    api_calls += 1
                except HTTPError as e:
                    # record_tmap_call()은 이미 실행됨(응답 수신 후).
                    did_call_api = True
                    api_calls += 1
                    out = {**err_out, "api_detail": str(e)[:500]}
                except JSONDecodeError as e:
                    did_call_api = True
                    api_calls += 1
                    out = {**err_out, "api_detail": f"JSON: {str(e)[:480]}"}
                except ChunkedEncodingError as e:
                    did_call_api = True
                    api_calls += 1
                    out = {**err_out, "api_detail": str(e)[:500]}
                except RequestException as e:
                    # get() 단계 실패 등 — 응답 객체 없음, record_tmap_call 미실행.
                    did_call_api = True
                    out = {**err_out, "api_detail": str(e)[:500]}
                except Exception as e:
                    did_call_api = True
                    api_calls += 1
                    out = {**err_out, "api_detail": str(e)[:500]}
                out["start_station_id"] = row["start_station_id"]
                out["end_station_id"] = row["end_station_id"]
                out["start_lon"] = row["start_lon"]
                out["start_lat"] = row["start_lat"]
                out["end_lon"] = row["end_lon"]
                out["end_lat"] = row["end_lat"]
                out.setdefault("api_detail", "")
                _put_coord_cache(coord_transit_cache, ck, out)

        cache_map[k] = out
        if did_call_api:
            time.sleep(0.15)

    DATA_CACHE.mkdir(parents=True, exist_ok=True)
    full_cache = pd.DataFrame(list(cache_map.values()))
    full_cache.to_csv(TRANSIT_PAIRS_CSV, index=False)
    print(f"[build_pair_cache] TMAP API HTTP 요청 수(이번 실행): {api_calls}")
    log_build_pair_cache_done(
        pairs_in_run=int(len(pairs)),
        fetch_path_attempts=int(api_calls),
        skipped_cached_ok=int(skipped_cached_ok),
        source=journal_source,
    )
    if return_summary:
        return {
            "pairs_in_run": int(len(pairs)),
            "fetch_path_attempts": int(api_calls),
            "skipped_cached_ok": int(skipped_cached_ok),
        }
    return None


def main():
    DATA_OUT.mkdir(parents=True, exist_ok=True)

    trips = load_data()
    build_summary = build_pair_cache(trips, return_summary=True)

    pair_cache = pd.read_csv(TRANSIT_PAIRS_CSV)
    pair_cache["start_station_id"] = pair_cache["start_station_id"].map(norm_station_id)
    pair_cache["end_station_id"] = pair_cache["end_station_id"].map(norm_station_id)
    merge_cols = [
        "start_station_id",
        "end_station_id",
        "transit_total_min",
        "transit_riding_min",
        "transit_status",
        "api_detail",
    ]
    pair_cache = pair_cache[[c for c in merge_cols if c in pair_cache.columns]]
    trips = trips.merge(pair_cache, on=["start_station_id", "end_station_id"], how="left")

    trips["대중교통총시간_분"] = trips["transit_total_min"]
    trips["대중교통탑승시간_분"] = trips["transit_riding_min"]

    # 따릉이가 대중교통보다 빠를 때만: 대중교통 소요 − 따릉이 소요(분), 그 외는 빈 값
    trips["따릉이더빠른차이_분"] = (
        trips["transit_total_min"] - trips["bike_time_min"]
    ).where(trips["bike_time_min"] < trips["transit_total_min"])

    trips.to_excel(TRIPS_OUT_XLSX, index=False)

    write_last_run_summary(
        {
            "trip_rows": int(len(trips)),
            "transit_pair_cache_rows": int(len(pair_cache)),
            "build_pair_cache": build_summary or {},
            "paths": {
                "transit_pairs_csv": str(TRANSIT_PAIRS_CSV.relative_to(ROOT)),
                "trips_with_transit_xlsx": str(TRIPS_OUT_XLSX.relative_to(ROOT)),
            },
        }
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--rewrite-trips":
        k, d = rewrite_trips_csv_station_range(TRIPS_CSV)
        print(f"trips.csv: kept {k}, dropped {d}")
    else:
        main()
