"""
로컬 대시보드용 API.

전체 엑셀·캐시 일괄 처리는 배치로:  python -m src.run  (또는 scripts/run_batch.sh)

이 모듈(웹):
  터미널 1:  python -m uvicorn src.web_api:app --reload --host 127.0.0.1 --port 8000
  터미널 2:  cd frontend && npm install && npm run dev
  브라우저: http://localhost:5173  (Vite가 /api → 8000 프록시)
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats
import os
import csv as _csv
from pathlib import Path
from math import asin, cos, radians, sin, sqrt
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .app_journal import append_jsonl, read_last_events
from .tmap_usage import get_tmap_usage
from .run import (
    TRANSIT_PAIRS_CSV,
    TRIPS_CSV,
    STATIONS_XLSX,
    _RETRY_STATUSES,
    build_pair_cache,
    fetch_transit_time,
    load_data,
    norm_station_id,
)

_cache_lock = threading.Lock()
_factors_lock = threading.Lock()

_tmap_fill_active = threading.Event()
_tmap_fill_last: Dict[str, Any] = {"empty": True}


def clean_header(s: str) -> str:
    return (s or "").replace("\ufeff", "").strip()

app = FastAPI(title="bike-transit-compare")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _load_stations_with_names() -> pd.DataFrame:
    xl = pd.ExcelFile(STATIONS_XLSX)
    parts = []
    for sheet in xl.sheet_names:
        if sheet.strip() == "안내":
            continue
        head = pd.read_excel(STATIONS_XLSX, sheet_name=sheet, header=None, nrows=8)
        if head.shape[0] < 6:
            continue
        v = head.iloc[5, 0]
        if pd.isna(v):
            continue
        if not isinstance(v, (int, float)) and not (
            isinstance(v, str) and str(v).strip().isdigit()
        ):
            continue
        raw = pd.read_excel(STATIONS_XLSX, sheet_name=sheet, header=None, skiprows=5)
        if raw.shape[1] < 6:
            continue
        parts.append(
            pd.DataFrame(
                {
                    "대여소번호": raw.iloc[:, 0],
                    "name": raw.iloc[:, 1]
                    .astype(str)
                    .str.strip()
                    .replace("nan", ""),
                    "위도": pd.to_numeric(raw.iloc[:, 4], errors="coerce"),
                    "경도": pd.to_numeric(raw.iloc[:, 5], errors="coerce"),
                }
            )
        )
    if not parts:
        raise ValueError("stations.xlsx 에서 대여소 시트를 찾지 못했습니다.")
    df = pd.concat(parts, ignore_index=True).dropna(subset=["대여소번호"], how="all")
    df["station_id"] = df["대여소번호"].apply(norm_station_id)
    df = df.dropna(subset=["station_id"])
    return df.drop_duplicates(subset=["station_id"], keep="last")


_stations_df: Optional[pd.DataFrame] = None


def stations_table() -> pd.DataFrame:
    global _stations_df
    if _stations_df is None:
        _stations_df = _load_stations_with_names()
    return _stations_df


def _station_row(sid: str) -> Optional[pd.Series]:
    t = stations_table()
    m = t["station_id"] == sid
    if not m.any():
        return None
    return t.loc[m].iloc[0]


def _read_pair_cache() -> pd.DataFrame:
    if not TRANSIT_PAIRS_CSV.exists():
        return pd.DataFrame(
            columns=[
                "start_station_id",
                "end_station_id",
                "transit_total_min",
                "transit_riding_min",
                "transit_total_dist_m",
                "transit_status",
                "start_lon",
                "start_lat",
                "end_lon",
                "end_lat",
                "api_detail",
            ]
        )
    return pd.read_csv(TRANSIT_PAIRS_CSV)


def _upsert_pair_cache_row(row: dict[str, Any]) -> None:
    TRANSIT_PAIRS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with _cache_lock:
        df = _read_pair_cache()
        k0 = norm_station_id(row["start_station_id"])
        k1 = norm_station_id(row["end_station_id"])
        if df.empty:
            df = pd.DataFrame([row])
        else:
            df["start_station_id"] = df["start_station_id"].map(norm_station_id)
            df["end_station_id"] = df["end_station_id"].map(norm_station_id)
            mask = (df["start_station_id"] == k0) & (df["end_station_id"] == k1)
            if mask.any():
                idx = df.index[mask][0]
                for col, val in row.items():
                    if col not in df.columns:
                        df[col] = pd.NA
                    df.at[idx, col] = val
            else:
                df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(TRANSIT_PAIRS_CSV, index=False)


def _load_merged_trips() -> pd.DataFrame:
    """trips + transit_pairs 병합(캐시 없으면 대중교통 열은 비움)."""
    trips = load_data()
    pc = _read_pair_cache()
    if pc.empty:
        out = trips.copy()
        out["transit_total_min"] = pd.NA
        out["transit_riding_min"] = pd.NA
        out["transit_total_dist_m"] = pd.NA
        out["transit_status"] = ""
        out["api_detail"] = pd.NA
        return out
    pc["start_station_id"] = pc["start_station_id"].map(norm_station_id)
    pc["end_station_id"] = pc["end_station_id"].map(norm_station_id)
    cols = [
        c
        for c in (
            "start_station_id",
            "end_station_id",
            "transit_total_min",
            "transit_riding_min",
            "transit_total_dist_m",
            "transit_status",
            "api_detail",
        )
        if c in pc.columns
    ]
    return trips.merge(pc[cols], on=["start_station_id", "end_station_id"], how="left")


@app.get("/api/od-distance/ratio")
def od_distance_ratio(
    threshold_m: int = Query(
        700,
        ge=1,
        le=50_000,
        description="대중교통 경로 totalDistance(미터) 기준, 이 거리(m) 이하인 출발·도착 쌍의 비율",
    )
):
    pc = _read_pair_cache()
    if pc.empty:
        return {"empty": True, "threshold_m": int(threshold_m)}
    if "transit_total_dist_m" not in pc.columns:
        return {
            "empty": True,
            "threshold_m": int(threshold_m),
            "error": "pair cache에 transit_total_dist_m 컬럼이 없습니다. 캐시를 갱신하세요.",
        }

    st = pc.get("transit_status")
    ok = st.astype(str) == "OK" if st is not None else pd.Series([False] * len(pc))
    dist = pd.to_numeric(pc["transit_total_dist_m"], errors="coerce")
    usable = ok & dist.notna()
    total_pairs = int(usable.sum())
    within = int((usable & (dist <= float(threshold_m))).sum())
    ratio = (within / total_pairs) if total_pairs > 0 else None

    return {
        "empty": False,
        "threshold_m": int(threshold_m),
        "total_ok_pairs_with_distance": total_pairs,
        "within_threshold_pairs": within,
        "ratio": ratio,
    }


def _mask_trips_station_pair_with_comparable(m: pd.DataFrame) -> pd.Series:
    """
    출발·도착 대여소 쌍마다 비교 가능 트립이 1건 이상인 쌍에 속한 행만 True.
    (비교 불가 쌍의 트립은 통계·차트·임계 승률에서 제외해 기준을 맞춤.)
    """
    if m.empty:
        return pd.Series(dtype=bool)
    t = pd.to_numeric(m["transit_total_min"], errors="coerce")
    b = pd.to_numeric(m["bike_time_min"], errors="coerce")
    st = m["transit_status"].astype(str)
    ok = st == "OK"
    valid = ok & t.notna() & b.notna()
    tmp = m[["start_station_id", "end_station_id"]].copy()
    tmp["_v"] = valid.astype(int)
    comp_sum = tmp.groupby(["start_station_id", "end_station_id"], dropna=False)[
        "_v"
    ].transform("sum")
    return comp_sum > 0


def _hist_bins(series: pd.Series, bins: int = 18, q_cap: float = 0.995) -> List[dict]:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return []
    hi = float(max(s.quantile(q_cap), s.max(), 1.0))
    cnt, edges = np.histogram(s, bins=bins, range=(0.0, hi))
    out = []
    for i in range(len(cnt)):
        if int(cnt[i]) <= 0:
            continue
        out.append(
            {
                "name": f"{edges[i]:.0f}–{edges[i + 1]:.0f}분",
                "count": int(cnt[i]),
            }
        )
    return out


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    # WGS84 mean Earth radius (m)
    r = 6_371_000.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2.0) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2.0) ** 2
    return float(2.0 * r * asin(sqrt(a)))


@app.get("/api/geo/od-distance-table")
def geo_od_distance_table(
    threshold_m: int = Query(700, ge=1, le=50_000),
    sort_by: str = Query("dist_m", description="dist_m | trips"),
    sort_dir: str = Query("asc", description="asc | desc"),
    limit: int = Query(200, ge=10, le=5000),
    offset: int = Query(0, ge=0, le=5_000_000),
):
    """
    외부 API 없이 stations.xlsx의 위·경도로 OD 직선거리(m) 계산.
    '모든 조합'은 실제 데이터(trips.csv)에 등장한 출발-도착 쌍 기준(현실적으로 전수 N^2는 과대).
    """
    trips = load_data()
    if trips.empty:
        return {"empty": True, "threshold_m": int(threshold_m)}

    # OD pair frequency (trips count)
    tmp = trips[["start_station_id", "end_station_id"]].copy()
    tmp["start_station_id"] = tmp["start_station_id"].map(norm_station_id)
    tmp["end_station_id"] = tmp["end_station_id"].map(norm_station_id)
    g = (
        tmp.dropna(subset=["start_station_id", "end_station_id"])
        .groupby(["start_station_id", "end_station_id"], dropna=False)
        .size()
        .reset_index(name="trips")
    )
    # 자기 자신 → 자기 자신(0m) 구간은 제외
    g = g.loc[g["start_station_id"] != g["end_station_id"]].reset_index(drop=True)

    st = stations_table()[["station_id", "name", "위도", "경도"]].copy()
    st["station_id"] = st["station_id"].map(norm_station_id)
    st = st.dropna(subset=["station_id"]).drop_duplicates(subset=["station_id"], keep="last")

    s = st.rename(
        columns={"station_id": "start_station_id", "name": "start_name", "위도": "start_lat", "경도": "start_lon"}
    )
    e = st.rename(
        columns={"station_id": "end_station_id", "name": "end_name", "위도": "end_lat", "경도": "end_lon"}
    )
    m = g.merge(s, on="start_station_id", how="left").merge(e, on="end_station_id", how="left")

    m["start_lat"] = pd.to_numeric(m["start_lat"], errors="coerce")
    m["start_lon"] = pd.to_numeric(m["start_lon"], errors="coerce")
    m["end_lat"] = pd.to_numeric(m["end_lat"], errors="coerce")
    m["end_lon"] = pd.to_numeric(m["end_lon"], errors="coerce")

    ok = m[["start_lat", "start_lon", "end_lat", "end_lon"]].notna().all(axis=1)
    m_ok = m.loc[ok].copy()
    if m_ok.empty:
        return {
            "empty": True,
            "threshold_m": int(threshold_m),
            "error": "좌표가 있는 OD 쌍이 없습니다.",
        }

    # Vectorized-ish: apply over rows (still OK for few 10k pairs)
    m_ok["dist_m"] = m_ok.apply(
        lambda r: _haversine_m(float(r["start_lat"]), float(r["start_lon"]), float(r["end_lat"]), float(r["end_lon"])),
        axis=1,
    )
    m_ok["over_threshold"] = m_ok["dist_m"] > float(threshold_m)

    total_pairs = int(len(m_ok))
    over_pairs = int(m_ok["over_threshold"].sum())
    over_ratio = (over_pairs / total_pairs) if total_pairs > 0 else None

    sb = (sort_by or "dist_m").strip().lower()
    if sb not in ("dist_m", "trips"):
        sb = "dist_m"
    sd = (sort_dir or "asc").strip().lower()
    asc = sd != "desc"

    m_ok = m_ok.sort_values(by=[sb, "start_station_id", "end_station_id"], ascending=[asc, True, True])
    page = m_ok.iloc[int(offset) : int(offset) + int(limit)]

    rows = []
    for _, r in page.iterrows():
        sid = str(r["start_station_id"])
        eid = str(r["end_station_id"])
        sname = "" if pd.isna(r.get("start_name")) else str(r.get("start_name") or "").strip()
        ename = "" if pd.isna(r.get("end_name")) else str(r.get("end_name") or "").strip()
        rows.append(
            {
                "start_id": sid,
                "end_id": eid,
                "label": f"{sid}·{sname} → {eid}·{ename}",
                "trips": int(r["trips"]) if pd.notna(r["trips"]) else 0,
                "dist_m": round(float(r["dist_m"]), 1),
                "over_threshold": bool(r["over_threshold"]),
            }
        )

    return {
        "empty": False,
        "threshold_m": int(threshold_m),
        "total_pairs_with_coords": total_pairs,
        "over_threshold_pairs": over_pairs,
        "over_threshold_ratio": over_ratio,
        "sort_by": sb,
        "sort_dir": "asc" if asc else "desc",
        "limit": int(limit),
        "offset": int(offset),
        "rows": rows,
    }

def _hist_diff_min_stacked(
    diff_min: pd.Series, bike_faster_mask: pd.Series, n_bins: int = 20
) -> List[dict[str, Any]]:
    """diff = 대중교통 − 따릉이(분). 양수면 따릉이가 더 빠른 쪽과 겹침."""
    d = pd.to_numeric(diff_min, errors="coerce")
    bf = bike_faster_mask.astype(bool)
    ok = d.notna() & bf.notna()
    d = d[ok]
    bf = bf[ok]
    if d.empty:
        return []
    arr = d.to_numpy(dtype=float)
    bfa = bf.to_numpy()
    lo = float(np.percentile(arr, 2))
    hi = float(np.percentile(arr, 98))
    if lo >= hi:
        lo, hi = float(arr.min()), float(arr.max())
    span = max((hi - lo) * 0.08, 0.5)
    lo, hi = lo - span, hi + span
    edges = np.linspace(lo, hi, int(n_bins) + 1)
    out: List[dict[str, Any]] = []
    for i in range(int(n_bins)):
        left, right = float(edges[i]), float(edges[i + 1])
        last = i == n_bins - 1
        in_bin = (arr >= left) & (arr <= right if last else arr < right)
        c_tot = int(in_bin.sum())
        if c_tot <= 0:
            continue
        c_b = int((in_bin & bfa).sum())
        c_t = c_tot - c_b
        out.append(
            {
                "name": f"{left:.0f}~{right:.0f}분",
                "bike_faster": c_b,
                "transit_faster": c_t,
            }
        )
    return out


def _bike_win_rate_by_ride_duration_bucket(
    m: pd.DataFrame, valid: pd.Series, bike_win: pd.Series
) -> List[dict[str, Any]]:
    b = pd.to_numeric(m["bike_time_min"], errors="coerce")
    edges = [(0.0, 15.0), (15.0, 30.0), (30.0, 45.0), (45.0, float("inf"))]
    labels = ["0–15분", "15–30분", "30–45분", "45분 이상"]
    out: List[dict[str, Any]] = []
    for (lo, hi), lab in zip(edges, labels):
        if hi == float("inf"):
            mask = valid & b.notna() & (b >= lo)
        else:
            mask = valid & b.notna() & (b >= lo) & (b < hi)
        comp = int(mask.sum())
        if comp <= 0:
            out.append(
                {"bucket": lab, "comparable": 0, "bike_wins": 0, "rate_pct": None}
            )
            continue
        bw = int((mask & bike_win).sum())
        out.append(
            {
                "bucket": lab,
                "comparable": comp,
                "bike_wins": bw,
                "rate_pct": round(100.0 * bw / comp, 2),
            }
        )
    return out


def compute_global_stats() -> dict[str, Any]:
    if not TRIPS_CSV.exists():
        return {
            "trip_rows": 0,
            "comparable_rows": 0,
            "bike_faster_count": 0,
            "bike_faster_rate": None,
            "avg_transit_min": None,
            "avg_bike_min": None,
            "avg_saved_min_when_bike_faster": None,
            "trip_filter_note": "비교 가능 트립이 1건 이상인 출발·도착 쌍에만 속한 행만 포함(차트·임계 승률과 동일).",
        }
    m_full = _load_merged_trips()
    pair_ok = _mask_trips_station_pair_with_comparable(m_full)
    m = m_full.loc[pair_ok].reset_index(drop=True)
    t = pd.to_numeric(m["transit_total_min"], errors="coerce")
    b = pd.to_numeric(m["bike_time_min"], errors="coerce")
    st = m["transit_status"].astype(str)
    ok = st == "OK"
    valid = ok & t.notna() & b.notna()
    comparable = int(valid.sum())
    if comparable == 0:
        return {
            "trip_rows": len(m),
            "comparable_rows": 0,
            "bike_faster_count": 0,
            "bike_faster_rate": None,
            "avg_transit_min": None,
            "avg_bike_min": None,
            "avg_saved_min_when_bike_faster": None,
            "trip_filter_note": "비교 가능 트립이 1건 이상인 출발·도착 쌍에만 속한 행만 포함(차트·임계 승률과 동일).",
        }
    bike_faster = valid & (b < t)
    n_bf = int(bike_faster.sum())
    rate = n_bf / comparable
    saved = (t - b).where(bike_faster)
    return {
        "trip_rows": len(m),
        "comparable_rows": comparable,
        "bike_faster_count": n_bf,
        "bike_faster_rate": round(rate, 4),
        "avg_transit_min": round(float(t[valid].mean()), 2),
        "avg_bike_min": round(float(b[valid].mean()), 2),
        "avg_saved_min_when_bike_faster": round(float(saved.mean()), 2)
        if n_bf
        else None,
        "trip_filter_note": "비교 가능 트립이 1건 이상인 출발·도착 쌍에만 속한 행만 포함(차트·임계 승률과 동일).",
    }


def compute_charts_summary() -> dict[str, Any]:
    if not TRIPS_CSV.exists():
        return {"error": "trips.csv 없음", "empty": True}
    m_full = _load_merged_trips()
    m = m_full.loc[_mask_trips_station_pair_with_comparable(m_full)].reset_index(drop=True)
    t = pd.to_numeric(m["transit_total_min"], errors="coerce")
    b = pd.to_numeric(m["bike_time_min"], errors="coerce")
    st = m["transit_status"].astype(str)
    ok = st == "OK"
    valid = ok & t.notna() & b.notna()
    bike_win = valid & (b < t)
    transit_win = valid & (b >= t)

    pie: List[dict] = []
    if int(valid.sum()) > 0:
        pie = [
            {"name": "따릉이 더 빠름", "value": int(bike_win.sum())},
            {
                "name": "대중교통이 같거나 더 빠름",
                "value": int(transit_win.sum()),
            },
        ]

    status_vc = (
        m.loc[st != "", "transit_status"]
        .fillna("비어있음")
        .astype(str)
        .value_counts()
        .head(12)
    )
    status_bar = [
        {"name": str(name)[:24], "count": int(c)} for name, c in status_vc.items()
    ]

    diff_min = t - b
    hist_diff_stacked = _hist_diff_min_stacked(diff_min[valid], bike_win[valid])
    ride_bucket_rates = _bike_win_rate_by_ride_duration_bucket(m, valid, bike_win)

    scatter_bf: List[dict] = []
    scatter_tw: List[dict] = []
    sub = m.loc[valid, ["bike_time_min", "transit_total_min"]].copy()
    sub["bike"] = pd.to_numeric(sub["bike_time_min"], errors="coerce")
    sub["tr"] = pd.to_numeric(sub["transit_total_min"], errors="coerce")
    sub = sub.dropna(subset=["bike", "tr"])
    if len(sub) > 2800:
        sub = sub.sample(2800, random_state=42)
    for _, r in sub.iterrows():
        bx, ty = float(r["bike"]), float(r["tr"])
        pt = {
            "x": round(bx, 2),
            "y": round(ty, 2),
            "diff": round(ty - bx, 2),
        }
        if bx < ty:
            scatter_bf.append(pt)
        else:
            scatter_tw.append(pt)

    try:
        st_df = stations_table()
        id_name = dict(
            zip(st_df["station_id"].astype(str), st_df["name"].astype(str))
        )
    except Exception:
        id_name = {}

    tmp_p = m[["start_station_id", "end_station_id"]].copy()
    tmp_p["_v"] = valid.astype(int)
    tmp_p["_bw"] = bike_win.astype(int)
    pg = tmp_p.groupby(["start_station_id", "end_station_id"], dropna=False)
    pair_agg = (
        pg.agg(
            trips=("start_station_id", "size"),
            comparable=("_v", "sum"),
            bike_wins=("_bw", "sum"),
        )
        .reset_index()
        .nlargest(14, "trips")
    )
    top_od: List[dict[str, Any]] = []
    for _, r in pair_agg.iterrows():
        sa, ea = str(r["start_station_id"]), str(r["end_station_id"])
        na, nb = id_name.get(sa, ""), id_name.get(ea, "")
        label = f"{sa}→{ea}"
        if na or nb:
            label = f"{sa}({na[:8]}) → {ea}({nb[:8]})"
        comp = int(r["comparable"])
        bw = int(r["bike_wins"])
        rate_pct = round(100.0 * bw / comp, 2) if comp > 0 else None
        pmask = (
            (m["start_station_id"].astype(str) == sa)
            & (m["end_station_id"].astype(str) == ea)
            & valid
        )
        avg_diff = (
            round(float(diff_min[pmask].mean()), 2) if int(pmask.sum()) > 0 else None
        )
        top_od.append(
            {
                "label": label[:42] + ("…" if len(label) > 42 else ""),
                "trips": int(r["trips"]),
                "start_id": sa,
                "end_id": ea,
                "comparable": comp,
                "rate_pct": rate_pct,
                "avg_diff_min": avg_diff,
            }
        )

    ratio: List[dict] = []
    m_ok = m.loc[ok].copy()
    if len(m_ok):
        tot_ok = pd.to_numeric(m_ok["transit_total_min"], errors="coerce")
        rid_ok = pd.to_numeric(m_ok["transit_riding_min"], errors="coerce")
        mask_r = tot_ok.notna() & rid_ok.notna() & (tot_ok > 0)
        rat_pct = (rid_ok / tot_ok * 100).where(mask_r)
        ratio = _hist_bins(rat_pct, bins=12, q_cap=0.99)
        for item in ratio:
            item["name"] = item["name"].replace("분", "%")

    return {
        "empty": False,
        "trip_rows": len(m),
        "comparable_rows": int(valid.sum()),
        "trip_filter_note": "비교 가능 트립이 1건 이상인 출발·도착 쌍에만 속한 행만 포함(임계 승률 탭과 동일 기준).",
        "pie_faster": pie,
        "status_bar": status_bar,
        "hist_diff_min_stacked": hist_diff_stacked,
        "bike_win_rate_by_ride_bucket": ride_bucket_rates,
        "hist_transit_ride_ratio_pct": ratio,
        "scatter_bike_faster": scatter_bf,
        "scatter_transit_faster": scatter_tw,
        "top_od_pairs": top_od,
    }


def compute_map_graph(min_comparable: int, max_edges: int) -> dict[str, Any]:
    """
    지도용: 대여소(점) + 출발·도착 쌍을 잇는 선(승률·임계는 프론트에서 색상 처리).
    """
    if not TRIPS_CSV.exists():
        return {
            "empty": True,
            "error": "trips.csv 없음",
            "nodes": [],
            "edges": [],
        }
    m_full = _load_merged_trips()
    m = m_full.loc[_mask_trips_station_pair_with_comparable(m_full)].reset_index(drop=True)
    t = pd.to_numeric(m["transit_total_min"], errors="coerce")
    b = pd.to_numeric(m["bike_time_min"], errors="coerce")
    st = m["transit_status"].astype(str)
    ok = st == "OK"
    valid = ok & t.notna() & b.notna()
    bike_f = valid & (b < t)

    tmp = m[["start_station_id", "end_station_id"]].copy()
    tmp["_v"] = valid
    tmp["_bf"] = bike_f
    g = tmp.groupby(["start_station_id", "end_station_id"], dropna=False)
    agg = (
        g.agg(
            total_trips=("start_station_id", "size"),
            comparable=("_v", "sum"),
            bike_wins=("_bf", "sum"),
        )
        .reset_index()
    )
    agg = agg.loc[agg["comparable"] >= int(min_comparable)].copy()
    if agg.empty:
        nodes = _map_station_nodes()
        return {
            "empty": False,
            "nodes": nodes,
            "edges": [],
            "meta": {
                "min_comparable": int(min_comparable),
                "max_edges": int(max_edges),
                "edge_count": 0,
            },
        }

    comp = agg["comparable"].to_numpy(dtype=int)
    bw = agg["bike_wins"].to_numpy(dtype=int)
    rate = np.zeros(len(agg), dtype=float)
    np.divide(bw, comp, out=rate, where=comp > 0)
    agg["rate_pct"] = np.where(comp > 0, rate * 100.0, np.nan)
    agg = agg.sort_values("comparable", ascending=False).head(int(max_edges))

    try:
        st_df = stations_table()
        id_name = dict(
            zip(st_df["station_id"].astype(str), st_df["name"].astype(str))
        )
    except Exception:
        id_name = {}

    edges: List[dict] = []
    for _, r in agg.iterrows():
        sa, ea = str(r["start_station_id"]), str(r["end_station_id"])
        na, nb = id_name.get(sa, ""), id_name.get(ea, "")
        rp = r["rate_pct"]
        if pd.isna(rp):
            continue
        edges.append(
            {
                "from_id": sa,
                "to_id": ea,
                "from_name": na or "이름 없음",
                "to_name": nb or "이름 없음",
                "rate_pct": round(float(rp), 2),
                "comparable": int(r["comparable"]),
                "total_trips": int(r["total_trips"]),
            }
        )

    nodes = _map_station_nodes()
    return {
        "empty": False,
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "min_comparable": int(min_comparable),
            "max_edges": int(max_edges),
            "edge_count": len(edges),
        },
    }


def _map_station_nodes() -> List[dict]:
    try:
        st_df = stations_table()
    except Exception:
        return []
    out: List[dict] = []
    for _, r in st_df.iterrows():
        sid = str(r["station_id"])
        lat, lon = r["위도"], r["경도"]
        if pd.isna(lat) or pd.isna(lon):
            continue
        nm = r.get("name")
        name = "" if pd.isna(nm) else str(nm).strip()
        out.append(
            {
                "id": sid,
                "name": name or "이름 없음",
                "lat": float(lat),
                "lon": float(lon),
            }
        )
    return out


def _hist_rate_pct_0_100(rates: np.ndarray, *, include_empty_bins: bool = False) -> List[dict]:
    """0–100%를 20구간(각 5%p)으로 나눈 히스토그램."""
    r = rates[np.isfinite(rates)] if len(rates) else np.array([], dtype=float)
    r = np.clip(r, 0.0, 100.0) if len(r) else r
    cnt, edges = np.histogram(r, bins=20, range=(0.0, 100.0))
    out = []
    for i in range(len(cnt)):
        c = int(cnt[i])
        if not include_empty_bins and c <= 0:
            continue
        out.append(
            {
                "name": f"{edges[i]:.0f}–{edges[i + 1]:.0f}%",
                "bin_lo": float(edges[i]),
                "bin_hi": float(edges[i + 1]),
                "count": c,
            }
        )
    return out


def compute_od_threshold_winners(threshold_pct: float) -> dict[str, Any]:
    """
    출발·도착 대여소 쌍별로 (비교 가능 트립 중 따릉이가 더 빠른 비율) > threshold_pct 이면 따릉이 유리,
    아니면 대중교통 유리. 비교 불가 쌍은 제외한 뒤 집계.
    """
    thr = float(threshold_pct)

    m = _load_merged_trips()
    t = pd.to_numeric(m["transit_total_min"], errors="coerce")
    b = pd.to_numeric(m["bike_time_min"], errors="coerce")
    st = m["transit_status"].astype(str)
    ok = st == "OK"
    valid = ok & t.notna() & b.notna()
    bike_f = valid & (b < t)

    tmp = m[["start_station_id", "end_station_id"]].copy()
    tmp["_v"] = valid
    tmp["_bf"] = bike_f
    g = tmp.groupby(["start_station_id", "end_station_id"], dropna=False)
    agg = (
        g.agg(
            total_trips=("start_station_id", "size"),
            comparable=("_v", "sum"),
            bike_wins=("_bf", "sum"),
        )
        .reset_index()
    )
    agg = agg.loc[agg["comparable"] > 0].copy()
    if agg.empty:
        return {
            "empty": True,
            "threshold_pct": round(thr, 2),
            "total_od_pairs": 0,
            "pie_od_class": [],
            "pie_od_class_full": [],
            "scatter": [],
            "hist_od_bike_rate": [],
            "bars_top_bike_od": [],
            "bars_weakest_bike_od": [],
            "rows": [],
        }

    comp = agg["comparable"].to_numpy(dtype=int)
    bw = agg["bike_wins"].to_numpy(dtype=int)
    rate = np.zeros(len(agg), dtype=float)
    np.divide(bw, comp, out=rate, where=comp > 0)
    rate_pct = np.where(comp > 0, rate * 100.0, np.nan)

    cls_arr = np.where(rate_pct > thr, "bike_win", "transit_win")

    agg["rate_pct"] = rate_pct
    agg["classification"] = cls_arr

    try:
        st_df = stations_table()
        id_name = dict(
            zip(st_df["station_id"].astype(str), st_df["name"].astype(str))
        )
    except Exception:
        id_name = {}

    vc = agg["classification"].value_counts()
    pie_od = [
        {"name": "따릉이 유리 (구간)", "value": int(vc.get("bike_win", 0))},
        {"name": "대중교통 유리 (구간)", "value": int(vc.get("transit_win", 0))},
    ]

    scatter: List[dict] = []
    rows: List[dict] = []
    for _, r in agg.iterrows():
        sa, ea = str(r["start_station_id"]), str(r["end_station_id"])
        na, nb = id_name.get(sa, ""), id_name.get(ea, "")
        short = f"{sa}→{ea}"
        long_l = f"{sa} ({na or '—'}) → {ea} ({nb or '—'})"
        comp_i = int(r["comparable"])
        rp = r["rate_pct"]
        rp_f = round(float(rp), 2) if not pd.isna(rp) else None
        cl = str(r["classification"])
        rows.append(
            {
                "start_id": sa,
                "end_id": ea,
                "label_short": short,
                "label_long": long_l[:80] + ("…" if len(long_l) > 80 else ""),
                "total_trips": int(r["total_trips"]),
                "comparable": comp_i,
                "bike_wins": int(r["bike_wins"]),
                "rate_pct": rp_f,
                "classification": cl,
            }
        )
        if rp_f is not None:
            scatter.append(
                {
                    "x": comp_i,
                    "y": rp_f,
                    "cls": cl,
                }
            )

    if len(scatter) > 900:
        scatter = (
            pd.DataFrame(scatter)
            .sample(900, random_state=42)
            .to_dict(orient="records")
        )

    rows.sort(
        key=lambda x: (
            x["rate_pct"] is None,
            -(x["rate_pct"] or 0),
            -x["total_trips"],
        )
    )

    sub_bike = agg[agg["classification"] == "bike_win"].copy()
    top_bike = sub_bike.nlargest(14, "rate_pct") if len(sub_bike) else pd.DataFrame()
    bars_bike: List[dict] = []
    for _, r in top_bike.iterrows():
        sa, ea = str(r["start_station_id"]), str(r["end_station_id"])
        na, nb = id_name.get(sa, ""), id_name.get(ea, "")
        bars_bike.append(
            {
                "label": f"{sa}→{ea}" + (f" ({na[:6]})" if na else ""),
                "rate_pct": round(float(r["rate_pct"]), 2),
                "comparable": int(r["comparable"]),
            }
        )

    sub_tr = agg[agg["classification"] == "transit_win"].copy()
    top_tr = sub_tr.nsmallest(14, "rate_pct") if len(sub_tr) else pd.DataFrame()
    bars_transit: List[dict] = []
    for _, r in top_tr.iterrows():
        sa, ea = str(r["start_station_id"]), str(r["end_station_id"])
        na, nb = id_name.get(sa, ""), id_name.get(ea, "")
        bars_transit.append(
            {
                "label": f"{sa}→{ea}" + (f" ({na[:6]})" if na else ""),
                "rate_pct": round(float(r["rate_pct"]), 2),
                "comparable": int(r["comparable"]),
            }
        )

    hist_od_rate = _hist_rate_pct_0_100(
        agg["rate_pct"].to_numpy(dtype=float), include_empty_bins=True
    )

    return {
        "empty": False,
        "threshold_pct": round(thr, 2),
        "total_od_pairs": len(agg),
        "pie_od_class": [p for p in pie_od if p["value"] > 0],
        "pie_od_class_full": pie_od,
        "scatter": scatter,
        "hist_od_bike_rate": hist_od_rate,
        "bars_top_bike_od": bars_bike,
        "bars_weakest_bike_od": bars_transit,
        "rows": rows,
    }


class BatchRefreshBody(BaseModel):
    n: int = Field(20, ge=1, le=400)
    force_refresh: bool = False


class TmapDistrictFillBody(BaseModel):
    """POST /api/tmap-by-district/fill-until-complete 요청 본문(모두 선택)."""

    workers: int = Field(4, ge=1, le=32)
    pair_workers: int = Field(3, ge=1, le=32)
    max_batches: int = Field(80, ge=1, le=500, description="fill_tmap_cache 를 최대 몇 번까지 연속 실행")
    sleep_sec_between_batches: float = Field(4.0, ge=0.0, le=600.0)
    single_pass: bool = Field(
        False,
        description="True면 스크립트에 --single-pass(구당 1라운드만). 보통 False 로 두고 내부 재시도·라운드에 맡김.",
    )


class LookupResponse(BaseModel):
    start_station_id: str
    end_station_id: str
    from_cache: bool
    transit_total_min: Optional[float] = None
    transit_riding_min: Optional[float] = None
    transit_status: str
    api_detail: Optional[str] = None
    bike_time_min: Optional[float] = None
    bike_faster: Optional[bool] = None
    bike_saved_min: Optional[float] = None
    start_lat: Optional[float] = None
    start_lon: Optional[float] = None
    end_lat: Optional[float] = None
    end_lon: Optional[float] = None
    trip_count_for_pair: int = 0


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/stations")
def list_stations():
    df = stations_table()
    rows = []
    for _, r in df.iterrows():
        sid = r["station_id"]
        rows.append(
            {
                "id": sid,
                "label": f"{sid} · {r['name'] or '이름없음'}",
                "lat": float(r["위도"]) if pd.notna(r["위도"]) else None,
                "lon": float(r["경도"]) if pd.notna(r["경도"]) else None,
            }
        )
    rows.sort(key=lambda x: x["id"])
    return {"stations": rows}


@app.get("/api/stats")
def get_stats():
    return compute_global_stats()


@app.get("/api/usage")
def tmap_usage():
    """TMAP API에 대한 HTTP 요청이 응답을 받은 누적 횟수(리셋 없음)."""
    return get_tmap_usage()


_district_rebuild_lock = threading.Lock()


def _default_od_dir() -> Path:
    # Prefer in-repo folder if present, else fall back to user's previous path.
    root = Path(__file__).resolve().parent.parent
    cand1 = root / "관내이동_시간_거리"
    if cand1.exists():
        return cand1
    cand2 = Path.home() / "Documents" / "sangongtong" / "bike-transit-compare" / "관내이동_시간_거리"
    return cand2


@app.post("/api/district-savings/rebuild")
def rebuild_district_savings():
    """
    `data/cache/tmap_by_district`를 기반으로 `frontend/public/district_savings.json`을 재생성.
    - 배치가 돌아가면서 cache CSV가 늘어나도, 이 엔드포인트를 호출하면 프런트가 바로 최신을 보게 됨.
    """
    if not _district_rebuild_lock.acquire(blocking=False):
        raise HTTPException(409, "rebuild already running")
    try:
        root = Path(__file__).resolve().parent.parent
        od_dir = Path(os.getenv("OD_DISTRICT_DIR") or _default_od_dir())
        tmap_dir = root / "data" / "cache" / "tmap_by_district"
        out_json = root / "frontend" / "public" / "district_savings.json"
        out_json.parent.mkdir(parents=True, exist_ok=True)

        from scripts.build_district_savings import main as _build_main  # type: ignore

        # Run script main with argv style by temporarily patching sys.argv.
        import sys as _sys

        argv0 = list(_sys.argv)
        try:
            _sys.argv = [
                "build_district_savings.py",
                "--od-dir",
                str(od_dir),
                "--tmap-dir",
                str(tmap_dir),
                "--out",
                str(out_json),
            ]
            code = int(_build_main())
        finally:
            _sys.argv = argv0

        if code != 0:
            raise HTTPException(500, f"rebuild failed: exit_code={code}")
        return {"ok": True, "out": str(out_json), "od_dir": str(od_dir), "tmap_dir": str(tmap_dir)}
    finally:
        _district_rebuild_lock.release()


# borrow_min(0.01분 반올림) -> 재계산 payload 캐시 (디스크 재스캔 1회 후 빠른 재사용)
_DISTRICT_SAVINGS_BORROW_CACHE: dict[tuple[int, int, int], dict] = {}


@app.get("/api/district-savings/with-borrow")
def district_savings_with_borrow(
    borrow_min: float = Query(0.0, ge=0.0, le=10.0),
):
    """따릉이 대여 소요시간(borrow_min, 분)을 더해 재계산한 district_savings payload를 반환.

    파일을 덮어쓰지 않고 메모리에서 생성한다(가설1 슬라이더 '적용'용).
    borrow_min=0이면 기존 district_savings.json과 동일한 값.
    """
    root = Path(__file__).resolve().parent.parent
    od_dir = Path(os.getenv("OD_DISTRICT_DIR") or _default_od_dir())
    tmap_dir = root / "data" / "cache" / "tmap_by_district"
    if not od_dir.exists():
        raise HTTPException(404, f"OD dir not found: {od_dir}")
    if not tmap_dir.exists():
        raise HTTPException(404, f"TMAP dir not found: {tmap_dir}")

    key = (
        int(round(float(borrow_min) * 100)),
        _max_mtime_glob(od_dir, "*.csv"),
        _max_mtime_glob(tmap_dir, "*_tmap_pairs.csv"),
    )
    cached = _DISTRICT_SAVINGS_BORROW_CACHE.get(key)
    if cached is not None:
        return cached

    from scripts.build_district_savings import build_payload as _build_payload  # type: ignore

    try:
        payload = _build_payload(od_dir, tmap_dir, float(borrow_min))
    except Exception as e:
        raise HTTPException(500, f"recompute failed: {e}")

    _DISTRICT_SAVINGS_BORROW_CACHE[key] = payload
    return payload


def _warmup_district_savings_borrow(borrow_min: float = 0.5) -> None:
    """기본 슬라이더(+0.5분)용 payload를 백그라운드에서 미리 계산해 첫 화면 로딩을 줄인다."""
    root = Path(__file__).resolve().parent.parent
    od_dir = Path(os.getenv("OD_DISTRICT_DIR") or _default_od_dir())
    tmap_dir = root / "data" / "cache" / "tmap_by_district"
    if not od_dir.exists() or not tmap_dir.exists():
        return
    key = (
        int(round(float(borrow_min) * 100)),
        _max_mtime_glob(od_dir, "*.csv"),
        _max_mtime_glob(tmap_dir, "*_tmap_pairs.csv"),
    )
    if key in _DISTRICT_SAVINGS_BORROW_CACHE:
        return
    from scripts.build_district_savings import build_payload as _build_payload  # type: ignore

    try:
        _DISTRICT_SAVINGS_BORROW_CACHE[key] = _build_payload(od_dir, tmap_dir, float(borrow_min))
    except Exception:
        pass


@app.on_event("startup")
def _startup_warmup_default_borrow() -> None:
    threading.Thread(target=_warmup_district_savings_borrow, args=(0.5,), daemon=True).start()
    threading.Thread(target=_warmup_supply_analysis, daemon=True).start()


def _warmup_supply_analysis() -> None:
    try:
        supply_analysis()
    except Exception:
        pass


@app.get("/api/diagnostics/transit-last")
def diagnostics_transit_last(
    limit: int = Query(10, ge=1, le=100),
):
    """
    오늘(UTC) journal 파일에서 마지막 대중교통 API 호출 로그를 요약해서 반환.
    - kind: run.fetch_transit_time에서 남기는 `tmap_http`
    """
    events = read_last_events("tmap_http", limit=int(limit))
    if not events:
        return {"empty": True, "kind": "tmap_http", "limit": int(limit)}
    last = events[-1]
    # 최소 필드만 노출(너무 길어지는 body는 저장할 때 잘려 있음)
    return {
        "empty": False,
        "kind": "tmap_http",
        "limit": int(limit),
        "count_in_tail": len(events),
        "last": {
            "ts": last.get("ts"),
            "phase": last.get("phase"),
            "transit_status": last.get("transit_status"),
            "api_detail": last.get("api_detail"),
            "http_status": last.get("http_status"),
            "coord_key": last.get("coord_key"),
            "body_head": last.get("body_head"),
        },
    }


def _tmap_by_district_dir() -> Path:
    # default location used by fill script
    return Path(__file__).resolve().parent.parent / "data" / "cache" / "tmap_by_district"

_od_pairs_cache_lock = threading.Lock()
_od_pairs_total_cache: dict[str, dict[str, Any]] = {}
_overall_progress_history_lock = threading.Lock()
_overall_progress_history: list[dict[str, Any]] = []


def _pair_key_undirected(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def _od_pairs_total_for_gu(gu: str) -> int | None:
    """
    관내이동_시간_거리/{gu}_시간_거리.csv 에서
    고유 undirected OD쌍 개수(자기 자신 제외)를 계산.

    캐시: 파일 mtime이 바뀌지 않으면 재계산하지 않음.
    """
    od_dir = Path(os.getenv("OD_DISTRICT_DIR") or _default_od_dir())
    fp = od_dir / f"{gu}_시간_거리.csv"
    if not fp.exists():
        return None
    try:
        mtime = fp.stat().st_mtime
    except Exception:
        mtime = None

    with _od_pairs_cache_lock:
        hit = _od_pairs_total_cache.get(gu)
        if hit and hit.get("mtime") == mtime and isinstance(hit.get("pairs_total"), int):
            return int(hit["pairs_total"])

    # compute
    pairs: set[tuple[str, str]] = set()
    try:
        with open(fp, "r", encoding="utf-8", newline="") as f:
            r = _csv.DictReader(f)
            if not r.fieldnames:
                return None
            # columns are Korean; match exactly
            sk = "시작_대여소_ID"
            ek = "종료_대여소_ID"
            # handle BOM etc by normalizing fieldnames
            fn = [clean_header(x) for x in (r.fieldnames or [])]
            # map clean->raw
            m = {clean_header(x): x for x in (r.fieldnames or [])}
            if sk not in m or ek not in m:
                return None
            sk_raw = m[sk]
            ek_raw = m[ek]
            for row in r:
                a = (row.get(sk_raw) or "").strip()
                b = (row.get(ek_raw) or "").strip()
                if not a or not b or a == b:
                    continue
                pairs.add(_pair_key_undirected(a, b))
    except Exception:
        return None

    out = int(len(pairs))
    with _od_pairs_cache_lock:
        _od_pairs_total_cache[gu] = {"mtime": mtime, "pairs_total": out}
    return out

def _all_seoul_gus() -> list[str]:
    """
    Prefer GeoJSON list (exactly the 25 gu names used in map),
    fall back to OD folder filenames, then cache folder.
    """
    root = Path(__file__).resolve().parent.parent
    gj = root / "frontend" / "public" / "seoul_gu_simple.geojson"
    try:
        import json as _json

        obj = _json.loads(gj.read_text(encoding="utf-8"))
        gus = []
        for f in obj.get("features", []):
            nm = (f.get("properties") or {}).get("name")
            if nm and str(nm).endswith("구"):
                gus.append(str(nm))
        gus = sorted(set(gus))
        if len(gus) >= 25:
            return gus
    except Exception:
        pass

    od_dir = Path(os.getenv("OD_DISTRICT_DIR") or _default_od_dir())
    try:
        gus = []
        for p in od_dir.glob("*_시간_거리.csv"):
            gu = p.stem.replace("_시간_거리", "")
            if gu.endswith("구"):
                gus.append(gu)
        gus = sorted(set(gus))
        if gus:
            return gus
    except Exception:
        pass

    d = _tmap_by_district_dir()
    gus = sorted({p.name.replace("_tmap_pairs.csv", "") for p in d.glob("*_tmap_pairs.csv") if p.is_file()})
    return gus


@app.get("/api/tmap-by-district/summary")
def tmap_by_district_summary():
    """
    `data/cache/tmap_by_district/*_tmap_pairs.csv`를 스캔해서 구별 캐시 현황 요약을 반환.
    - total_rows: 헤더 제외 총 행 수
    - status_counts: transit_status 값별 카운트
    - last_written_at_utc: written_at_utc 최대값(있으면)
    """
    d = _tmap_by_district_dir()
    if not d.exists():
        return {"dir": str(d), "rows": []}

    out_rows = []
    for gu in _all_seoul_gus():
        fp = d / f"{gu}_tmap_pairs.csv"
        total_rows = 0
        status_counts: dict[str, int] = {}
        last_written_at = ""
        try:
            if fp.exists():
                with open(fp, "r", encoding="utf-8", newline="") as f:
                    r = _csv.DictReader(f)
                    for row in r:
                        total_rows += 1
                        st = (row.get("transit_status") or "").strip() or "EMPTY"
                        status_counts[st] = int(status_counts.get(st, 0)) + 1
                        ts = (row.get("written_at_utc") or "").strip()
                        if ts and ts > last_written_at:
                            last_written_at = ts
        except Exception:
            # unreadable file -> keep zeros
            pass

        ok = int(status_counts.get("OK", 0))
        no_path = int(status_counts.get("NO_PATH_OR_TOO_CLOSE", 0))
        api_err = int(status_counts.get("API_ERROR", 0))
        other = int(total_rows - ok - no_path - api_err)
        expected_pairs_total = _od_pairs_total_for_gu(gu)
        # 진행률: OK / 전체쌍(행 수가 재시도로 늘어나도 OK 비율이 의미 있음)
        completion_ratio = (
            (float(ok) / float(expected_pairs_total)) if expected_pairs_total and expected_pairs_total > 0 else None
        )
        ok_ratio = completion_ratio
        rows_per_expected_ratio = (
            (float(total_rows) / float(expected_pairs_total)) if expected_pairs_total and expected_pairs_total > 0 else None
        )
        out_rows.append(
            {
                "gu": gu,
                "file": fp.name,
                "expected_pairs_total": expected_pairs_total,
                "completion_ratio": completion_ratio,
                "ok_ratio": ok_ratio,
                "rows_per_expected_ratio": rows_per_expected_ratio,
                "total_rows": int(total_rows),
                "ok_rows": ok,
                "no_path_rows": no_path,
                "api_error_rows": api_err,
                "other_rows": other,
                "status_counts": status_counts,
                "last_written_at_utc": last_written_at or None,
            }
        )

    # overall summary (진행률 = OK 행 합 / 기대 쌍 합)
    exp_sum = sum(int(r.get("expected_pairs_total") or 0) for r in out_rows)
    got_sum = sum(int(r.get("total_rows") or 0) for r in out_rows)
    ok_sum = sum(int(r.get("ok_rows") or 0) for r in out_rows)
    overall_completion = (float(ok_sum) / float(exp_sum)) if exp_sum > 0 else None

    # ETA estimation using recent history of (timestamp, ok_sum).
    eta = {"rows_per_min": None, "eta_minutes": None, "eta_finish_at_kst": None, "window_sec": None}
    try:
        now = datetime.now(timezone.utc)
        with _overall_progress_history_lock:
            _overall_progress_history.append(
                {"ts_utc": now.isoformat(), "ok_sum": int(ok_sum), "exp_sum": int(exp_sum), "got_sum": int(got_sum)}
            )
            # keep last ~30 points
            if len(_overall_progress_history) > 30:
                _overall_progress_history[:] = _overall_progress_history[-30:]
            hist = list(_overall_progress_history)

        # choose earliest point within 30 minutes
        cutoff = now - timedelta(minutes=30)
        pts = []
        for h in hist:
            try:
                ts = datetime.fromisoformat(str(h["ts_utc"]))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    pts.append((ts, int(h.get("ok_sum", h.get("got_sum", 0)))))
            except Exception:
                continue

        if len(pts) >= 2:
            t0, v0 = pts[0]
            t1, v1 = pts[-1]
            dt = (t1 - t0).total_seconds()
            dv = float(v1 - v0)
            if dt > 30 and dv > 0 and exp_sum > 0:
                rows_per_min = dv / (dt / 60.0)
                remaining = float(exp_sum - ok_sum)
                eta_min = (remaining / rows_per_min) if rows_per_min > 0 else None
                if eta_min is not None and eta_min >= 0 and eta_min < 60 * 24 * 30:
                    finish_utc = now + timedelta(minutes=float(eta_min))
                    finish_kst = finish_utc.astimezone(timezone(timedelta(hours=9)))
                    eta = {
                        "rows_per_min": float(rows_per_min),
                        "eta_minutes": float(eta_min),
                        "eta_finish_at_kst": finish_kst.isoformat(),
                        "window_sec": float(dt),
                    }
    except Exception:
        pass

    return {
        "dir": str(d),
        "rows": out_rows,
        "overall": {
            "expected_pairs_total_sum": int(exp_sum) if exp_sum > 0 else None,
            "cached_rows_sum": int(got_sum),
            "ok_rows_sum": int(ok_sum),
            "completion_ratio": overall_completion,
            "eta": eta,
        },
    }


def _factors_csv_path() -> Path:
    root = Path(__file__).resolve().parent.parent
    return root / "data" / "factors" / "gu_factors.csv"

def _factors_wide_csv_path() -> Path:
    root = Path(__file__).resolve().parent.parent
    return root / "data" / "factors" / "seoul_gu_features_combined_wide.csv"


def _factors_meta_path() -> Path:
    root = Path(__file__).resolve().parent.parent
    return root / "data" / "factors" / "gu_factors_meta.json"


def _factors_supplemental_dir() -> Path:
    root = Path(__file__).resolve().parent.parent
    d = root / "data" / "factors" / "supplemental"
    d.mkdir(parents=True, exist_ok=True)
    return d


# CSV만 채우면 상관·VIF 분석에 자동 반영되는 보조 요인 슬롯 (wide CSV 컬럼명과 동일)
SUPPLEMENTAL_FACTOR_SLOTS: list[dict[str, Any]] = [
    {
        "factor": "single_person_household_ratio_pct",
        "file": "single_person_household_ratio_pct.csv",
        "unit": "%",
        "category": "population",
        "wide_column": "single_person_household_ratio_pct",
        "period_column": "single_person_household_year",
    },
    {
        "factor": "employment_rate_pct",
        "file": "employment_rate_pct.csv",
        "unit": "%",
        "category": "income",
        "wide_column": "employment_rate_pct",
        "period_column": "employment_rate_year",
    },
    {
        "factor": "park_area_total_m2",
        "file": "park_area_total_m2.csv",
        "unit": "m²",
        "category": "geo",
        "wide_column": "park_area_total_m2",
        "period_column": "park_area_year",
    },
]


def _extra_wide_factor_columns() -> list[tuple[str, str, str | None]]:
    """(column, unit, optional period column in wide CSV)"""
    out: list[tuple[str, str, str | None]] = []
    for spec in SUPPLEMENTAL_FACTOR_SLOTS:
        out.append((str(spec["wide_column"]), str(spec["unit"]), spec.get("period_column")))
    return out


def _read_one_supplemental_csv(fp: Path, spec: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    factor = str(spec["factor"])
    status: dict[str, Any] = {
        "factor": factor,
        "file": fp.name,
        "category": spec.get("category"),
        "exists": fp.exists(),
        "loaded": False,
        "rows": 0,
        "path": str(fp),
    }
    cols = ["gu", "factor", "value", "unit", "source", "year"]
    if not fp.exists():
        return pd.DataFrame(columns=cols), status
    try:
        raw = pd.read_csv(fp)
    except Exception as exc:
        status["error"] = str(exc)
        return pd.DataFrame(columns=cols), status
    if raw.empty:
        return pd.DataFrame(columns=cols), status

    df = raw.copy()
    df.columns = [str(c).strip() for c in df.columns]
    if "gu" not in df.columns:
        status["error"] = "missing column: gu"
        return pd.DataFrame(columns=cols), status

    value_col: str | None = None
    if "value" in df.columns:
        value_col = "value"
    elif factor in df.columns:
        value_col = factor
    else:
        numeric_cols = [
            c
            for c in df.columns
            if c != "gu" and c != "factor" and pd.to_numeric(df[c], errors="coerce").notna().any()
        ]
        if len(numeric_cols) == 1:
            value_col = numeric_cols[0]

    if value_col is None:
        status["error"] = f"missing value column (expected value or {factor})"
        return pd.DataFrame(columns=cols), status

    df["gu"] = df["gu"].astype(str).str.strip()
    vals = pd.to_numeric(df[value_col], errors="coerce")
    unit_series = df["unit"].astype(str).str.strip() if "unit" in df.columns else None
    source_series = df["source"].astype(str).str.strip() if "source" in df.columns else None
    year_series = df["year"].astype(str).str.strip() if "year" in df.columns else None

    out_rows: list[dict[str, Any]] = []
    for i, gu in enumerate(df["gu"].tolist()):
        v = vals.iloc[i]
        if v is None or not np.isfinite(v):
            continue
        out_rows.append(
            {
                "gu": str(gu).strip(),
                "factor": factor,
                "value": float(v),
                "unit": str(spec.get("unit") or ""),
                "source": (str(source_series.iloc[i]) if source_series is not None else ""),
                "year": (str(year_series.iloc[i]) if year_series is not None else ""),
            }
        )
    if unit_series is not None:
        for j, row in enumerate(out_rows):
            u = str(unit_series.iloc[j]) if j < len(unit_series) else ""
            if u and u.lower() != "nan":
                row["unit"] = u
    out = pd.DataFrame(out_rows, columns=cols)
    status["loaded"] = not out.empty
    status["rows"] = int(len(out))
    return out, status


def _read_supplemental_factors_long() -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    parts: list[pd.DataFrame] = []
    statuses: list[dict[str, Any]] = []
    base = _factors_supplemental_dir()
    for spec in SUPPLEMENTAL_FACTOR_SLOTS:
        fp = base / str(spec["file"])
        chunk, st = _read_one_supplemental_csv(fp, spec)
        statuses.append(st)
        if not chunk.empty:
            parts.append(chunk)
    if not parts:
        return pd.DataFrame(columns=["gu", "factor", "value", "unit", "source", "year"]), statuses
    return pd.concat(parts, ignore_index=True), statuses


def _merge_factors_long(*frames: pd.DataFrame) -> pd.DataFrame:
    cols = ["gu", "factor", "value", "unit", "source", "year"]
    nonempty = [f for f in frames if f is not None and not f.empty]
    if not nonempty:
        return pd.DataFrame(columns=cols)
    merged = pd.concat(nonempty, ignore_index=True)
    merged["gu"] = merged["gu"].astype(str).str.strip()
    merged["factor"] = merged["factor"].astype(str).str.strip()
    return merged.drop_duplicates(subset=["gu", "factor"], keep="first").reset_index(drop=True)


def _read_factors_long() -> pd.DataFrame:
    """
    Returns normalized long-format factors:
      gu,factor,value,unit,source,year

    Priority:
    1) data/factors/seoul_gu_features_combined_wide.csv (7 factors, wide)
    2) data/factors/gu_factors.csv (legacy, long)
    """
    wide_fp = _factors_wide_csv_path()
    if wide_fp.exists():
        try:
            dfw = pd.read_csv(wide_fp)
        except Exception:
            dfw = pd.DataFrame()
        if not dfw.empty:
            dfw["gu"] = dfw["gu"].astype(str).str.strip()
            keep = [
                ("average_monthly_income_krw", "원", dfw.get("average_income_period")),
                ("foreigner_resident_ratio_pct", "%", dfw.get("foreigner_ratio_year")),
                ("population_density_persons_per_km2", "명/㎢", dfw.get("population_density_year")),
                ("distance_from_seoul_center_km", "km", None),
                ("mountain_forest_proxy_ratio_pct", "%", dfw.get("mountain_forest_proxy_year")),
                ("elderly_65plus_ratio_pct", "%", dfw.get("aging_ratio_period")),
                ("income_std_proxy_krw_per_month", "원", dfw.get("income_std_proxy_period")),
            ] + _extra_wide_factor_columns()
            out_rows: list[dict[str, Any]] = []
            for col, unit, period_series in keep:
                if col not in dfw.columns:
                    continue
                vals = pd.to_numeric(dfw[col], errors="coerce")
                years = period_series.astype(str).str.strip() if period_series is not None else None
                for i, gu in enumerate(dfw["gu"].astype(str).tolist()):
                    v = vals.iloc[i]
                    if v is None or not np.isfinite(v):
                        continue
                    out_rows.append(
                        {
                            "gu": str(gu).strip(),
                            "factor": col,
                            "value": float(v),
                            "unit": unit,
                            "source": "",
                            "year": (str(years.iloc[i]) if years is not None else ""),
                        }
                    )
            base_df = pd.DataFrame(out_rows, columns=["gu", "factor", "value", "unit", "source", "year"])
            supp_df, _ = _read_supplemental_factors_long()
            return _merge_factors_long(base_df, supp_df)

    fp = _factors_csv_path()
    if not fp.exists():
        supp_df, _ = _read_supplemental_factors_long()
        return supp_df
    df = pd.read_csv(fp)
    if df.empty:
        supp_df, _ = _read_supplemental_factors_long()
        return supp_df
    df["gu"] = df["gu"].astype(str).str.strip()
    df["factor"] = df["factor"].astype(str).str.strip()
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    supp_df, _ = _read_supplemental_factors_long()
    return _merge_factors_long(df, supp_df)


def _factor_category_from_name(name: str) -> str:
    """메타에 category가 없을 때 컬럼명 휴리스틱(인구/소득/지리)."""
    s = (name or "").lower()
    if any(k in s for k in ("employment", "employ", "고용")):
        return "income"
    if any(k in s for k in ("park", "공원")):
        return "geo"
    if any(k in s for k in ("household", "single_person", "1인", "가구")):
        return "population"
    if any(k in s for k in ("income", "krw", "salary", "wage", "earn", "proxy_krw")):
        return "income"
    if any(k in s for k in ("dist", "km", "mountain", "forest", "geo", "center", "cityhall", "centroid")):
        return "geo"
    return "population"


def _default_wide_factor_meta() -> dict[str, dict[str, Any]]:
    """Wide CSV(7요인) 컬럼명에 맞는 기본 라벨·범주. gu_factors_meta.json과 병합 시 파일 쪽이 같은 키에서 우선."""
    return {
        "average_monthly_income_krw": {
            "label_ko": "평균소득(월, 원)",
            "category": "income",
            "unit": "원",
            "summary_ko": "국민연금공단 시군구 평균소득월액(대략적 proxy).",
        },
        "foreigner_resident_ratio_pct": {
            "label_ko": "외국인 거주비율(%, 등록외국인/인구)",
            "category": "population",
            "unit": "%",
            "summary_ko": "등록외국인 수 ÷ 해당 구 인구 × 100 (실제 체류 외국인과 차이 가능).",
        },
        "population_density_persons_per_km2": {
            "label_ko": "인구밀도(명/㎢)",
            "category": "population",
            "unit": "명/㎢",
            "summary_ko": "서울시 통계의 구별 인구밀도(총인구/면적 기준).",
        },
        "distance_from_seoul_center_km": {
            "label_ko": "위치/중심거리(km)",
            "category": "geo",
            "unit": "km",
            "summary_ko": "각 구 대표 중심점과 서울 중심점(시청 근처) 간의 Haversine 거리(계산 proxy).",
        },
        "mountain_forest_proxy_ratio_pct": {
            "label_ko": "산/임야 비율 proxy(%)",
            "category": "geo",
            "unit": "%",
            "summary_ko": "자치구 면적 중 산/임야로 분류된 비율(지형 proxy, 고도/경사 기반 아님).",
        },
        "elderly_65plus_ratio_pct": {
            "label_ko": "고령화 비율(65세+, %)",
            "category": "population",
            "unit": "%",
            "summary_ko": "65세 이상 인구 ÷ 전체 인구 × 100 (공표 통계).",
        },
        "income_std_proxy_krw_per_month": {
            "label_ko": "소득 표준편차 proxy(원/월)",
            "category": "income",
            "unit": "원",
            "summary_ko": "공식 구별 소득 표준편차가 없어, 평균소득×가정 CV로 만든 proxy(정식 통계 아님).",
        },
        "single_person_household_ratio_pct": {
            "label_ko": "1인 가구 비율(%)",
            "category": "population",
            "unit": "%",
            "summary_ko": "1인가구÷일반가구×100 (2024 시군구 합계, 주민등록 가구통계).",
            "csv_slot": "data/factors/supplemental/single_person_household_ratio_pct.csv",
        },
        "employment_rate_pct": {
            "label_ko": "고용률(%)",
            "category": "income",
            "unit": "%",
            "summary_ko": "15세+ 시군구 고용률(2025 Q2, KOSIS 경제활동인구 총괄).",
            "csv_slot": "data/factors/supplemental/employment_rate_pct.csv",
        },
        "park_area_total_m2": {
            "label_ko": "공원 면적(㎡, 합계)",
            "category": "geo",
            "unit": "m²",
            "summary_ko": "2024 구별 공원 면적 합(천㎡→㎡, 종류별 단순 합산 proxy).",
            "csv_slot": "data/factors/supplemental/park_area_total_m2.csv",
        },
    }


def _read_factors_meta() -> dict[str, Any]:
    wide_meta = _default_wide_factor_meta()
    fp = _factors_meta_path()
    base: dict[str, Any] = {}
    if fp.exists():
        try:
            import json as _json

            raw = _json.loads(fp.read_text(encoding="utf-8"))
            base = raw if isinstance(raw, dict) else {}
        except Exception:
            base = {}
    merged_factors: dict[str, Any] = dict(wide_meta)
    user_fac = base.get("factors")
    if isinstance(user_fac, dict):
        for name, umeta in user_fac.items():
            if not isinstance(umeta, dict):
                continue
            if name in merged_factors and isinstance(merged_factors[name], dict):
                merged_factors[name] = {**merged_factors[name], **umeta}
            else:
                merged_factors[name] = dict(umeta)
    if not fp.exists():
        if _factors_wide_csv_path().exists():
            return {
                "generated_by": "src/web_api.py (wide defaults)",
                "source_csv": str(_factors_wide_csv_path()),
                "factors": merged_factors,
            }
        return {}
    out = {**base, "factors": merged_factors}
    if _factors_wide_csv_path().exists():
        out.setdefault("source_csv", str(_factors_wide_csv_path()))
    return out


def _coverage_from_hist(hist: list[float] | None, matched_weight: float | None, thr_pct: int) -> float | None:
    if not hist or matched_weight is None:
        return None
    try:
        tot = float(matched_weight)
        if not np.isfinite(tot) or tot <= 0:
            return None
        t = int(max(0, min(100, int(thr_pct))))
        hit = 0.0
        for i in range(t, 101):
            hit += float(hist[i] if i < len(hist) else 0.0)
        return (hit / tot) * 100.0
    except Exception:
        return None


def _spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    if a.size < 2 or b.size < 2:
        return None
    # rank with average ties using pandas
    ra = pd.Series(a).rank(method="average").to_numpy(dtype=float)
    rb = pd.Series(b).rank(method="average").to_numpy(dtype=float)
    r = np.corrcoef(ra, rb)[0, 1]
    return float(r) if np.isfinite(r) else None


def _vif_table(x: pd.DataFrame) -> list[dict[str, Any]]:
    """
    Variance Inflation Factor: VIF_j = 1 / (1 - R²_j), where R²_j is from OLS of
    standardized column j on all other standardized columns (+ intercept).

    - Drops near-constant columns (std ~ 0) before z-scoring.
    - Uses stabilized lstsq (rcond) and clamps R² to [0, 1) to avoid numerical blow-ups.
    """
    cols = list(x.columns)
    if len(cols) < 2:
        return []
    xz = x[cols].astype(float)
    stds = xz.std(ddof=0)
    usable = [c for c in cols if float(stds.get(c, 0) or 0.0) > 1e-12]
    if len(usable) < 2:
        return []
    xz = xz[usable]
    z = (xz - xz.mean()) / xz.std(ddof=0).replace(0, np.nan)
    z = z.replace([np.inf, -np.inf], np.nan).dropna(axis=1, how="any")
    cols = list(z.columns)
    if len(cols) < 2:
        return []

    out: list[dict[str, Any]] = []
    n_rows, rcond = z.shape[0], 1e-10
    for c in cols:
        y = z[c].to_numpy(dtype=float)
        others = [cc for cc in cols if cc != c]
        X = z[others].to_numpy(dtype=float)
        X = np.concatenate([np.ones((X.shape[0], 1)), X], axis=1)
        try:
            beta, residuals, rank, s = np.linalg.lstsq(X, y, rcond=rcond)
        except Exception:
            continue
        yhat = X @ beta
        ssr = float(np.sum((y - yhat) ** 2))
        sst = float(np.sum((y - float(np.mean(y))) ** 2))
        if sst <= 1e-15:
            r2 = 0.0
        else:
            r2 = 1.0 - (ssr / sst)
        if not np.isfinite(r2):
            r2 = 0.0
        # OLS R² can slightly leave [0,1] with ill-conditioned X; clamp for VIF formula
        r2 = float(np.clip(r2, 0.0, 0.999999))
        if rank < min(X.shape[1], n_rows) - 1:
            r2 = min(r2, 0.999)
        vif = (1.0 / (1.0 - r2)) if r2 < 0.999999 else float("inf")
        out.append({"factor": c, "vif": float(vif), "r2": float(r2)})
    out.sort(key=lambda r: (np.inf if not np.isfinite(r["vif"]) else r["vif"]), reverse=True)
    return out


@app.get("/api/factors/table")
def factors_table():
    """
    요인표(정규화된 long-format).
    - 우선순위: `data/factors/seoul_gu_features_combined_wide.csv` (기본 7요인 + 선택 컬럼)
      → `data/factors/supplemental/*.csv` (1인가구·고용률·공원면적 슬롯)
      → `data/factors/gu_factors.csv`(legacy)
    """
    df = _read_factors_long()
    meta = _read_factors_meta()
    _, supplemental_status = _read_supplemental_factors_long()
    return {
        "empty": bool(df.empty),
        "csv": str(_factors_wide_csv_path() if _factors_wide_csv_path().exists() else _factors_csv_path()),
        "supplemental_dir": str(_factors_supplemental_dir()),
        "supplemental": supplemental_status,
        "rows": df.to_dict(orient="records"),
        "meta": meta,
    }


@app.get("/api/factors/analysis")
def factors_analysis(
    coverage_thr_pct: int = Query(20, ge=0, le=95),
    borrow_min: float = Query(0.0, ge=0.0, le=10.0),
):
    """
    소가설2 분석용 요인 통계:
    - F1/Depth/Coverage와 요인 간 상관(pearson/spearman)
    - 요인 간 상관행렬(pearson)
    - VIF(다중공선성) (수치 요인만)

    F1은 프런트 정의와 동일:
      F1 = 2 * (Depth/100) * (Coverage/100) / ((Depth/100)+(Coverage/100))
    Coverage는 district_savings.json의 히스토그램으로 임계(coverage_thr_pct) 기준 계산.

    borrow_min(따릉이 대여 소요시간, 분) > 0이면 따릉이 시간에 그만큼 더해
    OD pair-level 데이터로 구별 depth/coverage/F1을 재계산한다(0이면 기존 JSON 경로).
    """
    root = Path(__file__).resolve().parent.parent
    savings_fp = root / "frontend" / "public" / "district_savings.json"
    if not savings_fp.exists():
        raise HTTPException(404, "district_savings.json not found (run /api/district-savings/rebuild first)")

    import json as _json

    savings = _json.loads(savings_fp.read_text(encoding="utf-8"))
    districts = savings.get("districts") or []
    hist_by_gu = savings.get("coverage_hist_1pct_by_gu") or {}
    matched_w_by_gu = savings.get("matched_weight_by_gu") or {}

    use_borrow = float(borrow_min) > 1e-9
    borrow_by_gu: dict[str, dict[str, float]] = {}
    if use_borrow:
        od_dir = Path(os.getenv("OD_DISTRICT_DIR") or _default_od_dir())
        tmap_dir = _tmap_by_district_dir()
        if od_dir.exists() and tmap_dir.exists():
            try:
                borrow_by_gu = _per_gu_f1_with_borrow(float(borrow_min), int(coverage_thr_pct), od_dir, tmap_dir)
            except Exception:
                borrow_by_gu = {}
        if not borrow_by_gu:
            use_borrow = False  # 데이터 못 읽으면 기존 경로로 폴백

    # build target metrics by gu
    rows_t: list[dict[str, Any]] = []
    if use_borrow:
        for gu, m in borrow_by_gu.items():
            rows_t.append(
                {
                    "gu": str(gu),
                    "depth_pct": float(m["depth_pct"]),
                    "coverage_pct": float(m["coverage_pct"]),
                    "f1": float(m["f1"]),
                }
            )
    else:
        for d in districts:
            gu = str(d.get("gu") or "").strip()
            dep = d.get("depth_pct")
            try:
                depth = float(dep) if dep is not None else None
            except Exception:
                depth = None
            cov = _coverage_from_hist(hist_by_gu.get(gu), matched_w_by_gu.get(gu), int(coverage_thr_pct))
            if depth is None or cov is None or not np.isfinite(depth) or not np.isfinite(cov):
                continue
            dd = max(0.0, min(1.0, depth / 100.0))
            cc = max(0.0, min(1.0, cov / 100.0))
            s = dd + cc
            f1 = (2 * dd * cc / s) if s > 0 else 0.0
            rows_t.append({"gu": gu, "depth_pct": float(depth), "coverage_pct": float(cov), "f1": float(f1)})

    targets = pd.DataFrame(rows_t)
    if targets.empty:
        return {
            "empty": True,
            "coverage_thr_pct": int(coverage_thr_pct),
            "error": "No usable targets (need depth+coverage per gu).",
        }

    # factors (wide)
    long = _read_factors_long()
    if long.empty:
        return {
            "empty": True,
            "coverage_thr_pct": int(coverage_thr_pct),
            "error": "No factors (gu_factors.csv missing).",
        }
    wide = long.pivot_table(index="gu", columns="factor", values="value", aggfunc="mean")
    merged = targets.merge(wide, left_on="gu", right_index=True, how="left")

    meta = _read_factors_meta()
    factor_meta = (meta.get("factors") or {}) if isinstance(meta, dict) else {}

    factor_cols = [c for c in wide.columns if c in merged.columns]
    corr_rows: list[dict[str, Any]] = []
    for c in factor_cols:
        sub = merged[["f1", "depth_pct", "coverage_pct", c]].dropna()
        if len(sub) < 4:
            continue
        x = sub[c].to_numpy(dtype=float)
        for tgt in ["f1", "depth_pct", "coverage_pct"]:
            y = sub[tgt].to_numpy(dtype=float)
            pr = float(np.corrcoef(x, y)[0, 1])
            sr = _spearman(x, y)
            pearson_p: float | None = None
            spearman_p: float | None = None
            n_sub = int(len(sub))
            if n_sub >= 3 and np.isfinite(pr):
                try:
                    _, pearson_p = scipy_stats.pearsonr(x, y)
                    pearson_p = float(pearson_p) if np.isfinite(pearson_p) else None
                except Exception:
                    pearson_p = None
            if n_sub >= 3 and sr is not None and np.isfinite(sr):
                try:
                    _, spearman_p = scipy_stats.spearmanr(x, y)
                    spearman_p = float(spearman_p) if np.isfinite(spearman_p) else None
                except Exception:
                    spearman_p = None
            m = factor_meta.get(c) if isinstance(factor_meta, dict) else None
            raw_cat = (m.get("category") if isinstance(m, dict) else None)
            cat0 = str(raw_cat).strip().lower() if raw_cat else ""
            cat = cat0 if cat0 in ("population", "income", "geo") else _factor_category_from_name(c)
            corr_rows.append(
                {
                    "factor": c,
                    "category": cat,
                    "target": tgt,
                    "n": n_sub,
                    "pearson_r": float(pr) if np.isfinite(pr) else None,
                    "pearson_p": pearson_p,
                    "spearman_r": float(sr) if (sr is not None and np.isfinite(sr)) else None,
                    "spearman_p": spearman_p,
                }
            )

    # factor-factor correlation matrix (pearson) on rows with any factor values
    factor_df = merged[factor_cols].copy()
    n_gu = int(len(targets))
    # per-factor non-null: allow smaller 구 panels while still excluding sparse columns
    min_nonnull = max(4, min(8, n_gu // 2))
    keep_cols = [c for c in factor_cols if int(factor_df[c].notna().sum()) >= min_nonnull]
    factor_df = factor_df[keep_cols]
    corr_min_p = max(3, min_nonnull - 1)
    corr_mat = factor_df.corr(method="pearson", min_periods=corr_min_p) if not factor_df.empty else pd.DataFrame()

    # VIF on complete cases (all selected factors non-null per row)
    vif_rows: list[dict[str, Any]] = []
    if len(keep_cols) >= 2:
        complete = factor_df.dropna()
        min_complete = max(max(5, len(keep_cols) + 1), min(10, n_gu))
        if len(complete) >= min_complete and complete.shape[1] >= 2:
            vif_rows = _vif_table(complete)

    # 가설 1과 동일한 구별 F1 산술평균이 운영 임계(0.25)를 넘는지에 대한 보조 통계
    # (구를 i.i.d. 표본으로 보는 단순화; 해석은 보고서에 한 줄 부연 권장)
    f1_vals = targets["f1"].to_numpy(dtype=float)
    f1_mean = float(np.mean(f1_vals)) if len(f1_vals) else None
    mean_f1_stats: dict[str, Any] | None = None
    f1_threshold = 0.25
    if f1_mean is not None and len(f1_vals) >= 2 and np.all(np.isfinite(f1_vals)):
        t_res = scipy_stats.ttest_1samp(f1_vals, f1_threshold, alternative="greater")
        rng = np.random.default_rng(42)
        b = 5000
        boot_means = np.empty(b, dtype=float)
        n_f = len(f1_vals)
        for i in range(b):
            idx = rng.integers(0, n_f, size=n_f)
            boot_means[i] = float(np.mean(f1_vals[idx]))
        ci_low, ci_high = float(np.percentile(boot_means, 2.5)), float(np.percentile(boot_means, 97.5))
        mean_f1_stats = {
            "n_gu": int(len(f1_vals)),
            "mean_f1": f1_mean,
            "threshold_f1": f1_threshold,
            "t_stat": float(t_res.statistic) if np.isfinite(t_res.statistic) else None,
            "p_value_mean_gt_threshold_t": float(t_res.pvalue) if np.isfinite(t_res.pvalue) else None,
            "bootstrap_b": b,
            "bootstrap_mean_ci95": [ci_low, ci_high],
        }

    f1_by_gu = {
        str(r["gu"]): float(r["f1"])
        for r in rows_t
        if r.get("gu") and r.get("f1") is not None and np.isfinite(float(r["f1"]))
    }

    return {
        "empty": False,
        "coverage_thr_pct": int(coverage_thr_pct),
        "borrow_min": float(borrow_min),
        "targets_n": int(len(targets)),
        "factors_n": int(len(factor_cols)),
        "mean_f1_stats": mean_f1_stats,
        "corr_rows": corr_rows,
        "f1_by_gu": f1_by_gu,
        "factor_corr": {
            "factors": list(corr_mat.columns),
            "matrix": corr_mat.round(6).to_numpy().tolist() if not corr_mat.empty else [],
        },
        "vif": vif_rows,
        "meta": meta,
    }


@app.get("/api/factors/linear-cano-analysis")
def factors_linear_cano_analysis(
    coverage_thr_pct: int = Query(20, ge=0, le=95),
    alpha: float = Query(0.05, gt=0, lt=1),
    abs_r_threshold: float = Query(0.2, ge=0, le=1),
    min_n: int = Query(15, ge=4, le=50),
):
    """
    연관 요인(|r|≥임계)에 대한 단순 선형 회귀 F1~X_j 및 CCA(CANO).
    단순 회귀 비유의 요인은 X블록 정준상관으로 다변량 해석.
    """
    from .factor_linear_cano import run_associated_factor_analysis

    base = factors_analysis(coverage_thr_pct=coverage_thr_pct)
    if base.get("empty"):
        return {**base, "linear_cano_empty": True}

    root = Path(__file__).resolve().parent.parent
    savings_fp = root / "frontend" / "public" / "district_savings.json"
    import json as _json

    savings = _json.loads(savings_fp.read_text(encoding="utf-8"))
    districts = savings.get("districts") or []
    hist_by_gu = savings.get("coverage_hist_1pct_by_gu") or {}
    matched_w_by_gu = savings.get("matched_weight_by_gu") or {}

    rows_t: list[dict[str, Any]] = []
    for d in districts:
        gu = str(d.get("gu") or "").strip()
        dep = d.get("depth_pct")
        try:
            depth = float(dep) if dep is not None else None
        except Exception:
            depth = None
        cov = _coverage_from_hist(hist_by_gu.get(gu), matched_w_by_gu.get(gu), int(coverage_thr_pct))
        if depth is None or cov is None or not np.isfinite(depth) or not np.isfinite(cov):
            continue
        rows_t.append(
            {
                "gu": gu,
                "depth_pct": float(depth),
                "coverage_pct": float(cov),
                "f1": _f1_from_depth_coverage(float(depth), float(cov)),
            }
        )
    targets = pd.DataFrame(rows_t)
    long = _read_factors_long()
    if long.empty or targets.empty:
        return {"empty": True, "error": "No merged panel for linear/CCA."}
    wide = long.pivot_table(index="gu", columns="factor", values="value", aggfunc="mean")
    merged = targets.merge(wide, left_on="gu", right_index=True, how="left")
    meta = _read_factors_meta()
    factor_meta = (meta.get("factors") or {}) if isinstance(meta, dict) else {}

    lc = run_associated_factor_analysis(
        merged,
        factor_meta,
        abs_r_threshold=float(abs_r_threshold),
        min_n=int(min_n),
        alpha=float(alpha),
        corr_rows=base.get("corr_rows") or [],
    )
    return {
        "coverage_thr_pct": int(coverage_thr_pct),
        "corr_summary": base,
        "linear_cano": lc,
    }


def _f1_from_depth_coverage(depth_pct: float, coverage_pct: float) -> float:
    d = max(0.0, min(1.0, float(depth_pct) / 100.0))
    c = max(0.0, min(1.0, float(coverage_pct) / 100.0))
    s = d + c
    return float((2 * d * c / s) if s > 0 else 0.0)


def _max_mtime_glob(dir_path: Path, pattern: str) -> int:
    mx = 0
    try:
        for p in dir_path.glob(pattern):
            try:
                mx = max(mx, int(p.stat().st_mtime))
            except OSError:
                pass
    except OSError:
        pass
    return mx


# (coverage_thr, od_mtime, tmap_mtime) -> (w, trans, diffpos, hit) float32 arrays — OD/TMAP 전역 스캔 1회만
_F1_HOMO_TRIP_POOL: dict[tuple[int, int, int], tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}


def _load_f1_homogeneity_trip_arrays(
    coverage_thr_pct: int, od_dir: Path, tmap_dir: Path
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """OD + 구별 TMAP OK 캐시에서 풀 관측치(transit, diffpos+, hit, weight)를 numpy로 적재."""
    key = (
        int(coverage_thr_pct),
        _max_mtime_glob(od_dir, "*.csv"),
        _max_mtime_glob(tmap_dir, "*_tmap_pairs.csv"),
    )
    cached = _F1_HOMO_TRIP_POOL.get(key)
    if cached is not None:
        return cached

    trans: list[float] = []
    diffpos: list[float] = []
    hitl: list[float] = []
    wts: list[float] = []

    def _pair_key(a: str, b: str) -> tuple[str, str]:
        return (a, b) if a < b else (b, a)

    def _ratio_hit(transit_min: float, bike_min: float, thr: int) -> float:
        if transit_min <= 0:
            return 0.0
        d = (transit_min - bike_min) / transit_min * 100.0
        return 1.0 if d >= float(thr) else 0.0

    def _clean_header(s: str) -> str:
        return (s or "").replace("\ufeff", "").strip()

    def _pick_field(fieldnames: list[str], want: str) -> str:
        m = {_clean_header(f): f for f in fieldnames}
        k = _clean_header(want)
        if k not in m:
            raise ValueError(f"missing column '{want}'")
        return m[k]

    for fp in sorted([p for p in od_dir.glob("*.csv") if p.is_file()]):
        gu = fp.stem.replace("_시간_거리", "")
        cache_path = tmap_dir / f"{gu}_tmap_pairs.csv"
        if not cache_path.exists():
            continue
        tmap: dict[tuple[str, str], float] = {}
        try:
            with open(cache_path, "r", encoding="utf-8", newline="") as f:
                r = _csv.DictReader(f)
                if not r.fieldnames:
                    continue
                ak = _pick_field(r.fieldnames, "a_id")
                bk = _pick_field(r.fieldnames, "b_id")
                tk = _pick_field(r.fieldnames, "transit_total_min_1dp")
                sk = _pick_field(r.fieldnames, "transit_status")
                for row in r:
                    if (row.get(sk) or "").strip() != "OK":
                        continue
                    a = (row.get(ak) or "").strip()
                    b = (row.get(bk) or "").strip()
                    if not a or not b or a == b:
                        continue
                    try:
                        t = float((row.get(tk) or "").strip())
                    except Exception:
                        continue
                    tmap[_pair_key(a, b)] = t
        except Exception:
            continue

        try:
            with open(fp, "r", encoding="utf-8", newline="") as f:
                r = _csv.DictReader(f)
                if not r.fieldnames:
                    continue
                sk = _pick_field(r.fieldnames, "시작_대여소_ID")
                ek = _pick_field(r.fieldnames, "종료_대여소_ID")
                bk = _pick_field(r.fieldnames, "전체_이용_분")
                wk = _pick_field(r.fieldnames, "빈도")
                for row in r:
                    a = (row.get(sk) or "").strip()
                    b = (row.get(ek) or "").strip()
                    if not a or not b or a == b:
                        continue
                    try:
                        bike_min = float((row.get(bk) or "").strip())
                        w = float((row.get(wk) or "").strip())
                    except Exception:
                        continue
                    if not np.isfinite(w) or w <= 0:
                        continue
                    t = tmap.get(_pair_key(a, b))
                    if t is None or not np.isfinite(t):
                        continue
                    dpos = max(float(t) - float(bike_min), 0.0)
                    trans.append(float(t))
                    diffpos.append(dpos)
                    hitl.append(_ratio_hit(float(t), float(bike_min), int(coverage_thr_pct)))
                    wts.append(float(w))
        except Exception:
            continue

    w = np.array(wts, dtype=np.float64)
    ta = np.array(trans, dtype=np.float64)
    da = np.array(diffpos, dtype=np.float64)
    ha = np.array(hitl, dtype=np.float64)
    _F1_HOMO_TRIP_POOL[key] = (w, ta, da, ha)
    return w, ta, da, ha


# (od_mtime, tmap_mtime) -> {gu: {"t","bike","w": np.ndarray}} — 대여시간 슬라이더 재계산용 pair-level 풀
_PAIR_LEVEL_POOL: dict[tuple[int, int], dict[str, dict[str, np.ndarray]]] = {}


def _load_pair_level_by_gu(od_dir: Path, tmap_dir: Path) -> dict[str, dict[str, np.ndarray]]:
    """OD + 구별 TMAP OK 캐시에서 (transit, bike, weight)를 구별로 적재. 대여시간 A를 더해 depth/coverage 재계산용."""
    key = (_max_mtime_glob(od_dir, "*.csv"), _max_mtime_glob(tmap_dir, "*_tmap_pairs.csv"))
    cached = _PAIR_LEVEL_POOL.get(key)
    if cached is not None:
        return cached

    def _pair_key(a: str, b: str) -> tuple[str, str]:
        return (a, b) if a < b else (b, a)

    def _clean_header(s: str) -> str:
        return (s or "").replace("\ufeff", "").strip()

    def _pick_field(fieldnames: list[str], want: str) -> str:
        m = {_clean_header(f): f for f in fieldnames}
        k = _clean_header(want)
        if k not in m:
            raise ValueError(f"missing column '{want}'")
        return m[k]

    out: dict[str, dict[str, np.ndarray]] = {}
    for fp in sorted([p for p in od_dir.glob("*.csv") if p.is_file()]):
        gu = fp.stem.replace("_시간_거리", "")
        cache_path = tmap_dir / f"{gu}_tmap_pairs.csv"
        if not cache_path.exists():
            continue
        tmap: dict[tuple[str, str], float] = {}
        try:
            with open(cache_path, "r", encoding="utf-8", newline="") as f:
                r = _csv.DictReader(f)
                if not r.fieldnames:
                    continue
                ak = _pick_field(r.fieldnames, "a_id")
                bk = _pick_field(r.fieldnames, "b_id")
                tk = _pick_field(r.fieldnames, "transit_total_min_1dp")
                sk = _pick_field(r.fieldnames, "transit_status")
                for row in r:
                    if (row.get(sk) or "").strip() != "OK":
                        continue
                    a = (row.get(ak) or "").strip()
                    b = (row.get(bk) or "").strip()
                    if not a or not b or a == b:
                        continue
                    try:
                        t = float((row.get(tk) or "").strip())
                    except Exception:
                        continue
                    tmap[_pair_key(a, b)] = t
        except Exception:
            continue

        ts: list[float] = []
        bikes: list[float] = []
        wts2: list[float] = []
        try:
            with open(fp, "r", encoding="utf-8", newline="") as f:
                r = _csv.DictReader(f)
                if not r.fieldnames:
                    continue
                sk = _pick_field(r.fieldnames, "시작_대여소_ID")
                ek = _pick_field(r.fieldnames, "종료_대여소_ID")
                bk = _pick_field(r.fieldnames, "전체_이용_분")
                wk = _pick_field(r.fieldnames, "빈도")
                for row in r:
                    a = (row.get(sk) or "").strip()
                    b = (row.get(ek) or "").strip()
                    if not a or not b or a == b:
                        continue
                    try:
                        bike_min = float((row.get(bk) or "").strip())
                        w = float((row.get(wk) or "").strip())
                    except Exception:
                        continue
                    if not np.isfinite(w) or w <= 0:
                        continue
                    t = tmap.get(_pair_key(a, b))
                    if t is None or not np.isfinite(t):
                        continue
                    ts.append(float(t))
                    bikes.append(float(bike_min))
                    wts2.append(float(w))
        except Exception:
            continue

        if ts:
            out[gu] = {
                "t": np.array(ts, dtype=np.float64),
                "bike": np.array(bikes, dtype=np.float64),
                "w": np.array(wts2, dtype=np.float64),
            }

    _PAIR_LEVEL_POOL[key] = out
    return out


def _per_gu_f1_with_borrow(
    borrow_min: float, coverage_thr_pct: int, od_dir: Path, tmap_dir: Path
) -> dict[str, dict[str, float]]:
    """대여시간 A(분)를 따릉이 시간에 더했을 때 구별 depth/coverage/F1을 재계산.

    depth_pct    = Σ max(transit − (bike + A), 0)·w / Σ transit·w × 100
    coverage_pct = Σ[ (transit − bike − A)/transit×100 ≥ thr ]·w / Σ w × 100   (matched-only)
    F1           = 2·d·c / (d + c)  (d, c는 0~1 정규화)
    """
    data = _load_pair_level_by_gu(od_dir, tmap_dir)
    A = float(borrow_min)
    thr = float(int(coverage_thr_pct))
    res: dict[str, dict[str, float]] = {}
    for gu, d in data.items():
        t = d["t"]
        bike = d["bike"]
        w = d["w"]
        if t.size == 0:
            continue
        adj = t - bike - A
        dpos = np.maximum(adj, 0.0)
        denom = float(np.sum(t * w))
        if denom <= 0:
            continue
        depth = float(np.sum(dpos * w) / denom * 100.0)
        matched_w = float(np.sum(w))
        if matched_w <= 0:
            continue
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.where(t > 0, adj / t * 100.0, -1.0)
        hit = (ratio >= thr).astype(np.float64)
        coverage = float(np.sum(hit * w) / matched_w * 100.0)
        dd = max(0.0, min(1.0, depth / 100.0))
        cc = max(0.0, min(1.0, coverage / 100.0))
        s = dd + cc
        f1 = (2.0 * dd * cc / s) if s > 0 else 0.0
        res[gu] = {"depth_pct": depth, "coverage_pct": coverage, "f1": f1}
    return res


@app.get("/api/f1/homogeneity-test")
def f1_homogeneity_test(
    coverage_thr_pct: int = Query(20, ge=0, le=95),
    alpha: float = Query(0.05, gt=0.0, lt=1.0),
    mc_sims: int = Query(10000, ge=80, le=10000),
    sample_n: int = Query(10000, ge=3000, le=200000),
    random_seed: int = Query(42, ge=0, le=2**31 - 1),
    min_pooled_observations: int = Query(800, ge=200, le=500000),
):
    """
    소가설2 설명 전, "구별 F1이 동일하다"는 귀무가설을 통계적으로 검정.

    엄밀하게는 구별로 1개의 요약값(F1)만 있으면 검정이 불가능하므로,
    관내이동 CSV의 trip-row(빈도 가중)를 관측치로 사용하여
      H0: 구별 label은 F1 생성 과정에 영향이 없다(=모든 구가 같은 분포)
    를 가정하고, label permutation/재표본 기반 Monte Carlo 검정을 수행한다.

    - test statistic: 구별 F1들의 분산 Var(F1_gu)
    - p-value: null 시뮬레이션에서 Var >= Var_obs 비율

    Note: 빈도 가중(continuous weight) 때문에 완전한 "정확 퍼뮤테이션" 대신,
          가중치에 비례해 관측치를 sample_n개 재표본한 뒤,
          관측된 구별 matched_weight 비율대로 구 라벨을 무작위 할당하여 H0를 근사한다.
    """
    root = Path(__file__).resolve().parent.parent
    savings_fp = root / "frontend" / "public" / "district_savings.json"
    if not savings_fp.exists():
        raise HTTPException(404, "district_savings.json not found (run /api/district-savings/rebuild first)")

    import json as _json

    savings = _json.loads(savings_fp.read_text(encoding="utf-8"))
    districts = savings.get("districts") or []
    hist_by_gu = savings.get("coverage_hist_1pct_by_gu") or {}
    matched_w_by_gu = savings.get("matched_weight_by_gu") or {}

    # observed F1 by gu (same definition as frontend)
    obs_rows: list[dict[str, Any]] = []
    for d in districts:
        gu = str(d.get("gu") or "").strip()
        dep = d.get("depth_pct")
        try:
            depth = float(dep) if dep is not None else None
        except Exception:
            depth = None
        cov = _coverage_from_hist(hist_by_gu.get(gu), matched_w_by_gu.get(gu), int(coverage_thr_pct))
        if depth is None or cov is None:
            continue
        if not np.isfinite(depth) or not np.isfinite(cov):
            continue
        f1 = _f1_from_depth_coverage(depth, cov)
        obs_rows.append({"gu": gu, "depth_pct": depth, "coverage_pct": cov, "f1": f1})

    if len(obs_rows) < 8:
        return {
            "empty": True,
            "error": "유효한 구가 너무 적어서 검정을 수행할 수 없습니다.",
            "coverage_thr_pct": int(coverage_thr_pct),
        }

    obs = pd.DataFrame(obs_rows)
    obs_f1 = obs["f1"].to_numpy(dtype=float)
    obs_var = float(np.var(obs_f1, ddof=0))
    obs_mean_f1 = float(np.mean(obs_f1))
    obs_by_gu = sorted(
        (
            {
                "gu": str(r["gu"]),
                "depth_pct": float(r["depth_pct"]),
                "coverage_pct": float(r["coverage_pct"]),
                "f1": float(r["f1"]),
            }
            for r in obs_rows
        ),
        key=lambda x: x["gu"],
    )

    # Build pooled observations from OD files + per-district TMAP OK cache
    od_dir = Path(os.getenv("OD_DISTRICT_DIR") or _default_od_dir())
    tmap_dir = _tmap_by_district_dir()
    if not od_dir.exists():
        raise HTTPException(404, f"OD dir not found: {od_dir}")
    if not tmap_dir.exists():
        raise HTTPException(404, f"TMAP dir not found: {tmap_dir}")

    w, trans_a, diffpos_a, hit_a = _load_f1_homogeneity_trip_arrays(int(coverage_thr_pct), od_dir, tmap_dir)
    wts_len = int(w.shape[0])

    min_pool = int(min_pooled_observations)
    rng = np.random.default_rng(int(random_seed))

    def _response_common(
        *,
        null_arr: np.ndarray,
        pval: float,
        ge_count: int,
        test_mode: str,
        note: str,
        method_ko: str,
        null_sample_n: int,
    ) -> dict[str, Any]:
        b_total = int(null_arr.size)
        return {
            "empty": False,
            "alpha": float(alpha),
            "coverage_thr_pct": int(coverage_thr_pct),
            "test_mode": test_mode,
            "observed": {
                "var_f1": obs_var,
                "districts_n": int(len(obs_rows)),
                "mean_f1": obs_mean_f1,
                "by_gu": obs_by_gu,
            },
            "null": {
                "mc_sims": int(mc_sims),
                "sample_n": int(null_sample_n),
                "var_f1_mean": float(np.mean(null_arr)),
                "var_f1_p95": float(np.quantile(null_arr, 0.95)),
                # 귀무 분포에서 '관측 통계량 이상'이 나온 횟수 (꼬리 개수)
                "ge_count": int(ge_count),
                "b_total": b_total,
            },
            "p_value": pval,
            # p는 add-one 추정량 (b+1)/(B+1) 이라 절대 0이 되지 않음. 하한은 1/(B+1).
            "p_value_method": "add_one_(b+1)/(B+1)",
            "p_value_floor": float(1.0 / (b_total + 1)),
            "reject_h0": bool(pval < float(alpha)),
            "h0": "All districts share the same underlying trip distribution for (transit,bike)->F1; district labels do not matter.",
            "h0_ko": (
                "서울 자치구마다 하나씩 요약된 F1(Depth·Coverage 조화)이 ‘구에 관계없이 같은 생성 과정’에서 나왔다고 가정한다. "
                "즉 구 라벨을 무작위로 바꿔도(가중 재표본·무작위 할당) 지금만큼의 구별 F1 분산은 자주 나온다."
            ),
            "h1_ko": "실제 구별 F1 분산은 위 귀무 과정보다 크다(구마다 다른 수준·분포).",
            "test_stat": "Var(F1_gu)",
            "note": note,
            "method_ko": method_ko,
        }

    if wts_len < min_pool:
        # Trip-level 풀이 부족할 때: 구별 F1 벡터에 대한 부트스트랩 귀무(교환가능·단일 모집단)로 Var 분포를 근사.
        n_gu = int(obs_f1.size)
        null_stats_fb: list[float] = []
        for _ in range(int(mc_sims)):
            idx = rng.integers(0, n_gu, size=n_gu, endpoint=False)
            null_stats_fb.append(float(np.var(obs_f1[idx], ddof=0)))
        null_arr = np.array(null_stats_fb, dtype=float)
        ge = int(np.sum(null_arr >= obs_var))
        pval = float((ge + 1) / (null_arr.size + 1))
        return _response_common(
            null_arr=null_arr,
            pval=pval,
            ge_count=ge,
            test_mode="bootstrap_f1_iid",
            note="Bootstrap variance under exchangeable district F1 (fallback when pooled TMAP-matched trips < min_pooled_observations).",
            method_ko=(
                "관측: district_savings.json과 동일한 정의로 구별 Depth·Coverage→F1을 구한 뒤 Var(F1)을 씁니다. "
                f"OD·TMAP OK로 맞춘 trip 풀 표본이 {wts_len}건으로, 경로 수준 귀무 시뮬레이션에 권장 최소({min_pool}건)에 못 미칩니다. "
                f"대신 구별 F1 {n_gu}개를 경험분포에서 복원추출해 Var(F1)을 반복 계산하는 부트스트랩({int(mc_sims)}회)으로 "
                "p를 add-one 추정량 (b+1)/(B+1)로 계산합니다(p 하한 1/(B+1), 0 불가). "
                "이 귀무는 ‘모든 구가 동일한 F1 분포에서 독립적으로 나왔다’에 가깝고, 경로 구조 차이는 반영하지 못하므로 탐색·보조 해석에 한정하세요."
            ),
            null_sample_n=n_gu,
        )

    w_sum = float(np.sum(w))
    if not np.isfinite(w_sum) or w_sum <= 0:
        return {
            "empty": True,
            "error": "trip 풀 가중치 합이 비정상입니다.",
            "coverage_thr_pct": int(coverage_thr_pct),
        }
    p = w / w_sum

    # district size proportions based on matched_weight_by_gu from savings payload
    gu_weights = {k: float(v) for k, v in matched_w_by_gu.items() if v is not None and float(v) > 0}
    gu_list = [r["gu"] for r in obs_rows if r["gu"] in gu_weights]
    if len(gu_list) < 8:
        return {"empty": True, "error": "구별 matched_weight 정보가 부족합니다.", "coverage_thr_pct": int(coverage_thr_pct)}
    gw = np.array([gu_weights[g] for g in gu_list], dtype=float)
    gw = gw / float(np.sum(gw))
    # MC 1회당 표본 크기 상한(응답 시간·프록시 타임아웃 방지) — 요청 sample_n은 이 값으로 캡
    eff_n = min(int(sample_n), 12000)
    eff_n = max(eff_n, 4000)
    # fixed counts per district for each simulation
    counts = np.floor(gw * float(eff_n)).astype(int)
    # adjust to sum exactly eff_n
    rem = int(eff_n - int(np.sum(counts)))
    if rem > 0:
        add_idx = rng.choice(len(counts), size=rem, replace=True, p=gw)
        for i in add_idx:
            counts[i] += 1

    def _stat_from_sample(idx: np.ndarray) -> float:
        # draw pooled observations then assign random labels by partitioning
        # shuffle indices then slice by counts to districts
        rng.shuffle(idx)
        start = 0
        f1s: list[float] = []
        for n in counts:
            part = idx[start : start + int(n)]
            start += int(n)
            if part.size < 2:
                continue
            # equal-weight within sample (already weight-proportional sampling)
            t_sum = float(np.sum(trans_a[part]))
            if t_sum <= 0:
                continue
            depth = float(np.sum(diffpos_a[part]) / t_sum * 100.0)
            cov = float(np.mean(hit_a[part]) * 100.0)
            f1s.append(_f1_from_depth_coverage(depth, cov))
        if len(f1s) < 3:
            return 0.0
        return float(np.var(np.array(f1s, dtype=float), ddof=0))

    null_stats: list[float] = []
    pool_n = int(w.shape[0])
    # 가중 복원추출을 매 반복마다 np.random.choice(p=...)로 하면 cumsum을 매번 다시 계산해
    # 10,000회에서 느립니다. cumsum(=cdf)을 한 번만 만들고 searchsorted로 표본을 뽑습니다.
    cdf = np.cumsum(p)
    cdf[-1] = 1.0  # 부동소수점 누적오차 방지(마지막 칸 보정)
    for _ in range(int(mc_sims)):
        u = rng.random(int(eff_n))
        idx = np.searchsorted(cdf, u, side="right")
        np.clip(idx, 0, pool_n - 1, out=idx)
        null_stats.append(_stat_from_sample(idx))

    null_arr = np.array(null_stats, dtype=float)
    ge = int(np.sum(null_arr >= obs_var))
    pval = float((ge + 1) / (null_arr.size + 1))

    return _response_common(
        null_arr=null_arr,
        pval=pval,
        ge_count=ge,
        test_mode="trip_label_randomization",
        note="Monte Carlo weighted-resampling + random partitioning approximation (빈도 가중을 위해).",
        method_ko=(
            "관측: district_savings.json과 동일한 정의로 구별 Depth·Coverage→F1을 구한 뒤 Var(F1)을 씁니다. "
            "귀무: OD·TMAP OK 경로를 풀에서 가중 복원추출한 뒤, 구별 matched 가중 비율로 표본을 구에 무작위 할당해 F1을 다시 계산합니다. "
            f"이 과정을 {int(mc_sims)}회 반복해 Var(F1)의 귀무 분포를 근사하고, p는 add-one(plus-one) 추정량 (b+1)/(B+1)로 계산합니다 "
            f"(b=귀무에서 ‘관측 Var 이상’이 나온 횟수, B={int(mc_sims)}=반복수). 유한 시뮬레이션으로 p=0을 입증할 수 없으므로 p의 하한은 1/(B+1)이며 절대 0이 되지 않습니다. "
            f"(응답 시간을 위해 MC 표본 크기는 요청값과 무관하게 최대 {eff_n}건으로 캡합니다.)"
        ),
        null_sample_n=int(eff_n),
    )


def _tmap_summarize_api_errors(summary: dict[str, Any]) -> int:
    return sum(int(r.get("api_error_rows") or 0) for r in (summary.get("rows") or []))


def _tmap_fill_worker(body: TmapDistrictFillBody) -> None:
    """백그라운드: fill_tmap_cache 를 반복 실행해 API_ERROR 를 없앨 때까지(또는 상한) 돌린다."""
    global _tmap_fill_last
    root = Path(__file__).resolve().parent.parent
    od_dir = Path(os.getenv("OD_DISTRICT_DIR") or _default_od_dir())
    tmap_dir = _tmap_by_district_dir()
    tmap_dir.mkdir(parents=True, exist_ok=True)
    batches: list[dict[str, Any]] = []
    prev_api: Optional[int] = None
    streak = 0
    ok = False
    err_note: Optional[str] = None
    try:
        for bi in range(int(body.max_batches)):
            cmd = [
                sys.executable,
                "-m",
                "src.fill_tmap_cache",
                "--input-dir",
                str(od_dir),
                "--per-district",
                "--output-dir",
                str(tmap_dir),
                "--workers",
                str(int(body.workers)),
                "--pair-workers",
                str(int(body.pair_workers)),
            ]
            if body.single_pass:
                cmd.append("--single-pass")

            proc = subprocess.run(cmd, cwd=str(root), env=os.environ.copy())
            summ = tmap_by_district_summary()
            api_err = _tmap_summarize_api_errors(summ)
            overall = summ.get("overall") or {}
            ratio = overall.get("completion_ratio")

            batches.append(
                {
                    "batch_index": bi + 1,
                    "returncode": int(proc.returncode),
                    "api_error_rows_sum": api_err,
                    "completion_ratio": ratio,
                    "ok_rows_sum": overall.get("ok_rows_sum"),
                    "expected_pairs_total_sum": overall.get("expected_pairs_total_sum"),
                }
            )
            append_jsonl(
                {
                    "kind": "tmap_district_fill_batch",
                    "batch": bi + 1,
                    "returncode": int(proc.returncode),
                    "api_error_rows_sum": api_err,
                    "completion_ratio": ratio,
                }
            )

            if int(proc.returncode) != 0:
                err_note = f"fill_tmap_cache 비정상 종료 exit={int(proc.returncode)}"
                break

            if api_err == 0:
                ok = True
                break

            if prev_api is not None and api_err >= prev_api:
                streak += 1
            else:
                streak = 0
            prev_api = api_err
            if streak >= 4:
                err_note = "API_ERROR 행 수가 줄지 않아 중단했습니다(동일·악화 반복)."
                break

            time.sleep(float(body.sleep_sec_between_batches))

        if not err_note and not ok and batches:
            err_note = f"max_batches={int(body.max_batches)} 에 도달했습니다."

        _tmap_fill_last = {
            "empty": False,
            "ok": ok,
            "batches": batches,
            "error": err_note,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        _tmap_fill_last = {
            "empty": False,
            "ok": False,
            "batches": batches,
            "error": str(e),
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        _tmap_fill_active.clear()


@app.post("/api/tmap-by-district/fill-until-complete")
def tmap_district_fill_until_complete(body: Optional[TmapDistrictFillBody] = None):
    """
    구별 TMAP 캐시를 채우는 `fill_tmap_cache` 를 백그라운드에서 반복 실행합니다.
    - 한 배치: `python -m src.fill_tmap_cache --input-dir … --per-district …`
    - 배치 후 요약을 읽어 `API_ERROR` 행 합이 0이 될 때까지(또는 진행 없음·상한) 반복합니다.
    - 이미 실행 중이면 409.
    """
    if _tmap_fill_active.is_set():
        raise HTTPException(409, "tmap fill already in progress")
    b = body or TmapDistrictFillBody()
    _tmap_fill_active.set()
    th = threading.Thread(target=_tmap_fill_worker, args=(b,), daemon=True)
    th.start()
    return {
        "ok": True,
        "started": True,
        "params": (b.model_dump() if hasattr(b, "model_dump") else b.dict()),
        "message": "백그라운드에서 구별 TMAP 캐시 채움을 시작했습니다. /api/tmap-by-district/fill-status 로 진행 확인.",
    }


@app.get("/api/tmap-by-district/fill-status")
def tmap_district_fill_status():
    return {"active": _tmap_fill_active.is_set(), "last": _tmap_fill_last}


@app.get("/api/tmap-by-district/list")
def list_tmap_by_district():
    d = _tmap_by_district_dir()
    if not d.exists():
        return {"dir": str(d), "files": []}
    files = sorted([p.name for p in d.glob("*_tmap_pairs.csv") if p.is_file()])
    gus = [f.replace("_tmap_pairs.csv", "") for f in files]
    return {"dir": str(d), "files": files, "gus": gus}


@app.get("/api/tmap-by-district/table")
def tmap_by_district_table(
    gu: str = Query(..., description="예: 강남구"),
    limit: int = Query(200, ge=10, le=2000),
    offset: int = Query(0, ge=0, le=5_000_000),
    status: str = Query("", description="필터: OK/API_ERROR/NO_PATH_OR_TOO_CLOSE 등 (빈 문자열이면 전체)"),
):
    d = _tmap_by_district_dir()
    fp = d / f"{gu}_tmap_pairs.csv"
    if not fp.exists():
        raise HTTPException(404, f"파일 없음: {fp.name}")

    rows = []
    total = 0
    matched_total = 0
    with open(fp, "r", encoding="utf-8", newline="") as f:
        r = _csv.DictReader(f)
        if not r.fieldnames:
            return {"gu": gu, "rows": [], "total": 0, "offset": offset, "limit": limit}
        for row in r:
            st = (row.get("transit_status") or "").strip()
            if status and st != status:
                continue
            if total >= offset and len(rows) < limit:
                rows.append(
                    {
                        "a_id": row.get("a_id"),
                        "b_id": row.get("b_id"),
                        "transit_status": st,
                        "transit_total_min_1dp": row.get("transit_total_min_1dp"),
                        "transit_riding_min_1dp": row.get("transit_riding_min_1dp"),
                        "transit_total_dist_m": row.get("transit_total_dist_m"),
                        "api_detail": row.get("api_detail"),
                        "written_at_utc": row.get("written_at_utc"),
                    }
                )
            total += 1
            if len(rows) >= limit and total >= offset + limit:
                # keep counting? no—fast path, return approximate total unknown
                pass
    return {"gu": gu, "rows": rows, "total": total, "offset": int(offset), "limit": int(limit), "status": status}


def _batch_refresh_top_pairs(n: int, force_refresh: bool) -> dict[str, Any]:
    trips = load_data()
    summary = build_pair_cache(
        trips,
        top_pairs_limit=n,
        force_refresh=force_refresh,
        return_summary=True,
        journal_source="web_api.batch_refresh",
    )
    out = {
        "ok": True,
        **(summary or {}),
        "usage": get_tmap_usage(),
    }
    append_jsonl(
        {
            "kind": "web_batch_refresh",
            "n": n,
            "force_refresh": force_refresh,
            "fetch_path_attempts": summary.get("fetch_path_attempts") if summary else None,
            "skipped_cached_ok": summary.get("skipped_cached_ok") if summary else None,
            "usage_count": (out.get("usage") or {}).get("count"),
        }
    )
    return out


@app.post("/api/batch/refresh-top-pairs")
def batch_refresh_top_pairs_post(body: BatchRefreshBody):
    """
    trips.csv에서 이용 빈도 상위 n개 출발·도착 쌍에 대해 transit_pairs 캐시를 갱신합니다.
    force_refresh=True면 이미 OK인 쌍도 다시 조회합니다(좌표 캐시도 우회).
    """
    return _batch_refresh_top_pairs(body.n, body.force_refresh)


@app.get("/api/batch/refresh-top-pairs")
def batch_refresh_top_pairs_get(
    n: int = Query(20, ge=1, le=400),
    force_refresh: bool = Query(False),
):
    """POST와 동일 동작. 조회 API처럼 쿼리만으로 호출 가능(프록시·구버전 대응)."""
    return _batch_refresh_top_pairs(n, force_refresh)


@app.get("/api/map/graph")
def map_graph(
    min_comparable: int = Query(
        3,
        ge=1,
        le=50_000,
        description="한 쌍(출발·도착)당 비교 가능 트립이 이 값 이상일 때만 선으로 표시",
    ),
    max_edges: int = Query(
        700,
        ge=10,
        le=5000,
        description="비교 건수 많은 순으로 최대 이 개수의 구간(선)만 반환",
    ),
):
    return compute_map_graph(min_comparable, max_edges)


@app.get("/api/charts/summary")
def charts_summary():
    return compute_charts_summary()


@app.get("/api/od-threshold/summary")
def od_threshold_summary(
    threshold_pct: float = Query(
        50.0,
        ge=0.0,
        le=100.0,
        description="이 비율(%)을 초과하면 해당 출발·도착 쌍은 따릉이 유리로 분류",
    ),
):
    return compute_od_threshold_winners(threshold_pct)


@app.get("/api/lookup", response_model=LookupResponse)
def lookup_pair(
    start_id: str = Query(..., description="출발 대여소 번호"),
    end_id: str = Query(..., description="도착 대여소 번호"),
    fetch_if_missing: bool = Query(True, description="캐시 없으면 TMAP 호출"),
    force_refresh: bool = Query(
        False, description="API_ERROR/ERROR 캐시도 다시 호출"
    ),
):
    s = norm_station_id(start_id)
    e = norm_station_id(end_id)
    if not s or not e:
        raise HTTPException(400, "대여소 번호를 확인하세요.")

    sr = _station_row(s)
    er = _station_row(e)
    if sr is None or er is None:
        raise HTTPException(404, "stations.xlsx 에 없는 대여소입니다.")

    slat, slon = sr["위도"], sr["경도"]
    elat, elon = er["위도"], er["경도"]
    if pd.isna(slat) or pd.isna(slon) or pd.isna(elat) or pd.isna(elon):
        raise HTTPException(400, "대여소 좌표가 없습니다.")

    trips = load_data()
    pair_mask = (trips["start_station_id"] == s) & (trips["end_station_id"] == e)
    trip_count = int(pair_mask.sum())
    bike_time_min = None
    if trip_count:
        bike_time_min = float(pd.to_numeric(trips.loc[pair_mask, "이용시간(분)"], errors="coerce").mean())

    df = _read_pair_cache()
    from_cache = False
    row = None
    if not df.empty:
        df["start_station_id"] = df["start_station_id"].map(norm_station_id)
        df["end_station_id"] = df["end_station_id"].map(norm_station_id)
        m = (df["start_station_id"] == s) & (df["end_station_id"] == e)
        if m.any():
            row = df.loc[m].iloc[0].to_dict()
            st = str(row.get("transit_status", ""))
            if force_refresh:
                row = None
            elif st in _RETRY_STATUSES and fetch_if_missing:
                row = None
            else:
                from_cache = True

    if row is None and fetch_if_missing:
        out = fetch_transit_time(float(slon), float(slat), float(elon), float(elat))
        out["start_station_id"] = s
        out["end_station_id"] = e
        out["start_lon"] = float(slon)
        out["start_lat"] = float(slat)
        out["end_lon"] = float(elon)
        out["end_lat"] = float(elat)
        out.setdefault("api_detail", "")
        _upsert_pair_cache_row(out)
        row = out
        from_cache = False
        append_jsonl(
            {
                "kind": "web_lookup_fetch",
                "start_station_id": s,
                "end_station_id": e,
                "transit_status": str(out.get("transit_status", "")),
            }
        )
        time.sleep(0.15)  # 과호출 완화
    elif row is None:
        return LookupResponse(
            start_station_id=s,
            end_station_id=e,
            from_cache=False,
            transit_status="NOT_CACHED",
            api_detail="캐시에 없고 fetch_if_missing=false",
            bike_time_min=bike_time_min,
            trip_count_for_pair=trip_count,
            start_lat=float(slat),
            start_lon=float(slon),
            end_lat=float(elat),
            end_lon=float(elon),
        )

    ttm = row.get("transit_total_min")
    trm = row.get("transit_riding_min")
    st = str(row.get("transit_status", ""))
    ad = row.get("api_detail")
    try:
        ttm_f = None if ttm is None or pd.isna(ttm) else float(ttm)
    except (TypeError, ValueError):
        ttm_f = None
    try:
        trm_f = None if trm is None or pd.isna(trm) else float(trm)
    except (TypeError, ValueError):
        trm_f = None

    bike_faster = None
    saved = None
    if bike_time_min is not None and ttm_f is not None and st == "OK":
        bike_faster = bike_time_min < ttm_f
        if bike_faster:
            saved = ttm_f - bike_time_min

    return LookupResponse(
        start_station_id=s,
        end_station_id=e,
        from_cache=from_cache,
        transit_total_min=ttm_f,
        transit_riding_min=trm_f,
        transit_status=st,
        api_detail=str(ad) if ad is not None and str(ad) != "nan" and str(ad) else None,
        bike_time_min=bike_time_min,
        bike_faster=bike_faster,
        bike_saved_min=round(saved, 2) if saved is not None else None,
        start_lat=float(slat),
        start_lon=float(slon),
        end_lat=float(elat),
        end_lon=float(elon),
        trip_count_for_pair=trip_count,
    )


# ───────────────────────────── 가설 2 · 수급 균형 (ANOVA) ─────────────────────────────

# 순유입 H0 기각 대여소 CSV → 대여소명 기반 3집단 라벨링 후 순유입량 ANOVA
_SUPPLY_NETFLOW_CACHE: dict[tuple[str, int], dict] = {}

# 교육시설/관공서 키워드 (대여소명에 포함되면 해당 집단)
_GOV_EDU_KEYWORDS: tuple[str, ...] = (
    "초등학교", "중학교", "고등학교", "대학교", "대학원", "캠퍼스", "학교", "유치원", "어린이집",
    "도서관", "구청", "시청", "군청", "주민센터", "행정복지센터", "동사무소", "파출소", "지구대",
    "경찰서", "소방서", "우체국", "보건소", "세무서", "법원", "검찰", "교육청", "정부청사",
    "구민회관", "복지관", "주민자치", "동주민",
)

# 지하철"역": '역' 뒤에 경계(숫자/번/출구/공백/괄호/끝/방향어 등)가 오는 경우만 매칭 (역삼동·역촌 등 오탐 방지)
_SUBWAY_RE = __import__("re").compile(r"역(\s|\)|\(|$|[0-9]|번|출구|입구|광장|사거리|삼거리|교차로|네거리|앞|뒤)")


def _classify_station_name(name: str) -> str:
    s = str(name or "")
    if _SUBWAY_RE.search(s):
        return "subway"
    if any(k in s for k in _GOV_EDU_KEYWORDS):
        return "gov_edu"
    return "other"


_SUPPLY_GROUP_LABELS: dict[str, str] = {
    "subway": "지하철역",
    "gov_edu": "교육·관공서",
    "other": "기타",
}


def _supply_netflow_csv_path() -> Path:
    env = os.getenv("SUPPLY_NETFLOW_CSV")
    if env:
        return Path(env)
    root = Path(__file__).resolve().parent.parent
    return root / "data" / "rejectH0_positive_netflow_station_names.csv"


@app.get("/api/supply-demand/anova")
def supply_demand_anova(
    dv: str = Query("일평균_순유입량", description="ANOVA 종속변수 컬럼명"),
):
    """가설 2 · 수급 균형.

    순유입 H0 기각(양(+)의 순유입) 대여소들을 대여소명으로 3집단
    (지하철역 / 교육·관공서 / 기타)으로 라벨링하고, 순유입량(기본 일평균_순유입량)에 대해
    일원배치 ANOVA를 수행한다. 등분산 검정(Levene)과 Tukey HSD 사후검정도 함께 반환.
    """
    fp = _supply_netflow_csv_path()
    if not fp.exists():
        raise HTTPException(404, f"netflow CSV not found: {fp}")

    key = (str(dv), int(fp.stat().st_mtime))
    cached = _SUPPLY_NETFLOW_CACHE.get(key)
    if cached is not None:
        return cached

    try:
        df = pd.read_csv(fp)
    except Exception as e:
        raise HTTPException(500, f"CSV 읽기 실패: {e}")

    if "대여소명" not in df.columns:
        raise HTTPException(400, "CSV에 '대여소명' 컬럼이 없습니다.")
    if dv not in df.columns:
        raise HTTPException(400, f"DV 컬럼 '{dv}' 가 CSV에 없습니다. 가능한 컬럼: {list(df.columns)}")

    df = df.copy()
    df["_grp"] = df["대여소명"].apply(_classify_station_name)
    df["_dv"] = pd.to_numeric(df[dv], errors="coerce")
    df = df.dropna(subset=["_dv"])

    order = ["subway", "gov_edu", "other"]
    arrays: dict[str, np.ndarray] = {
        g: df.loc[df["_grp"] == g, "_dv"].to_numpy(dtype=float) for g in order
    }

    groups_out: list[dict[str, Any]] = []
    for g in order:
        v = arrays[g]
        n = int(v.size)
        groups_out.append(
            {
                "key": g,
                "label": _SUPPLY_GROUP_LABELS[g],
                "n": n,
                "mean": float(np.mean(v)) if n else None,
                "std": float(np.std(v, ddof=1)) if n > 1 else None,
                "sem": float(np.std(v, ddof=1) / np.sqrt(n)) if n > 1 else None,
                "median": float(np.median(v)) if n else None,
                "min": float(np.min(v)) if n else None,
                "max": float(np.max(v)) if n else None,
            }
        )

    valid_arrays = [arrays[g] for g in order if arrays[g].size >= 2]
    if len(valid_arrays) < 2:
        raise HTTPException(400, "ANOVA를 위한 유효 집단(각 n≥2)이 부족합니다.")

    # one-way ANOVA
    f_stat, p_anova = scipy_stats.f_oneway(*valid_arrays)
    k = len(valid_arrays)
    n_total = int(sum(a.size for a in valid_arrays))
    df_between = k - 1
    df_within = n_total - k
    # eta squared = SS_between / SS_total
    grand = np.concatenate(valid_arrays)
    grand_mean = float(np.mean(grand))
    ss_between = float(sum(a.size * (np.mean(a) - grand_mean) ** 2 for a in valid_arrays))
    ss_total = float(np.sum((grand - grand_mean) ** 2))
    eta_sq = (ss_between / ss_total) if ss_total > 0 else None

    # Levene (등분산 가정 점검)
    try:
        lev_w, lev_p = scipy_stats.levene(*valid_arrays, center="median")
        levene = {"W": float(lev_w), "p": float(lev_p)}
    except Exception:
        levene = None

    # Welch's ANOVA (등분산 가정 불충족 시 권장)
    welch = None
    try:
        ns = np.array([a.size for a in valid_arrays], dtype=float)
        means = np.array([np.mean(a) for a in valid_arrays], dtype=float)
        vars = np.array([np.var(a, ddof=1) for a in valid_arrays], dtype=float)
        if np.all(vars > 0) and np.all(ns > 1):
            wts = ns / vars
            W = float(np.sum(wts))
            xbb = float(np.sum(wts * means) / W)
            A = float(np.sum(wts * (means - xbb) ** 2) / (k - 1))
            lam = float(np.sum((1.0 - wts / W) ** 2 / (ns - 1.0)))
            f_welch = A / (1.0 + (2.0 * (k - 2) / (k * k - 1.0)) * lam)
            df2 = (k * k - 1.0) / (3.0 * lam)
            p_welch = float(scipy_stats.f.sf(f_welch, k - 1, df2))
            welch = {
                "f_stat": float(f_welch),
                "p_value": p_welch,
                "df_between": int(k - 1),
                "df_within": float(df2),
                "reject_h0": bool(p_welch < 0.05),
            }
    except Exception:
        welch = None

    # Tukey HSD 사후검정
    tukey_rows: list[dict[str, Any]] = []
    try:
        used = [g for g in order if arrays[g].size >= 2]
        res = scipy_stats.tukey_hsd(*[arrays[g] for g in used])
        means = {g: float(np.mean(arrays[g])) for g in used}
        for i in range(len(used)):
            for j in range(i + 1, len(used)):
                gi, gj = used[i], used[j]
                ci = res.confidence_interval()
                tukey_rows.append(
                    {
                        "a": _SUPPLY_GROUP_LABELS[gi],
                        "b": _SUPPLY_GROUP_LABELS[gj],
                        "mean_diff": means[gi] - means[gj],
                        "p": float(res.pvalue[i, j]),
                        "ci_low": float(ci.low[i, j]),
                        "ci_high": float(ci.high[i, j]),
                        "reject": bool(res.pvalue[i, j] < 0.05),
                    }
                )
    except Exception:
        tukey_rows = []

    # 분류 검증용 전체 대여소 목록
    gu_col = "자치구" if "자치구" in df.columns else None
    stations = [
        {
            "name": str(r["대여소명"]),
            "gu": (str(r[gu_col]) if gu_col else None),
            "group": _SUPPLY_GROUP_LABELS[str(r["_grp"])],
            "group_key": str(r["_grp"]),
            "netflow": float(r["_dv"]),
        }
        for _, r in df.iterrows()
    ]

    payload = {
        "empty": False,
        "dv": str(dv),
        "n_total": int(len(df)),
        "groups": groups_out,
        "anova": {
            "f_stat": float(f_stat) if np.isfinite(f_stat) else None,
            "p_value": float(p_anova) if np.isfinite(p_anova) else None,
            "df_between": int(df_between),
            "df_within": int(df_within),
            "eta_sq": eta_sq,
            "alpha": 0.05,
            "reject_h0": bool(np.isfinite(p_anova) and p_anova < 0.05),
        },
        "levene": levene,
        "welch": welch,
        "tukey": tukey_rows,
        "classification": {
            "subway_rule": "대여소명에서 '역' 뒤에 숫자/번/출구/공백/방향어 등 경계가 오면 지하철역으로 분류",
            "gov_edu_keywords": list(_GOV_EDU_KEYWORDS),
            "note": "우선순위: 지하철역 → 교육·관공서 → 기타. (예: '역촌파출소'는 '역' 뒤 경계가 없어 관공서로 분류)",
        },
        "stations": stations,
    }
    _SUPPLY_NETFLOW_CACHE[key] = payload
    return payload


# ───────────────────────── 가설 2 · 수급 균형 (Capa·유동량 4단계 분석) ─────────────────────────
# 입력: data/supply/{station_capa.csv, station_daily_event_flow.csv, station_metrics.csv}
#   1) Capa ↔ 일평균 유동량 상관 (Pearson/Spearman)
#   2) 대여소별 평균 순유입량 0 검정 → +(과적)/−(고갈)/0(균형)
#   3) Capa 부족 검정 (ITDP 회전율 기준: 시간당 대여+반납 vs Capa/4, 단측 t)
#   4) (+/−/0) × (Capa부족 여부) = 6집단 분류

_SUPPLY_ANALYSIS_CACHE: dict[tuple[int, int, int], dict] = {}
_SUPPLY_CALENDAR_DAYS = 304  # 2025-01-01 ~ 2025-10-31
_SUPPLY_N_DAYS = 304

_NETFLOW_GROUP_ORDER = ["+_순유입/과적", "-_순유출/고갈", "0_균형(기각 안 됨)"]
_CAPA_SHORTAGE = "부족 O"
_CAPA_NOT_SHORTAGE = "부족 X"
_SIX_GROUP_ORDER: list[tuple[str, str]] = [
    ("+_순유입/과적", _CAPA_SHORTAGE),
    ("+_순유입/과적", _CAPA_NOT_SHORTAGE),
    ("-_순유출/고갈", _CAPA_SHORTAGE),
    ("-_순유출/고갈", _CAPA_NOT_SHORTAGE),
    ("0_균형(기각 안 됨)", _CAPA_SHORTAGE),
    ("0_균형(기각 안 됨)", _CAPA_NOT_SHORTAGE),
]
_SIX_GROUP_LABELS = {
    ("+_순유입/과적", _CAPA_SHORTAGE): "01_순유입_과적__부족O",
    ("+_순유입/과적", _CAPA_NOT_SHORTAGE): "02_순유입_과적__부족X",
    ("-_순유출/고갈", _CAPA_SHORTAGE): "03_순유출_고갈__부족O",
    ("-_순유출/고갈", _CAPA_NOT_SHORTAGE): "04_순유출_고갈__부족X",
    ("0_균형(기각 안 됨)", _CAPA_SHORTAGE): "05_균형__부족O",
    ("0_균형(기각 안 됨)", _CAPA_NOT_SHORTAGE): "06_균형__부족X",
}


def _supply_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "supply"


def _fisher_ci(r: float, n: int, z: float = 1.959963984540054) -> tuple[Optional[float], Optional[float]]:
    if n is None or n <= 3 or not np.isfinite(r) or abs(r) >= 1:
        return None, None
    zr = np.arctanh(r)
    se = 1.0 / sqrt(n - 3)
    return float(np.tanh(zr - z * se)), float(np.tanh(zr + z * se))


@app.get("/api/supply/analysis")
def supply_analysis():
    """가설 2 · 수급 균형 통합 분석(4단계). data/supply CSV에서 매번 재계산(파일 mtime 캐시)."""
    base = _supply_dir()
    capa_fp = base / "station_capa.csv"
    daily_fp = base / "station_daily_event_flow.csv"
    metrics_fp = base / "station_metrics.csv"
    for fp in (capa_fp, daily_fp, metrics_fp):
        if not fp.exists():
            raise HTTPException(404, f"supply CSV not found: {fp}")

    key = (
        int(capa_fp.stat().st_mtime),
        int(daily_fp.stat().st_mtime),
        int(metrics_fp.stat().st_mtime),
    )
    cached = _SUPPLY_ANALYSIS_CACHE.get(key)
    if cached is not None:
        return cached

    capa = pd.read_csv(capa_fp, encoding="utf-8-sig")
    daily = pd.read_csv(daily_fp, encoding="utf-8-sig")
    metrics = pd.read_csv(metrics_fp, encoding="utf-8-sig")

    capa["Capa"] = pd.to_numeric(capa["Capa"], errors="coerce")
    daily["daily_event_flow"] = pd.to_numeric(daily["daily_event_flow"], errors="coerce")

    # 대여소별 일평균 유동량 (이벤트 기반: |대여|+|반납| 일합, 304일 평균)
    daily_ok = daily.dropna(subset=["daily_event_flow"])
    station_flow = daily_ok.groupby("대여소_ID", as_index=False).agg(
        total_event_flow=("daily_event_flow", "sum"),
        active_days=("date", "nunique"),
    )
    station_flow["avg_daily_event_flow"] = station_flow["total_event_flow"] / _SUPPLY_CALENDAR_DAYS

    # ── 1) Capa ↔ 일평균 유동량 상관 ──
    merged = capa[["대여소_번호", "대여소_ID", "자치구", "Capa"]].merge(
        station_flow[["대여소_ID", "avg_daily_event_flow"]], on="대여소_ID", how="inner"
    )
    merged = merged.dropna(subset=["Capa", "avg_daily_event_flow"])
    merged = merged[(merged["Capa"] > 0) & (merged["avg_daily_event_flow"] >= 0)].copy()
    n_corr = int(len(merged))
    x = merged["Capa"].to_numpy(dtype=float)
    y = merged["avg_daily_event_flow"].to_numpy(dtype=float)
    pear = scipy_stats.pearsonr(x, y)
    spear = scipy_stats.spearmanr(x, y)
    p_r = float(pear[0]); p_p = float(pear[1])
    s_r = float(spear[0]); s_p = float(spear[1])
    p_lo, p_hi = _fisher_ci(p_r, n_corr)
    points = [
        {"capa": float(c), "flow": float(f), "gu": (str(g) if g == g else None)}
        for c, f, g in zip(merged["Capa"], merged["avg_daily_event_flow"], merged["자치구"])
    ]

    # ── 2) 순유입량 0 검정 (대여소별 단일표본 t, 양측) ──
    m = metrics.copy()
    m["일평균_순유입량"] = pd.to_numeric(m["일평균_순유입량"], errors="coerce")
    m["순유입량_표준편차"] = pd.to_numeric(m["순유입량_표준편차"], errors="coerce")
    m["Capa"] = pd.to_numeric(m["Capa"], errors="coerce")
    nf = m.dropna(subset=["일평균_순유입량", "순유입량_표준편차"]).copy()
    se = nf["순유입량_표준편차"].to_numpy(dtype=float) / sqrt(_SUPPLY_N_DAYS)
    mean = nf["일평균_순유입량"].to_numpy(dtype=float)
    df_t = _SUPPLY_N_DAYS - 1
    with np.errstate(divide="ignore", invalid="ignore"):
        t_stat = np.where(
            se == 0,
            np.where(mean > 0, np.inf, np.where(mean < 0, -np.inf, 0.0)),
            mean / se,
        )
    p_two = 2.0 * scipy_stats.t.sf(np.abs(t_stat), df_t)
    p_two = np.where(np.isinf(t_stat), 0.0, p_two)
    reject = p_two < 0.05
    grp = np.where(~reject, "0_균형(기각 안 됨)", np.where(mean > 0, "+_순유입/과적", np.where(mean < 0, "-_순유출/고갈", "0_균형(기각 안 됨)")))
    nf["net_flow_group"] = grp
    nf["_reject"] = reject

    nf_groups = []
    for g in _NETFLOW_GROUP_ORDER:
        sub = nf[nf["net_flow_group"] == g]
        if len(sub) == 0:
            continue
        nf_groups.append({
            "key": g,
            "count": int(len(sub)),
            "share": float(len(sub) / len(nf)),
            "mean_net_flow": float(sub["일평균_순유입량"].mean()),
            "median_net_flow": float(sub["일평균_순유입량"].median()),
            "mean_capa": float(sub["Capa"].mean()),
        })

    # ── 3) Capa 부족 검정 (시간당 유동량 vs Capa/4, 단측 t) ──
    hourly = daily_ok.copy()
    hourly["hourly"] = hourly["daily_event_flow"] / 24.0
    fs = hourly.groupby("대여소_ID", as_index=False).agg(
        total_hourly=("hourly", "sum"),
        sumsq_hourly=("hourly", lambda s: float((s * s).sum())),
    )
    cap = nf[["대여소_ID", "자치구", "Capa", "net_flow_group", "일평균_순유입량"]].merge(
        fs, on="대여소_ID", how="left"
    )
    cap[["total_hourly", "sumsq_hourly"]] = cap[["total_hourly", "sumsq_hourly"]].fillna(0.0)
    cap = cap.dropna(subset=["Capa"])
    cap = cap[cap["Capa"] > 0].copy()
    mean_hourly = cap["total_hourly"].to_numpy(dtype=float) / _SUPPLY_N_DAYS
    threshold = cap["Capa"].to_numpy(dtype=float) / 4.0
    daily_ratio = mean_hourly * 24.0 / cap["Capa"].to_numpy(dtype=float)
    numerator = cap["sumsq_hourly"].to_numpy(dtype=float) - _SUPPLY_N_DAYS * (mean_hourly ** 2)
    sample_std = np.sqrt(np.clip(numerator, 0, None) / (_SUPPLY_N_DAYS - 1))
    se3 = sample_std / sqrt(_SUPPLY_N_DAYS)
    with np.errstate(divide="ignore", invalid="ignore"):
        t3 = np.where(
            se3 == 0,
            np.where(mean_hourly > threshold, np.inf, np.where(mean_hourly < threshold, -np.inf, 0.0)),
            (mean_hourly - threshold) / se3,
        )
    p3 = scipy_stats.t.sf(t3, _SUPPLY_N_DAYS - 1)
    p3 = np.where(np.isposinf(t3), 0.0, np.where(np.isneginf(t3), 1.0, p3))
    capa_issue_reject = p3 < 0.05
    cap["mean_hourly"] = mean_hourly
    cap["daily_ratio"] = daily_ratio
    cap["_capa_reject"] = capa_issue_reject
    cap["capacity_issue"] = np.where(capa_issue_reject, _CAPA_SHORTAGE, _CAPA_NOT_SHORTAGE)

    cap_by_group = []
    for g in _NETFLOW_GROUP_ORDER:
        sub = cap[cap["net_flow_group"] == g]
        if len(sub) == 0:
            continue
        cnt = int(len(sub))
        issue = int(sub["_capa_reject"].sum())
        cap_by_group.append({
            "group": g,
            "count": cnt,
            "issue_count": issue,
            "issue_share": float(issue / cnt) if cnt else None,
            "mean_ratio": float(sub["daily_ratio"].mean()),
            "median_ratio": float(sub["daily_ratio"].median()),
            "mean_hourly_flow": float(sub["mean_hourly"].mean()),
            "mean_capa": float(sub["Capa"].mean()),
        })

    # ── 4) 6집단 분류 ──
    six = []
    for nfg, capg in _SIX_GROUP_ORDER:
        sub = cap[(cap["net_flow_group"] == nfg) & (cap["capacity_issue"] == capg)]
        six.append({
            "label": _SIX_GROUP_LABELS[(nfg, capg)],
            "net_flow_group": nfg,
            "capacity_group": capg,
            "count": int(len(sub)),
            "mean_capa": float(sub["Capa"].mean()) if len(sub) else None,
            "median_ratio": float(sub["daily_ratio"].median()) if len(sub) else None,
            "mean_net_flow": float(sub["일평균_순유입량"].mean()) if len(sub) else None,
        })

    payload = {
        "empty": False,
        "alpha": 0.05,
        "n_days": _SUPPLY_N_DAYS,
        "correlation": {
            "n": n_corr,
            "pearson_r": p_r,
            "pearson_p": p_p,
            "spearman_rho": s_r,
            "spearman_p": s_p,
            "pearson_ci95": [p_lo, p_hi],
            "calendar_days": _SUPPLY_CALENDAR_DAYS,
            "points": points,
        },
        "net_flow": {
            "stations_n": int(len(nf)),
            "groups": nf_groups,
        },
        "capacity": {
            "threshold_def": "E(시간당 대여+반납) > Capa/4 (ITDP 일 6회 회전율의 시간 환산)",
            "stations_n": int(len(cap)),
            "by_group": cap_by_group,
        },
        "six_group": {
            "total": int(len(cap)),
            "groups": six,
        },
    }
    _SUPPLY_ANALYSIS_CACHE[key] = payload
    return payload
