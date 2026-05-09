import csv
import json
import math
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import requests


ROOT = Path(__file__).resolve().parents[1]
GEOJSON_PATH = ROOT / "frontend" / "public" / "seoul_gu_simple.geojson"
OUT_DIR = ROOT / "data" / "factors"
OUT_CSV = OUT_DIR / "gu_factors.csv"
OUT_META = OUT_DIR / "gu_factors_meta.json"
RAW_DIR = OUT_DIR / "_raw_files"


WIKI_URL = "https://ko.wikipedia.org/wiki/%EC%84%9C%EC%9A%B8%ED%8A%B9%EB%B3%84%EC%8B%9C%EC%9D%98_%ED%96%89%EC%A0%95_%EA%B5%AC%EC%97%AD"
SEOUL_CITYHALL = (37.5663, 126.9779)
FOREIGNERS_XLSX_URL = "https://www.immigration.go.kr/bbs/immigration/227/486618/download.do"
FOREIGNERS_XLSX_REF = "https://www.immigration.go.kr/bbs/immigration/227/600059/artclView.do"
FOREIGNERS_XLSX_BASIS = "2025-09-30"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _iter_coords(geom: Dict[str, Any]) -> Iterator[Tuple[float, float]]:
    # GeoJSON: Polygon: [ [ [lon,lat], ... ] ]
    # MultiPolygon: [ [ [ [lon,lat], ... ] ] ]
    t = geom.get("type")
    coords = geom.get("coordinates")
    if t == "Polygon":
        for ring in coords or []:
            for lon, lat in ring:
                yield float(lat), float(lon)
    elif t == "MultiPolygon":
        for poly in coords or []:
            for ring in poly or []:
                for lon, lat in ring:
                    yield float(lat), float(lon)


def centroid_latlon(geom: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    # Lightweight centroid approximation: mean of vertices.
    lats: list[float] = []
    lons: list[float] = []
    for lat, lon in _iter_coords(geom):
        lats.append(lat)
        lons.append(lon)
    if not lats:
        return None
    return (sum(lats) / len(lats), sum(lons) / len(lons))


@dataclass
class GuRow:
    gu: str
    population: Optional[float] = None
    area_km2: Optional[float] = None
    density_per_km2: Optional[float] = None
    centroid_lat: Optional[float] = None
    centroid_lon: Optional[float] = None
    dist_to_cityhall_km: Optional[float] = None


def load_geo_centroids() -> Dict[str, Tuple[float, float]]:
    gj = json.loads(GEOJSON_PATH.read_text(encoding="utf-8"))
    out: Dict[str, Tuple[float, float]] = {}
    for f in gj.get("features", []):
        name = (f.get("properties") or {}).get("name")
        geom = f.get("geometry")
        if not name or not geom:
            continue
        c = centroid_latlon(geom)
        if not c:
            continue
        out[str(name)] = c
    return out


def load_wiki_population_area() -> Dict[str, Tuple[float, float]]:
    # Wikipedia table contains commas in population.
    html = requests.get(
        WIKI_URL,
        timeout=40,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; SangongtongBot/0.1; +https://example.com)",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.6",
        },
    ).text

    class TableParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.in_table = False
            self.in_tr = False
            self.in_cell = False
            self.cell_tag = None
            self.cur_row: List[str] = []
            self.rows: List[List[str]] = []
            self.tables: List[List[List[str]]] = []

        def handle_starttag(self, tag, attrs):
            if tag == "table":
                self.in_table = True
                self.rows = []
            if self.in_table and tag == "tr":
                self.in_tr = True
                self.cur_row = []
            if self.in_tr and tag in ("th", "td"):
                self.in_cell = True
                self.cell_tag = tag
                self.cur_row.append("")

        def handle_endtag(self, tag):
            if tag == "table" and self.in_table:
                self.in_table = False
                if self.rows:
                    self.tables.append(self.rows)
            if tag == "tr" and self.in_tr:
                self.in_tr = False
                # Keep non-empty rows
                if any(c.strip() for c in self.cur_row):
                    self.rows.append([c.strip() for c in self.cur_row])
            if tag in ("th", "td") and self.in_cell:
                self.in_cell = False
                self.cell_tag = None

        def handle_data(self, data):
            if self.in_cell and self.cur_row:
                self.cur_row[-1] += data

    p = TableParser()
    p.feed(html)

    # Pick a table whose header contains 자치구 / 인구 / 면적
    target: Optional[List[List[str]]] = None
    for t in p.tables:
        if not t:
            continue
        header = t[0]
        joined = " ".join(header)
        if ("자치구" in joined) and ("인구" in joined) and ("면적" in joined):
            target = t
            break
    if target is None:
        raise RuntimeError("Wikipedia에서 자치구/인구/면적 표를 찾지 못했습니다.")

    header = target[0]
    # Find indices
    def _find_idx(keys: List[str]) -> int:
        for i, c in enumerate(header):
            for k in keys:
                if k in c:
                    return i
        return -1

    i_gu = _find_idx(["자치구"])
    i_pop = _find_idx(["인구"])
    i_area = _find_idx(["면적"])
    if min(i_gu, i_pop, i_area) < 0:
        raise RuntimeError(f"Wikipedia 표 헤더 파싱 실패: {header}")

    out: Dict[str, Tuple[float, float]] = {}
    for r in target[1:]:
        if len(r) <= max(i_gu, i_pop, i_area):
            continue
        gu = r[i_gu].strip()
        if not gu.endswith("구"):
            continue
        pop_raw = r[i_pop].replace(",", "").strip()
        area_raw = r[i_area].replace(",", "").strip()
        # Strip possible footnotes
        pop_raw = "".join(ch for ch in pop_raw if (ch.isdigit() or ch == "."))
        area_raw = "".join(ch for ch in area_raw if (ch.isdigit() or ch == "."))
        try:
            pop = float(pop_raw)
            area = float(area_raw)
        except ValueError:
            continue
        out[gu] = (pop, area)
    return out


