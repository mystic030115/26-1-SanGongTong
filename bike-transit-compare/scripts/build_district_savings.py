from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Tuple, List


def clean_header(s: str) -> str:
    return (s or "").replace("\ufeff", "").strip()


def pick_field(fieldnames: Iterable[str], want: str) -> str:
    m = {clean_header(f): f for f in fieldnames}
    k = clean_header(want)
    if k not in m:
        raise ValueError(f"missing column '{want}' in csv header: {list(fieldnames)[:20]}")
    return m[k]


def pair_key(a: str, b: str) -> Tuple[str, str]:
    return (a, b) if a < b else (b, a)


@dataclass
class DistrictAgg:
    gu: str
    total_weight: float = 0.0
    matched_weight: float = 0.0
    bike_min_total_all: float = 0.0
    bike_min_total_matched: float = 0.0
    transit_min_total_matched: float = 0.0
    saved_pos_min_total: float = 0.0
    net_saved_min_total: float = 0.0
    pairs_total: int = 0
    pairs_matched: int = 0
    # coverage histogram (0..100%) over matched trip-rows, weighted by 빈도
    coverage_hist_1pct: List[float] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.coverage_hist_1pct is None:
            self.coverage_hist_1pct = [0.0] * 101


def load_tmap_pairs(path: Path) -> Dict[Tuple[str, str], float]:
    """
    per-district cache file: a_id,b_id,...,transit_total_min_1dp,transit_status
    -> dict[(a,b)] = transit_total_min_1dp (float)
    """
    out: Dict[Tuple[str, str], float] = {}
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        if not r.fieldnames:
            return out
        ak = pick_field(r.fieldnames, "a_id")
        bk = pick_field(r.fieldnames, "b_id")
        tk = pick_field(r.fieldnames, "transit_total_min_1dp")
        sk = pick_field(r.fieldnames, "transit_status")
        for row in r:
            st = (row.get(sk) or "").strip()
            if st != "OK":
                continue
            a = (row.get(ak) or "").strip()
            b = (row.get(bk) or "").strip()
            if not a or not b or a == b:
                continue
            try:
                t = float((row.get(tk) or "").strip())
            except ValueError:
                continue
            out[pair_key(a, b)] = t
    return out


def parse_gu_name_from_filename(path: Path) -> str:
    # e.g. "강남구_시간_거리.csv" -> "강남구"
    stem = path.stem
    if stem.endswith("_시간_거리"):
        return stem[: -len("_시간_거리")]
    return stem


# (od_dir, tmap_dir, od_mtime, tmap_mtime) -> [(gu, source_csv, tmap_cache_csv, rows)]
# rows: list of (bike_min, w, transit_or_None, pair_key)  — 대여시간 보정 없이 원본만 파싱
_DISTRICT_ROWS_CACHE: Dict[tuple, list] = {}


def _max_mtime(dir_path: Path, pattern: str) -> int:
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


def load_district_rows(od_dir: Path, tmap_dir: Path) -> list:
    """OD + TMAP를 한 번만 파싱(캐시). borrow와 무관한 원본 행만 적재 → 재계산이 빠르다."""
    od_dir = Path(od_dir)
    tmap_dir = Path(tmap_dir)
    key = (
        str(od_dir),
        str(tmap_dir),
        _max_mtime(od_dir, "*.csv"),
        _max_mtime(tmap_dir, "*_tmap_pairs.csv"),
    )
    cached = _DISTRICT_ROWS_CACHE.get(key)
    if cached is not None:
        return cached

    od_files = sorted(p for p in od_dir.glob("*.csv") if p.is_file())
    if not od_files:
        raise SystemExit(f"no csv in {od_dir}")

    per_gu: list = []
    for fp in od_files:
        gu = parse_gu_name_from_filename(fp)
        cache_path = tmap_dir / f"{gu}_tmap_pairs.csv"
        tmap = load_tmap_pairs(cache_path)

        rows: list = []
        with open(fp, "r", encoding="utf-8", newline="") as f:
            r = csv.DictReader(f)
            if not r.fieldnames:
                continue
            sk = pick_field(r.fieldnames, "시작_대여소_ID")
            ek = pick_field(r.fieldnames, "종료_대여소_ID")
            bk = pick_field(r.fieldnames, "전체_이용_분")
            wk = pick_field(r.fieldnames, "빈도")

            for row in r:
                a = (row.get(sk) or "").strip()
                b = (row.get(ek) or "").strip()
                if not a or not b or a == b:
                    continue
                try:
                    bike_min = float((row.get(bk) or "").strip())
                    w = float((row.get(wk) or "").strip())
                except ValueError:
                    continue
                if w <= 0:
                    continue
                k = pair_key(a, b)
                rows.append((bike_min, w, tmap.get(k), k))

        per_gu.append((gu, fp.name, cache_path.name if cache_path.exists() else None, rows))

    _DISTRICT_ROWS_CACHE[key] = per_gu
    return per_gu


