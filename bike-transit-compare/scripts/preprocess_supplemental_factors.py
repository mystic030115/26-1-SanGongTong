#!/usr/bin/env python3
"""
보조 요인 3종 CSV 생성:
  - single_person_household_ratio_pct (가구통계)
  - employment_rate_pct (경제활동인구)
  - park_area_total_m2 (서울시 공원 통계)

사용 예:
  python scripts/preprocess_supplemental_factors.py \\
    --employment ~/Downloads/시군구_경제활동인구_총괄_20260522124529.csv \\
    --parks ~/Downloads/서울시\\ 공원\\ 통계.csv \\
    --household ~/Downloads/가구원수별+...
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "factors" / "supplemental"
RAW_ARCHIVE = ROOT / "data" / "factors" / "_raw_sources"
WIDE_CSV = ROOT / "data" / "factors" / "seoul_gu_features_combined_wide.csv"
META_JSON = ROOT / "data" / "factors" / "gu_factors_meta.json"

SEOUL_GU_25 = [
    "종로구", "중구", "용산구", "성동구", "광진구", "동대문구", "중랑구", "성북구", "강북구", "도봉구",
    "노원구", "은평구", "서대문구", "마포구", "양천구", "강서구", "구로구", "금천구", "영등포구", "동작구",
    "관악구", "서초구", "강남구", "송파구", "강동구",
]


def read_kr_csv(path: Path, **kwargs) -> pd.DataFrame:
    for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            return pd.read_csv(path, encoding=enc, **kwargs)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Cannot decode {path}")


def load_seoul_gu_list() -> list[str]:
    if WIDE_CSV.exists():
        df = pd.read_csv(WIDE_CSV)
        gu = df["gu"].astype(str).str.strip().tolist()
        if len(gu) >= 25:
            return gu
    return list(SEOUL_GU_25)


def parse_employment(path: Path) -> pd.DataFrame:
    raw = read_kr_csv(path, header=None)
    hdr = raw.iloc[1].astype(str).tolist()
    data = raw.iloc[2:].copy()
    data.columns = ["region", *hdr[1:]]
    seoul = data[data["region"].astype(str).str.startswith("서울 ")].copy()
    seoul["gu"] = seoul["region"].astype(str).str.replace("서울 ", "", regex=False)
    col = "고용률 (%)"
    if col not in seoul.columns:
        raise KeyError(f"Missing column {col!r} in employment file")
    seoul["value"] = (
        seoul[col]
        .astype(str)
        .str.replace(",", "", regex=False)
        .replace({"": None, "-": None, "*": None, "nan": None})
    )
    seoul["value"] = pd.to_numeric(seoul["value"], errors="coerce")
    out = seoul[["gu", "value"]].dropna()
    out["unit"] = "%"
    out["source"] = "KOSIS 시군구 경제활동인구(총괄)"
    out["year"] = "2025-Q2"
    return out


def parse_household(path: Path) -> pd.DataFrame:
    raw = read_kr_csv(path, header=None)
    data = raw.iloc[2:].copy()
    data.columns = [
        "lvl1", "lvl2", "total_hh", "hh1", "hh2", "hh3", "hh4", "hh5", "hh6", "hh7p", "avg_size",
    ]
    gu_rows = data[
        data["lvl1"].astype(str).str.contains("합계", na=False)
        & data["lvl2"].astype(str).str.endswith("구", na=False)
    ].copy()
    for c in ("total_hh", "hh1"):
        gu_rows[c] = pd.to_numeric(
            gu_rows[c].astype(str).str.replace(",", "", regex=False), errors="coerce"
        )
    gu_rows["gu"] = gu_rows["lvl2"].astype(str).str.strip()
    gu_rows["value"] = (gu_rows["hh1"] / gu_rows["total_hh"]) * 100.0
    out = gu_rows[["gu", "value"]].dropna()
    out = out[out["value"].between(0, 100)]
    out["unit"] = "%"
    out["source"] = "주민등록 가구원수별 가구통계(시군구 합계)"
    out["year"] = "2024"
    return out


def parse_parks(path: Path, area_scale_to_m2: float = 1000.0) -> pd.DataFrame:
    """면적: 원자료가 천㎡ 단위인 경우 area_scale_to_m2=1000으로 m² 환산."""
    df = read_kr_csv(path)
    df = df[df["기준년"].astype(str) != "yr"].copy()
    df["기준년"] = pd.to_numeric(df["기준년"], errors="coerce")
    df["면적"] = pd.to_numeric(df["면적"], errors="coerce")
    df["시군구"] = df["시군구"].astype(str).str.strip()
    df = df[df["시군구"].str.endswith("구", na=False)]
    latest = int(df["기준년"].max())
    sub = df[df["기준년"] == latest]
    agg = sub.groupby("시군구", as_index=False)["면적"].sum()
    agg.rename(columns={"시군구": "gu", "면적": "value"}, inplace=True)
    agg["value"] = agg["value"] * float(area_scale_to_m2)
    agg["unit"] = "m²"
    agg["source"] = "서울시 공원현황(구·공원종류별 면적 합)"
    agg["year"] = str(latest)
    agg.attrs["park_year"] = latest
    agg.attrs["area_scale"] = area_scale_to_m2
    return agg


def align_to_gu_list(df: pd.DataFrame, gu_list: list[str]) -> pd.DataFrame:
    m = {str(r.gu).strip(): float(r.value) for r in df.itertuples()}
    rows = []
    for gu in gu_list:
        v = m.get(gu)
        if v is None or not pd.notna(v):
            continue
        row = df[df["gu"] == gu].iloc[0].to_dict() if gu in m else {"gu": gu, "value": v}
        rows.append(
            {
                "gu": gu,
                "value": float(m[gu]),
                "unit": row.get("unit", ""),
                "source": row.get("source", ""),
                "year": row.get("year", ""),
            }
        )
    return pd.DataFrame(rows)


def write_slot(name: str, df: pd.DataFrame) -> Path:
    fp = OUT_DIR / f"{name}.csv"
    out = df[["gu", "value"]].copy()
    if "unit" in df.columns:
        # optional columns for traceability in wide merge — supplemental reader only needs gu,value
        pass
    out.to_csv(fp, index=False, encoding="utf-8")
    return fp


def update_meta(
    employment_year: str,
    household_year: str,
    park_year: str,
) -> None:
    meta: dict = {}
    if META_JSON.exists():
        meta = json.loads(META_JSON.read_text(encoding="utf-8"))
    fac = meta.setdefault("factors", {})

    fac["single_person_household_ratio_pct"] = {
        **fac.get("single_person_household_ratio_pct", {}),
        "category": "population",
        "label_ko": "1인 가구 비율(%)",
        "unit": "%",
        "source": "주민등록 가구원수별 가구통계(시군구)",
        "year": household_year,
        "definition_ko": "1인가구 수 ÷ 일반가구 수 × 100 (통계청·행안부 시군구 합계 행)",
        "notes": [
            "2024년 시군구 ‘합계’ 행 기준. 전국·서울 합계 행은 제외.",
            "‘1인 가구 비율’ 공표 정의와 세대원 등록 기준 차이가 있을 수 있음.",
        ],
    }
    fac["employment_rate_pct"] = {
        **fac.get("employment_rate_pct", {}),
        "category": "income",
        "label_ko": "고용률(%)",
        "unit": "%",
        "source": "KOSIS 시군구 경제활동인구(총괄)",
        "year": employment_year,
        "definition_ko": "15세 이상 경제활동인구 중 취업자 비율(%) — 원표 ‘고용률(%)’",
        "notes": [
            "2025년 2분기(표 상단 2025.2/2) 서울 시군구만 추출.",
            "실업자 RSE 등 일부 셀은 ‘-’, ‘*’로 비공개.",
        ],
    }
    fac["park_area_total_m2"] = {
        **fac.get("park_area_total_m2", {}),
        "category": "geo",
        "label_ko": "공원 면적(㎡, 합계)",
        "unit": "m²",
        "source": "서울시 공원현황 통계",
        "year": park_year,
        "definition_ko": f"{park_year}년 기준 구별 공원 면적 합(종류별 행 단순 합산, 천㎡→㎡×1000)",
        "notes": [
            "‘서울대공원’ 등 구 단위가 아닌 행정구역은 제외(25자치구만).",
            "종류별·상세유형별 행을 모두 합산해 구 간 경계 중복·겹침이 있을 수 있음(중복 컬럼 미차감).",
            "면적 단위는 원자료 천㎡ 가정 후 m²로 환산 — 공식 단위 확인 전 절대값 해석 주의.",
        ],
    }

    meta["supplemental_caveats"] = [
        "보조 요인 3종은 서로 다른 연도·출처(가구 2024, 고용 2025Q2, 공원 "
        + park_year
        + ")이며, F1·따릉이 집계 시점과 완전히 맞지 않을 수 있습니다.",
        "공원 면적은 구 내 공원 면적의 합산 proxy이며, 접근성·체감 녹지와 1:1 대응하지 않습니다.",
        "1인 가구 비율은 ‘일반가구’ 대비 1인가구 비중이며, 통계청 ‘1인 가구 비율’ 공표와 정의가 다를 수 있습니다.",
        "고용률은 15세 이상 기준 시군구 경제활동인구 조사 값입니다(주민등록 인구와 연령 구조가 다름).",
        "상관·산점도는 구 25개 단면(n=25)이므로 p-value·|r| 해석 시 다중비교·표본 크기를 고려하세요.",
    ]
    meta["supplemental_generated_by"] = "scripts/preprocess_supplemental_factors.py"
    META_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--employment", type=Path, required=True)
    ap.add_argument("--parks", type=Path, required=True)
    ap.add_argument("--household", type=Path, required=True)
    ap.add_argument("--park-area-scale", type=float, default=1000.0, help="천㎡→㎡ 배율 (기본 1000)")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_ARCHIVE.mkdir(parents=True, exist_ok=True)
    for src in (args.employment, args.parks, args.household):
        shutil.copy2(src, RAW_ARCHIVE / src.name)

    gu_list = load_seoul_gu_list()

    emp = align_to_gu_list(parse_employment(args.employment), gu_list)
    hh = align_to_gu_list(parse_household(args.household), gu_list)
    parks_raw = parse_parks(args.parks, area_scale_to_m2=args.park_area_scale)
    park_year = str(parks_raw.attrs.get("park_year", ""))
    parks = align_to_gu_list(parks_raw, gu_list)

    write_slot("employment_rate_pct", emp)
    write_slot("single_person_household_ratio_pct", hh)
    write_slot("park_area_total_m2", parks)

    update_meta(
        employment_year="2025-Q2",
        household_year="2024",
        park_year=park_year,
    )

    print(f"employment_rate_pct: {len(emp)} gu")
    print(f"single_person_household_ratio_pct: {len(hh)} gu")
    print(f"park_area_total_m2: {len(parks)} gu (year {park_year})")
    missing = [g for g in gu_list if g not in set(emp["gu"]) or g not in set(hh["gu"]) or g not in set(parks["gu"])]
    if missing:
        print("missing:", missing)


if __name__ == "__main__":
    main()