def load_registered_foreigners_by_gu() -> Dict[str, float]:
    """
    등록외국인(지역·국적) 현황 엑셀에서 서울특별시 자치구별 총합계(총계, 총계)를 뽑아온다.
    """
    import pandas as pd

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest = RAW_DIR / f"registered_foreigners_region_nationality_{FOREIGNERS_XLSX_BASIS}.xlsx"
    if not dest.exists():
        r = requests.get(
            FOREIGNERS_XLSX_URL,
            timeout=60,
            headers={"User-Agent": "Mozilla/5.0", "Referer": FOREIGNERS_XLSX_REF},
        )
        r.raise_for_status()
        dest.write_bytes(r.content)

    df = pd.read_excel(dest, sheet_name=0, header=None)
    header_row = df.iloc[2].tolist()
    # Expected: [시도, 시군구 (그룹), 성별, 총합계, ...]
    col_sido = 0
    col_sigungu = 1
    col_gender = 2
    col_total = 3
    if str(header_row[col_sido]).strip() != "시도" or str(header_row[col_total]).strip() != "총합계":
        raise RuntimeError(f"등록외국인 엑셀 포맷이 예상과 다릅니다. headers[0..4]={header_row[:5]}")

    seoul = df[df[col_sido] == "서울특별시"]
    out: Dict[str, float] = {}
    for _, r in seoul.iterrows():
        gu = str(r[col_sigungu]).strip()
        gender = str(r[col_gender]).strip()
        if not gu.endswith("구"):
            continue
        if gender != "총계":
            continue
        v = r[col_total]
        try:
            out[gu] = float(v)
        except Exception:
            continue
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    centroids = load_geo_centroids()
    wiki = load_wiki_population_area()
    foreigners = load_registered_foreigners_by_gu()

    all_gu = sorted(set(centroids.keys()) | set(wiki.keys()))
    rows: list[GuRow] = []
    for gu in all_gu:
        pop, area = wiki.get(gu, (None, None))
        density = (pop / area) if (pop is not None and area and area > 0) else None
        c = centroids.get(gu)
        lat = c[0] if c else None
        lon = c[1] if c else None
        dist = (
            haversine_km(lat, lon, SEOUL_CITYHALL[0], SEOUL_CITYHALL[1]) if (lat is not None and lon is not None) else None
        )
        rows.append(
            GuRow(
                gu=gu,
                population=pop,
                area_km2=area,
                density_per_km2=density,
                centroid_lat=lat,
                centroid_lon=lon,
                dist_to_cityhall_km=dist,
            )
        )

    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["gu", "factor", "value", "unit", "source", "year"])
        for r in rows:
            if r.population is not None:
                w.writerow([r.gu, "population_total", f"{r.population:.0f}", "people", WIKI_URL, "2022-05"])
            if r.area_km2 is not None:
                w.writerow([r.gu, "area_km2", f"{r.area_km2:.2f}", "km2", WIKI_URL, "2022-05"])
            if r.density_per_km2 is not None:
                w.writerow([r.gu, "population_density_per_km2", f"{r.density_per_km2:.1f}", "people/km2", WIKI_URL, "2022-05"])
            if r.centroid_lat is not None and r.centroid_lon is not None:
                w.writerow([r.gu, "centroid_lat", f"{r.centroid_lat:.6f}", "deg", "frontend/public/seoul_gu_simple.geojson", "2013"])
                w.writerow([r.gu, "centroid_lon", f"{r.centroid_lon:.6f}", "deg", "frontend/public/seoul_gu_simple.geojson", "2013"])
            if r.dist_to_cityhall_km is not None:
                w.writerow([r.gu, "dist_to_cityhall_km", f"{r.dist_to_cityhall_km:.3f}", "km", "computed_from_centroid", "2013"])

            # registered foreigners (MOJ, 2025-09-30)
            fv = foreigners.get(r.gu)
            if fv is not None and fv >= 0:
                w.writerow([r.gu, "registered_foreigners_total", f"{fv:.0f}", "people", FOREIGNERS_XLSX_REF, FOREIGNERS_XLSX_BASIS])
                if r.population and r.population > 0:
                    w.writerow(
                        [
                            r.gu,
                            "registered_foreigners_ratio_pct",
                            f"{(fv / r.population * 100):.3f}",
                            "%",
                            "computed_from_registered_foreigners_total_and_population_total",
                            FOREIGNERS_XLSX_BASIS,
                        ]
                    )

    meta = {
        "generated_by": "scripts/build_gu_factors.py",
        "notes": [
            "소가설2(상관 분석)용 외부 요인 데이터의 1차 버전입니다.",
            "현재는 공개적으로 즉시 확보 가능한 인구/면적(위키) + 지리(구 centroid)부터 포함했습니다.",
            "소득/불평등/외국인비율/고령화비율 등은 공개 파일 다운로드 경로를 추가로 확정하면 같은 포맷으로 확장합니다.",
        ],
        "factors": {
            "population_total": {
                "category": "population",
                "why": "구별 수요 규모(잠재 이용자 수)를 나타내는 기본 변수입니다.",
                "source": WIKI_URL,
                "year": "2022-05",
            },
            "population_density_per_km2": {
                "category": "population",
                "why": "밀집도는 따릉이 이동의 경쟁력(단거리/혼잡/정차 비용)에 영향을 줄 수 있어 설명변수로 유효합니다.",
                "source": WIKI_URL,
                "year": "2022-05",
            },
            "dist_to_cityhall_km": {
                "category": "geo",
                "why": "도심 접근성(중심부와의 거리)은 통근/이동 패턴과 따릉이 절약효과 분포에 영향을 줄 수 있습니다.",
                "source": "computed_from_centroid",
                "year": "2013",
            },
            "registered_foreigners_ratio_pct": {
                "category": "population",
                "why": "외국인 거주 비율은 직주 패턴/고용 구조/생활권 특성 등과 연결될 수 있어, 절약효과(F1)가 구별로 '균일하지 않다'는 가설을 설명하는 인구·사회적 요인으로 사용합니다.",
                "source": {"registered_foreigners_total": FOREIGNERS_XLSX_REF, "population_total": WIKI_URL},
                "year": FOREIGNERS_XLSX_BASIS,
                "notes": [
                    "등록외국인=출입국관리법상 90일 초과 장기체류자 중 등록자(법무부 통계). 실제 체류/거주 외국인 전체와는 차이가 있을 수 있습니다.",
                ],
            },
        },
    }
    OUT_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote: {OUT_CSV}")
    print(f"Wrote: {OUT_META}")


if __name__ == "__main__":
    main()