def build_payload(od_dir: Path, tmap_dir: Path, borrow_min: float = 0.0) -> dict:
    """구별 Depth/Coverage/절감 payload를 생성.

    borrow_min(따릉이 대여 소요시간, 분) > 0이면 따릉이 이용시간에 그만큼 더해
    (= bike_eff = bike_min + borrow_min) 모든 절감·침투·coverage를 재계산한다.
    borrow_min = 0이면 기존 결과와 동일하다.
    """
    od_dir = Path(od_dir)
    tmap_dir = Path(tmap_dir)
    A = float(borrow_min)

    per_gu = load_district_rows(od_dir, tmap_dir)

    results = []
    total = DistrictAgg(gu="전체")
    coverage_hist_by_gu: Dict[str, list] = {}
    matched_weight_by_gu: Dict[str, float] = {}

    def _ratio_bin_1pct(transit_min: float, bike_eff: float) -> int:
        if transit_min <= 0:
            return 0
        d = (transit_min - bike_eff) / transit_min * 100.0
        if d <= 0:
            return 0
        if d >= 100:
            return 100
        # floor to 1% bins (0..100)
        return int(d)

    for gu, source_csv, tmap_cache_csv, rows in per_gu:
        agg = DistrictAgg(gu=gu)
        pair_seen = set()
        pair_matched = set()

        for bike_min, w, t, k in rows:
            bike_eff = bike_min + A

            pair_seen.add(k)
            agg.total_weight += w
            agg.bike_min_total_all += bike_eff * w

            if t is None:
                continue

            pair_matched.add(k)
            agg.matched_weight += w
            agg.bike_min_total_matched += bike_eff * w
            agg.transit_min_total_matched += float(t) * w
            diff = float(t) - bike_eff
            if diff > 0:
                agg.saved_pos_min_total += diff * w
            agg.net_saved_min_total += diff * w
            bin_idx = _ratio_bin_1pct(float(t), bike_eff)
            agg.coverage_hist_1pct[bin_idx] += w

        agg.pairs_total = len(pair_seen)
        agg.pairs_matched = len(pair_matched)

        coverage_hist_by_gu[gu] = [round(x, 3) for x in agg.coverage_hist_1pct]
        matched_weight_by_gu[gu] = round(agg.matched_weight, 3)

        depth_pct = (
            (agg.saved_pos_min_total / agg.transit_min_total_matched * 100.0)
            if agg.transit_min_total_matched > 0
            else None
        )
        coverage_pct = (agg.matched_weight / agg.total_weight * 100.0) if agg.total_weight > 0 else None

        results.append(
            {
                "gu": agg.gu,
                "bike_min_total_all": round(agg.bike_min_total_all, 1),
                "bike_min_total_matched": round(agg.bike_min_total_matched, 1),
                "transit_min_total_matched": round(agg.transit_min_total_matched, 1),
                "depth_pct": round(depth_pct, 3) if depth_pct is not None else None,
                "saved_pos_min_total": round(agg.saved_pos_min_total, 1),
                "net_saved_min_total": round(agg.net_saved_min_total, 1),
                "coverage_trip_weight_pct": round(coverage_pct, 3) if coverage_pct is not None else None,
                "pairs_total": agg.pairs_total,
                "pairs_matched": agg.pairs_matched,
                "source_csv": source_csv,
                "tmap_cache_csv": tmap_cache_csv,
            }
        )

        # total (weighted)
        total.total_weight += agg.total_weight
        total.matched_weight += agg.matched_weight
        total.bike_min_total_all += agg.bike_min_total_all
        total.bike_min_total_matched += agg.bike_min_total_matched
        total.transit_min_total_matched += agg.transit_min_total_matched
        total.saved_pos_min_total += agg.saved_pos_min_total
        total.net_saved_min_total += agg.net_saved_min_total
        total.pairs_total += agg.pairs_total
        total.pairs_matched += agg.pairs_matched

    total_depth_pct = (
        (total.saved_pos_min_total / total.transit_min_total_matched * 100.0)
        if total.transit_min_total_matched > 0
        else None
    )
    total_coverage_pct = (total.matched_weight / total.total_weight * 100.0) if total.total_weight > 0 else None

    return {
        "meta": {
            "od_dir": str(od_dir),
            "tmap_dir": str(tmap_dir),
            "district_count": len(results),
            "borrow_min": round(A, 4),
            "note": "depth_pct = sum(max(transit-(bike+borrow),0)*w) / sum(transit*w) * 100 (matched-only, weighted by 빈도)",
            "coverage_note": "coverage at threshold t = sum_w(bin>=t) / sum_w(all matched), where bin is floor(max((transit-(bike+borrow))/transit*100,0))",
        },
        "total": {
            "gu": "전체",
            "bike_min_total_all": round(total.bike_min_total_all, 1),
            "bike_min_total_matched": round(total.bike_min_total_matched, 1),
            "transit_min_total_matched": round(total.transit_min_total_matched, 1),
            "depth_pct": round(total_depth_pct, 3) if total_depth_pct is not None else None,
            "saved_pos_min_total": round(total.saved_pos_min_total, 1),
            "net_saved_min_total": round(total.net_saved_min_total, 1),
            "coverage_trip_weight_pct": round(total_coverage_pct, 3) if total_coverage_pct is not None else None,
        },
        "districts": sorted(results, key=lambda x: x["gu"]),
        "coverage_hist_1pct_by_gu": coverage_hist_by_gu,
        "matched_weight_by_gu": matched_weight_by_gu,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--od-dir", required=True, help="관내이동_시간_거리 폴더 경로")
    ap.add_argument(
        "--tmap-dir",
        required=True,
        help="tmap_by_district 폴더 경로 (XX구_tmap_pairs.csv)",
    )
    ap.add_argument("--out", required=True, help="출력 JSON 경로 (프론트에서 import)")
    ap.add_argument("--borrow-min", type=float, default=0.0, help="따릉이 대여 소요시간(분) 보정값")
    args = ap.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payload = build_payload(Path(args.od_dir), Path(args.tmap_dir), float(args.borrow_min))

    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] wrote {out_path} (districts={payload['meta']['district_count']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

