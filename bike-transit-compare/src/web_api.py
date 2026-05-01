"""
로컬 대시보드용 API.

전체 엑셀·캐시 일괄 처리는 배치로:  python -m src.run  (또는 scripts/run_batch.sh)

이 모듈(웹):
  터미널 1:  python -m uvicorn src.web_api:app --reload --host 127.0.0.1 --port 8000
  터미널 2:  cd frontend && npm install && npm run dev
  브라우저: http://localhost:5173  (Vite가 /api → 8000 프록시)
"""

from __future__ import annotations

import threading
import time
from typing import Any, List, Optional

import numpy as np
import pandas as pd
from math import asin, cos, radians, sin, sqrt
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .app_journal import append_jsonl
from .odsay_usage import get_odsay_usage
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
        description="ODsay info.totalDistance 기준, 이 거리(m) 이하인 출발·도착 쌍의 비율",
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
def odsay_usage():
    """KST 기준 오늘 누적: ODsay URL에 대한 `requests.get`이 응답을 받은 횟수(자정 리셋)."""
    return get_odsay_usage()


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
        "usage": get_odsay_usage(),
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
    fetch_if_missing: bool = Query(True, description="캐시 없으면 ODsay 호출"),
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
