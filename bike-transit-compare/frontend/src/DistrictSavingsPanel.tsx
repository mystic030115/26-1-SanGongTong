import { useCallback, useEffect, useMemo, useRef, useState, type FC } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { GeoJSON, MapContainer, TileLayer, useMap } from "react-leaflet";
import type { Feature, FeatureCollection, GeoJsonProperties, Geometry } from "geojson";
import "leaflet/dist/leaflet.css";
import L from "leaflet";
import {
  fetchF1HomogeneityTest,
  fetchFactorsAnalysis,
  fetchFactorsTable,
  type FactorsAnalysis,
  type FactorsCorrelationRow,
  type FactorsTable,
  type F1HomogeneityTest,
} from "./api";
import {
  COVERAGE_MEANINGFUL_AVG_PCT,
  COVERAGE_PATH_THRESHOLD_DEFAULT_PCT,
  DEPTH_MEANINGFUL_PCT,
  F1_MEANINGFUL,
  THRESHOLD_RATIONALE_BLOCKS,
} from "./hypothesis1Thresholds";
type DistrictRow = {
  gu: string;
  bike_min_total_all: number;
  bike_min_total_matched: number;
  transit_min_total_matched: number;
  depth_pct: number | null;
  saved_pos_min_total: number;
  net_saved_min_total: number;
  coverage_trip_weight_pct: number | null;
  pairs_total: number;
  pairs_matched: number;
  source_csv?: string;
  tmap_cache_csv?: string | null;
};

type SavingsPayload = {
  total: {
    depth_pct: number | null;
    saved_pos_min_total: number;
    net_saved_min_total: number;
    coverage_trip_weight_pct: number | null;
    bike_min_total_matched: number;
    transit_min_total_matched: number;
  };
  districts: DistrictRow[];
  coverage_hist_1pct_by_gu?: Record<string, number[]>;
  matched_weight_by_gu?: Record<string, number>;
};

type SeoulGuFeature = Feature<Geometry, GeoJsonProperties & { name?: string }>;
type SeoulGuGeoJSON = FeatureCollection<Geometry, GeoJsonProperties & { name?: string }>;

const SEOUL_CENTER: [number, number] = [37.565, 126.985];

/** 25구 GeoJSON 경계에 맞춰 지도를 채움(초기 zoom 플레이스홀더는 fitBounds가 덮어씀) */
const DistrictHeatmapFitBounds: FC<{ bounds: L.LatLngBounds }> = ({ bounds }) => {
  const map = useMap();
  useEffect(() => {
    if (!bounds?.isValid?.()) return;
    const t = window.setTimeout(() => {
      try {
        const c = (map as any)?._container;
        if (!c || !map) return;
        map.invalidateSize();
        map.fitBounds(bounds, { padding: [10, 10], maxZoom: 14, animate: false });
      } catch {
        /* 탭 전환·레이아웃 숨김 직후 등에서 Leaflet이 일시 실패할 수 있음 */
      }
    }, 0);
    return () => window.clearTimeout(t);
  }, [map, bounds]);
  return null;
};
const MAP_MASK_OUTER_RING: [number, number][] = [
  [-180, -90],
  [180, -90],
  [180, 90],
  [-180, 90],
  [-180, -90],
];

function fmtPct(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v.toFixed(1)}%`;
}

function fmtPvalue(p: number | null | undefined): string {
  if (p == null || !Number.isFinite(p)) return "—";
  if (p < 1e-4) return "<0.0001";
  return p.toFixed(4);
}

function clamp01(x: number): number {
  return Math.max(0, Math.min(1, x));
}

function minMax(values: Array<number | null | undefined>): { min: number; max: number } | null {
  let mn = Number.POSITIVE_INFINITY;
  let mx = Number.NEGATIVE_INFINITY;
  for (const v of values) {
    if (v == null || !Number.isFinite(v)) continue;
    mn = Math.min(mn, v);
    mx = Math.max(mx, v);
  }
  if (!Number.isFinite(mn) || !Number.isFinite(mx)) return null;
  return { min: mn, max: mx };
}

function normByRange(v: number, range: { min: number; max: number } | null): number {
  if (!range) return clamp01(v);
  const den = range.max - range.min;
  if (!Number.isFinite(den) || den <= 0) return 0.5;
  return clamp01((v - range.min) / den);
}

/** 소가설 1 지도: 값이 클수록 더 어둡게(rgb 동일, 흰↔검) */
function heatmapGrayscaleFill(
  mode: "depth" | "coverage" | "f1",
  depth: number | null | undefined,
  cov: number | null | undefined,
  f1: number | null | undefined,
  ranges: { depth: ReturnType<typeof minMax>; coverage: ReturnType<typeof minMax>; f1: ReturnType<typeof minMax> }
): string {
  let v: number | null = null;
  let range: { min: number; max: number } | null = null;
  if (mode === "depth") {
    v = depth ?? null;
    range = ranges.depth;
    if (v != null && Number.isFinite(v)) {
      const t =
        range && Number.isFinite(range.min) && Number.isFinite(range.max)
          ? normByRange(v, range)
          : clamp01((v + 60) / 80);
      const curved = Math.pow(clamp01(t), 0.5);
      const k = Math.round(255 * (1 - curved));
      return `rgb(${k},${k},${k})`;
    }
  } else if (mode === "coverage") {
    v = cov ?? null;
    range = ranges.coverage;
    if (v != null && Number.isFinite(v)) {
      const t =
        range && Number.isFinite(range.min) && Number.isFinite(range.max) ? normByRange(v, range) : clamp01(v / 100);
      const curved = Math.pow(clamp01(t), 0.5);
      const k = Math.round(255 * (1 - curved));
      return `rgb(${k},${k},${k})`;
    }
  } else {
    v = f1 ?? null;
    range = ranges.f1;
    if (v != null && Number.isFinite(v)) {
      const t =
        range && Number.isFinite(range.min) && Number.isFinite(range.max) ? normByRange(v, range) : clamp01(v);
      const curved = Math.pow(clamp01(t), 0.5);
      const k = Math.round(255 * (1 - curved));
      return `rgb(${k},${k},${k})`;
    }
  }
  return "rgb(255,255,255)";
}

function coverageAtThresholdFromHist(
  hist: number[] | undefined,
  matchedWeight: number | undefined,
  thresholdPct: number
): number | null {
  if (!hist || !Array.isArray(hist) || hist.length < 2) return null;
  const tot = Number(matchedWeight);
  if (!Number.isFinite(tot) || tot <= 0) return null;
  const t = Math.max(0, Math.min(100, Math.floor(thresholdPct)));
  let hit = 0;
  for (let i = t; i <= 100; i++) hit += Number(hist[i] || 0);
  return (hit / tot) * 100;
}

function f1FromDepthCoverage(depthPct: number, coveragePct: number): number {
  const d = clamp01(depthPct / 100);
  const c = clamp01(coveragePct / 100);
  const s = d + c;
  if (s <= 0) return 0;
  return (2 * d * c) / s;
}

function colorForDepthBarBlue(): string {
  return "rgba(61,156,240,0.85)";
}

function barFillByMeaningful(
  v: number | null | undefined,
  thr: number,
  base: string,
  opts?: { alphaLow?: number; alphaHigh?: number }
): string {
  const alphaLow = opts?.alphaLow ?? 0.22;
  const alphaHigh = opts?.alphaHigh ?? 0.88;
  if (v == null || !Number.isFinite(v)) return `rgba(140,160,180,${alphaLow})`;

  // base is rgba(r,g,b,a) or similar; we only control alpha by wrapping.
  // Use base color's RGB if it's in rgba format; fallback to base.
  const m = base.match(/rgba\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,/i);
  const a = v >= thr ? alphaHigh : alphaLow;
  if (m) return `rgba(${m[1]}, ${m[2]}, ${m[3]}, ${a})`;
  return base;
}

export default function DistrictSavingsPanel() {
  const districtHeatmapGeoRef = useRef<L.GeoJSON | null>(null);
  const [data, setData] = useState<SavingsPayload | null>(null);
  const [geo, setGeo] = useState<SeoulGuGeoJSON | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [rebuilding, setRebuilding] = useState(false);
  // 따릉이 대여 소요시간(분) — draft = 슬라이더 현재값, applied = '적용' 눌러 반영된 값
  const [borrowDraft, setBorrowDraft] = useState(0.5);
  const [borrowApplied, setBorrowApplied] = useState(0.5);
  const [applying, setApplying] = useState(false);
  const borrowAppliedRef = useRef(0.5);
  const [coverageThrPct, setCoverageThrPct] = useState(COVERAGE_PATH_THRESHOLD_DEFAULT_PCT);
  const [showCoverageLabels, setShowCoverageLabels] = useState(true);
  const [mapMode, setMapMode] = useState<"depth" | "coverage" | "f1">("depth");
  const [factorCategory, setFactorCategory] = useState<"population" | "income" | "geo">("population");
  const [factors, setFactors] = useState<FactorsAnalysis | null>(null);
  const [factorsErr, setFactorsErr] = useState<string | null>(null);
  const [factorsTable, setFactorsTable] = useState<FactorsTable | null>(null);
  const [factorsTableErr, setFactorsTableErr] = useState<string | null>(null);
  /** 소가설 2 차트 전용(소가설 1 F1 히스토그램과 상태 분리) */
  const [overlayFactorH2, setOverlayFactorH2] = useState<string>("");
  const [f1Test, setF1Test] = useState<F1HomogeneityTest | null>(null);
  const [f1TestErr, setF1TestErr] = useState<string | null>(null);

  const loadAll = useCallback((borrow: number) => {
    const bust = `v=${Date.now()}`;
    const payloadP =
      borrow > 1e-9
        ? fetch(`/api/district-savings/with-borrow?borrow_min=${borrow}`).then((r) => {
            if (!r.ok) throw new Error("대여시간 보정 payload 로드 실패");
            return r.json();
          })
        : fetch(`/district_savings.json?${bust}`, { cache: "no-store" }).then((r) => {
            if (!r.ok) throw new Error("district_savings.json 로드 실패");
            return r.json();
          });
    return Promise.all([
      payloadP,
      fetch(`/seoul_gu_simple.geojson?${bust}`, { cache: "no-store" }).then((r) => {
        if (!r.ok) throw new Error("seoul_gu_simple.geojson 로드 실패");
        return r.json();
      }),
    ]).then(([payload, gj]) => {
      setData(payload as SavingsPayload);
      setGeo(gj as SeoulGuGeoJSON);
      setErr(null);
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    loadAll(borrowAppliedRef.current).catch((e) => {
      if (!cancelled) setErr(String(e));
    });
    // auto-refresh (cache grows while batch runs) — 적용된 대여시간 기준 유지
    const t = window.setInterval(() => {
      loadAll(borrowAppliedRef.current).catch(() => void 0);
    }, 45_000);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, [loadAll]);

  const loadFactors = useCallback(
    async (borrow: number) => {
      try {
        const j = await fetchFactorsAnalysis(coverageThrPct, borrow);
        setFactors(j);
        setFactorsErr(j.error ? String(j.error) : null);
      } catch (e) {
        setFactorsErr(String(e));
      }
    },
    [coverageThrPct]
  );

  useEffect(() => {
    loadFactors(borrowApplied).catch(() => void 0);
    const t = window.setInterval(() => {
      loadFactors(borrowApplied).catch(() => void 0);
    }, 45_000);
    return () => window.clearInterval(t);
  }, [loadFactors, borrowApplied]);

  const applyBorrow = useCallback(() => {
    const b = borrowDraft;
    setApplying(true);
    setBorrowApplied(b);
    borrowAppliedRef.current = b;
    Promise.all([loadAll(b), loadFactors(b)])
      .catch((e) => setErr(String(e)))
      .finally(() => setApplying(false));
  }, [borrowDraft, loadAll, loadFactors]);

  const loadFactorsTable = useCallback(async () => {
    try {
      const j = await fetchFactorsTable();
      setFactorsTable(j);
      setFactorsTableErr(null);
    } catch (e) {
      setFactorsTableErr(String(e));
    }
  }, []);

  useEffect(() => {
    loadFactorsTable().catch(() => void 0);
    const t = window.setInterval(() => {
      loadFactorsTable().catch(() => void 0);
    }, 120_000);
    return () => window.clearInterval(t);
  }, [loadFactorsTable]);

  const loadF1Test = useCallback(async () => {
    setF1TestErr(null);
    try {
      const j = await fetchF1HomogeneityTest({
        coverageThrPct,
        mcSims: 10000,
        sampleN: 10000,
        alpha: 0.05,
        timeoutMs: 300_000,
      });
      setF1Test(j);
      setF1TestErr(null);
    } catch (e) {
      const msg = String(e);
      setF1TestErr(msg);
      setF1Test({
        empty: true,
        error: msg,
        coverage_thr_pct: coverageThrPct,
      });
    }
  }, [coverageThrPct]);

  useEffect(() => {
    loadF1Test().catch(() => void 0);
    // this test is heavier; refresh less often
    const t = window.setInterval(() => {
      loadF1Test().catch(() => void 0);
    }, 120_000);
    return () => window.clearInterval(t);
  }, [loadF1Test]);

  const onRebuildNow = useCallback(async () => {
    setRebuilding(true);
    try {
      const r = await fetch("/api/district-savings/rebuild", { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      await loadAll(borrowAppliedRef.current);
    } catch (e) {
      setErr(String(e));
    } finally {
      setRebuilding(false);
    }
  }, [loadAll]);

  const byGu = useMemo(() => {
    const m = new Map<string, DistrictRow>();
    for (const d of data?.districts ?? []) m.set(d.gu, d);
    return m;
  }, [data?.districts]);

  const histByGu = data?.coverage_hist_1pct_by_gu ?? {};
  const matchedWByGu = data?.matched_weight_by_gu ?? {};

  const coverageByGu = useMemo(() => {
    const m = new Map<string, number | null>();
    for (const d of data?.districts ?? []) {
      m.set(
        d.gu,
        coverageAtThresholdFromHist(histByGu[d.gu], matchedWByGu[d.gu], coverageThrPct)
      );
    }
    return m;
  }, [data?.districts, histByGu, matchedWByGu, coverageThrPct]);

  // labelByGu and chartData are now derived after F1 is computed (see below)

  const avg = data?.total?.depth_pct ?? null;
  const coverageAvg = useMemo(() => {
    // weighted by matched transit total already reflected in avg? Here we use simple avg across districts with data
    const vals = (data?.districts ?? [])
      .map((d) => coverageByGu.get(d.gu))
      .filter((v): v is number => v != null && Number.isFinite(v));
    if (!vals.length) return null;
    const s = vals.reduce((a, b) => a + b, 0);
    return s / vals.length;
  }, [coverageByGu, data?.districts]);

  /** Depth(%)·Coverage(%) → F1(0~1) (구별, 그리고 그 평균) */
  const f1Banner = useMemo(() => {
    type Row = { gu: string; depth_pct: number; coverage_pct: number; f1: number };
    const rows: Row[] = [];
    for (const d of data?.districts ?? []) {
      const dep = d.depth_pct;
      const cov = coverageByGu.get(d.gu);
      if (dep == null || !Number.isFinite(dep) || cov == null || !Number.isFinite(cov)) continue;
      rows.push({ gu: d.gu, depth_pct: dep, coverage_pct: cov, f1: f1FromDepthCoverage(dep, cov) });
    }
    if (!rows.length) {
      return {
        avgF1: null as number | null,
        rows: [] as Row[],
      };
    }
    const avgF1 = rows.reduce((a, r) => a + r.f1, 0) / rows.length;
    return {
      avgF1,
      rows,
    };
  }, [data?.districts, coverageByGu]);

  const f1ByGu = useMemo(() => {
    const m = new Map<string, number | null>();
    for (const r of f1Banner.rows ?? []) m.set(r.gu, r.f1);
    return m;
  }, [f1Banner.rows]);

  const f1Avg = useMemo(() => {
    return f1Banner.avgF1;
  }, [f1Banner.avgF1]);

  /**
   * Single source of truth for per-gu metrics used by:
   * - heatmap colors
   * - map labels/tooltips
   * - histograms/tooltips
   *
   * This avoids subtle mismatches (e.g. null -> 0 in some views).
   */
  const metricsByGu = useMemo(() => {
    const m = new Map<
      string,
      {
        depth_pct: number | null;
        coverage_pct: number | null;
        f1: number | null;
        pairs_matched: number;
      }
    >();
    for (const d of data?.districts ?? []) {
      const depth = d.depth_pct;
      const cov = coverageByGu.get(d.gu) ?? null;
      const f1 = f1ByGu.get(d.gu) ?? null;
      m.set(d.gu, {
        depth_pct: depth != null && Number.isFinite(depth) ? depth : null,
        coverage_pct: cov != null && Number.isFinite(cov) ? cov : null,
        f1: f1 != null && Number.isFinite(f1) ? f1 : null,
        pairs_matched: d.pairs_matched,
      });
    }
    return m;
  }, [coverageByGu, data?.districts, f1ByGu]);

  const labelByGu = useMemo(() => {
    const m = new Map<string, string>();
    for (const [gu, v] of metricsByGu.entries()) {
      if (mapMode === "coverage") {
        if (v.coverage_pct == null) continue;
        m.set(gu, `${v.coverage_pct.toFixed(1)}%`);
      } else if (mapMode === "depth") {
        if (v.depth_pct == null) continue;
        m.set(gu, `${v.depth_pct.toFixed(1)}%`);
      } else {
        if (v.f1 == null) continue;
        m.set(gu, v.f1.toFixed(3));
      }
    }
    return m;
  }, [metricsByGu, mapMode]);

  // 지도 hover 툴팁이 항상 최신값을 읽도록 ref로 보관 (onEachFeature는 최초 1회만 실행되므로)
  const metricsByGuRef = useRef(metricsByGu);
  metricsByGuRef.current = metricsByGu;
  const coverageThrPctRef = useRef(coverageThrPct);
  coverageThrPctRef.current = coverageThrPct;

  // 모드 전환·borrow 적용 시 지도 위 값 라벨(마커)을 다시 그린다.
  // (react-leaflet v4는 style만 setStyle로 갱신하고 onEachFeature는 재실행하지 않음)
  useEffect(() => {
    const gj = districtHeatmapGeoRef.current as unknown as L.GeoJSON | null;
    if (!gj) return;
    gj.eachLayer((layer: unknown) => {
      const lyr = layer as {
        feature?: { properties?: { name?: string } };
        __coverageMarker?: L.Marker;
        _map?: L.Map;
        getBounds?: () => L.LatLngBounds;
      };
      const name = lyr.feature?.properties?.name;
      if (lyr.__coverageMarker) {
        try {
          lyr.__coverageMarker.remove();
        } catch {
          /* ignore */
        }
        lyr.__coverageMarker = undefined;
      }
      const map = lyr._map;
      const txt = name ? labelByGu.get(name) : undefined;
      if (showCoverageLabels && map && txt) {
        const center = lyr.getBounds?.().getCenter?.();
        if (center) {
          const icon = L.divIcon({
            className: "coverage-label-icon",
            html: `<div class="coverage-label">${txt}</div>`,
            iconSize: undefined,
          });
          const mk = L.marker(center, { icon, interactive: false });
          mk.addTo(map);
          lyr.__coverageMarker = mk;
        }
      }
    });
  }, [labelByGu, showCoverageLabels, geo]);

  const chartDataWithF1 = useMemo(() => {
    const rows = (data?.districts ?? []).slice();
    // keep districts in consistent 가나다순 across the UI
    rows.sort((a, b) => String(a.gu ?? "").localeCompare(String(b.gu ?? ""), "ko"));
    return rows.map((d) => {
      const depthV = metricsByGu.get(d.gu)?.depth_pct ?? null;
      const covV = metricsByGu.get(d.gu)?.coverage_pct ?? null;
      const f1V = metricsByGu.get(d.gu)?.f1 ?? null;
      return {
        gu: d.gu,
        depth_pct_raw: d.depth_pct,
        depth_pct: depthV,
        /** Recharts 막대 높이용(null이면 막대가 비어 보이는 문제 방지) */
        depth_bar: depthV != null && Number.isFinite(depthV) ? depthV : 0,
        coverage: covV,
        coverage_bar: covV != null && Number.isFinite(covV) ? covV : 0,
        f1: f1V,
        f1_bar: f1V != null && Number.isFinite(f1V) ? f1V : 0,
        pairs_matched: d.pairs_matched,
      };
    });
  }, [data?.districts, metricsByGu]);

  // keep existing variable name used by charts below
  const chartData = chartDataWithF1;

  const factorMetaByName = useMemo(() => {
    const fm = (factorsTable?.meta?.factors ?? {}) as Record<string, any>;
    return fm;
  }, [factorsTable?.meta?.factors]);

  const factorOptionsByCategory = useMemo(() => {
    const rows = factorsTable?.rows ?? [];
    const present = new Set(rows.map((r) => r.factor));
    const byCat: Record<string, string[]> = { population: [], income: [], geo: [] };
    const inferFromName = (factor: string): "population" | "income" | "geo" => {
      const s = factor.toLowerCase();
      if (/employment|employ|고용/.test(factor) || /employment|employ/.test(s)) {
        return "income";
      }
      if (/park|공원/.test(factor) || /park/.test(s)) {
        return "geo";
      }
      if (/household|single_person|1인|가구/.test(factor) || /household|single_person/.test(s)) {
        return "population";
      }
      if (/income|소득|krw|wage|salary|earn|proxy_krw|월액|편차/.test(factor) || /income|krw|salary|wage/.test(s)) {
        return "income";
      }
      if (
        /dist|distance|km|center|geo|mountain|forest|산|임야|위치|거리|cityhall|centroid|hall/.test(factor) ||
        /dist|km|mountain|forest|geo|center|hall/.test(s)
      ) {
        return "geo";
      }
      return "population";
    };
    for (const f of present) {
      const raw = String(factorMetaByName?.[f]?.category ?? "").toLowerCase();
      const k =
        raw === "population" || raw === "income" || raw === "geo" ? raw : inferFromName(f);
      byCat[k].push(f);
    }
    for (const k of Object.keys(byCat)) byCat[k].sort();
    return byCat as Record<"population" | "income" | "geo", string[]>;
  }, [factorMetaByName, factorsTable?.rows]);

  // 소가설 2: 카테고리별 기본 요인만 설정(히스토그램/소가설 1과 독립)
  useEffect(() => {
    const opts = factorOptionsByCategory[factorCategory] ?? [];
    if (!opts.length) return;
    if (overlayFactorH2 && opts.includes(overlayFactorH2)) return;
    const best =
      (factors?.corr_rows ?? [])
        .filter((r) => r.category === factorCategory && r.target === "f1")
        .slice()
        .sort((a, b) => Math.abs(b.pearson_r ?? 0) - Math.abs(a.pearson_r ?? 0))[0]?.factor ?? opts[0];
    setOverlayFactorH2(best);
  }, [factorCategory, factorOptionsByCategory, factors?.corr_rows, overlayFactorH2]);

  const overlayFactorByGuH2 = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of factorsTable?.rows ?? []) {
      if (!overlayFactorH2 || r.factor !== overlayFactorH2) continue;
      const v = Number(r.value);
      if (!Number.isFinite(v)) continue;
      m.set(r.gu, v);
    }
    return m;
  }, [factorsTable?.rows, overlayFactorH2]);

  /** 소가설 1: 구별 F1 막대만(0~0.5 표시 · 요인·빨간 선 없음) */
  const chartDataForF1BarsHypo1 = useMemo(() => {
    return chartData.map((r) => {
      const f1v = r.f1;
      const f1Clamped = f1v != null && Number.isFinite(f1v) ? Math.min(0.5, f1v) : 0;
      return { ...r, f1_bar_clamped: f1Clamped };
    });
  }, [chartData]);

  /** 소가설 2: 구별 F1 막대 + 선택 요인 빨간 선 */
  const chartDataForF2 = useMemo(() => {
    const base = chartDataForF1BarsHypo1.map((r) => ({
      ...r,
      overlay_raw: overlayFactorH2 ? (overlayFactorByGuH2.get(r.gu) ?? null) : null,
      overlay_scaled: null as number | null,
    }));
    const vals = base.map((r) => r.overlay_raw).filter((v): v is number => v != null && Number.isFinite(v));
    if (!overlayFactorH2 || !vals.length) return base;
    const mn = Math.min(...vals);
    const mx = Math.max(...vals);
    const den = mx - mn;
    for (const r of base) {
      const v = r.overlay_raw;
      if (v == null || !Number.isFinite(v)) continue;
      const t = den > 0 ? (v - mn) / den : 0.5;
      r.overlay_scaled = Math.max(0, Math.min(0.5, t * 0.5));
    }
    return base;
  }, [chartDataForF1BarsHypo1, overlayFactorH2, overlayFactorByGuH2]);

  const overlayF1CorrH2 = useMemo(() => {
    const row = (factors?.corr_rows ?? []).find(
      (x: FactorsCorrelationRow) => x.target === "f1" && x.factor === overlayFactorH2
    );
    return { pearson_r: row?.pearson_r ?? null, n: row?.n ?? null };
  }, [factors?.corr_rows, overlayFactorH2]);

  /** 소가설 2: F1(0~1) 구간별 구 개수 히스토그램 — 요인 빨간 선 없음(막대·기준선만) */
  const f1HistChartData = useMemo(() => {
    const binCount = 10;
    const lo = 0;
    const hi = 1;
    const w = (hi - lo) / binCount;
    type Bin = {
      binLabel: string;
      binLo: number;
      binHi: number;
      binMid: number;
      count: number;
    };
    const bins: Bin[] = Array.from({ length: binCount }, (_, i) => {
      const a = lo + i * w;
      const b = i === binCount - 1 ? hi : lo + (i + 1) * w;
      return {
        binLabel: `${a.toFixed(1)}–${b.toFixed(1)}`,
        binLo: a,
        binHi: b,
        binMid: (a + b) / 2,
        count: 0,
      };
    });
    for (const row of chartData) {
      const f1 = row.f1;
      if (f1 == null || !Number.isFinite(f1)) continue;
      let idx = Math.floor((f1 - lo) / w);
      if (f1 >= hi) idx = binCount - 1;
      if (f1 < lo) idx = 0;
      idx = Math.max(0, Math.min(binCount - 1, idx));
      bins[idx].count += 1;
    }
    return bins;
  }, [chartData]);

  const factorLabelKr = useCallback(
    (f: string): string => {
      const m = factorMetaByName?.[f];
      if (m?.label_ko) return String(m.label_ko);
      // sensible defaults for existing factors
      if (f === "registered_foreigners_ratio_pct") return "외국인 거주비율(등록외국인, %)";
      if (f === "registered_foreigners_total") return "등록외국인 수(명)";
      if (f === "population_total") return "인구(명)";
      if (f === "population_density_per_km2") return "인구밀도(명/㎢)";
      if (f === "dist_to_cityhall_km") return "도심거리(시청 기준, km)";
      if (f === "area_km2") return "면적(km²)";
      // combined-wide (seoul_gu_features_combined_wide.csv)
      if (f === "average_monthly_income_krw") return "평균소득(월, 원)";
      if (f === "foreigner_resident_ratio_pct") return "외국인 거주비율(%, 등록외국인/인구)";
      if (f === "population_density_persons_per_km2") return "인구밀도(명/㎢)";
      if (f === "distance_from_seoul_center_km") return "위치/중심거리(km)";
      if (f === "mountain_forest_proxy_ratio_pct") return "산/임야 비율 proxy(%)";
      if (f === "elderly_65plus_ratio_pct") return "고령화 비율(65세+, %)";
      if (f === "income_std_proxy_krw_per_month") return "소득 표준편차 proxy(원/월)";
      if (f === "single_person_household_ratio_pct") return "1인 가구 비율(%)";
      if (f === "employment_rate_pct") return "고용률(%)";
      if (f === "park_area_total_m2") return "공원 면적(㎡, 합계)";
      return f;
    },
    [factorMetaByName]
  );

  // 전체 요인 상관분석 표 (F1·요인 산점도 페이지와 동일 정의) — borrow 적용된 factors.corr_rows 사용
  const correlationTableRows = useMemo(() => {
    const seen = new Set<string>();
    return (factors?.corr_rows ?? [])
      .filter((r: FactorsCorrelationRow) => r.target === "f1")
      .filter((r) => {
        const fac = String(r.factor ?? "").trim();
        if (!fac || seen.has(fac)) return false;
        seen.add(fac);
        return r.pearson_r != null && Number.isFinite(Number(r.pearson_r));
      })
      .map((r) => {
        const pPear = r.pearson_p ?? null;
        const pSpear = r.spearman_p ?? null;
        const sigPearson = pPear != null && Number.isFinite(pPear) && pPear < 0.05;
        const sigSpearman = pSpear != null && Number.isFinite(pSpear) && pSpear < 0.05;
        return {
          factor: r.factor,
          label: factorLabelKr(r.factor),
          n: r.n,
          pearson_r: r.pearson_r,
          pearson_p: pPear,
          spearman_r: r.spearman_r ?? null,
          spearman_p: pSpear,
          sigPearson,
          sigSpearman,
        };
      })
      .sort((a, b) => Math.abs(Number(b.pearson_r ?? 0)) - Math.abs(Number(a.pearson_r ?? 0)));
  }, [factors?.corr_rows, factorLabelKr]);

  // Map color ranges (per-mode). Using min/max makes small differences stand out.
  const mapRanges = useMemo(() => {
    return {
      depth: minMax(chartData.map((r) => r.depth_pct_raw)),
      coverage: minMax(chartData.map((r) => r.coverage)),
      f1: minMax(chartData.map((r) => r.f1)),
    };
  }, [chartData]);

  const seoulMaxBounds = useMemo(() => {
    if (!geo?.features?.length) {
      return [
        [37.42, 126.76],
        [37.71, 127.22],
      ] as [[number, number], [number, number]];
    }
    try {
      const layer = L.geoJSON(geo as any);
      const b = layer.getBounds();
      if (b.isValid()) {
        const p = 0.02;
        const sw = b.getSouthWest();
        const ne = b.getNorthEast();
        const latPad = (ne.lat - sw.lat) * p;
        const lngPad = (ne.lng - sw.lng) * p;
        return [
          [sw.lat - latPad, sw.lng - lngPad],
          [ne.lat + latPad, ne.lng + lngPad],
        ] as [[number, number], [number, number]];
      }
    } catch {
      // fall through
    }
    return [
      [37.42, 126.76],
      [37.71, 127.22],
    ] as [[number, number], [number, number]];
  }, [geo]);

  const seoulDistrictFitBounds = useMemo((): L.LatLngBounds | null => {
    if (!geo?.features?.length) return null;
    try {
      const layer = L.geoJSON(geo as any);
      const b = layer.getBounds();
      return b.isValid() ? b : null;
    } catch {
      return null;
    }
  }, [geo]);

  const seoulMaskGeoJson = useMemo(() => {
    if (!geo?.features?.length) return null;
    const holes: [number, number][][] = [];

    const pushHole = (ring: any) => {
      if (!Array.isArray(ring) || ring.length < 4) return;
      holes.push(ring as [number, number][]);
    };

    for (const f of geo.features as any[]) {
      const g = f?.geometry;
      if (!g) continue;
      if (g.type === "Polygon") {
        // coordinates: [ring0, ring1...]
        const rings = g.coordinates;
        if (Array.isArray(rings) && rings.length) pushHole(rings[0]);
      } else if (g.type === "MultiPolygon") {
        const polys = g.coordinates;
        if (!Array.isArray(polys)) continue;
        for (const poly of polys) {
          if (Array.isArray(poly) && poly.length) pushHole(poly[0]);
        }
      }
    }

    return {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          properties: { kind: "seoul-mask" },
          geometry: {
            type: "Polygon",
            // first ring is outer, remaining are holes
            coordinates: [MAP_MASK_OUTER_RING, ...holes],
          },
        },
      ],
    } as any;
  }, [geo]);

  const f1ThrBinLabel = useMemo(() => {
    const thr = F1_MEANINGFUL;
    for (const r of f1HistChartData) {
      const last = r.binHi >= 1 - 1e-9;
      const inBin = thr >= r.binLo && (thr < r.binHi || (last && thr <= r.binHi));
      if (inBin) return r.binLabel;
    }
    return null;
  }, [f1HistChartData]);

  const overallDepthOk = avg != null && Number.isFinite(avg) ? avg >= DEPTH_MEANINGFUL_PCT : null;
  const overallCoverageOk =
    coverageAvg != null && Number.isFinite(coverageAvg) ? coverageAvg >= COVERAGE_MEANINGFUL_AVG_PCT : null;
  const overallF1Ok = f1Avg != null && Number.isFinite(f1Avg) ? f1Avg >= F1_MEANINGFUL : null;

  /** 소가설 2: |r|이 이 값 이상이면 ‘선형 연관이 있다’고 판정 */
  const hypo2AbsRThreshold = 0.2;
  const hypo2MinN = 15;

  const overallHypo1Summary = useMemo(() => {
    const d = avg != null && Number.isFinite(avg) ? avg : null;
    const c = coverageAvg != null && Number.isFinite(coverageAvg) ? coverageAvg : null;
    const f = f1Avg != null && Number.isFinite(f1Avg) ? f1Avg : null;
    const dOk = overallDepthOk;
    const cOk = overallCoverageOk;
    const fOk = overallF1Ok;
    const passN = [dOk, cOk, fOk].filter((x) => x === true).length;
    const knownN = [dOk, cOk, fOk].filter((x) => x !== null).length;
    let verdict: string;
    if (knownN === 0) verdict = "판정 불가 (지표 산출 불가)";
    else if (passN === knownN && knownN === 3) verdict = "가설 1 충족 (Depth·Coverage·F1 모두 기준 이상)";
    else if (passN === 0) verdict = "가설 1 미충족 (세 지표 모두 기준 미달)";
    else verdict = `가설 1 부분 충족 (${passN}/${knownN} 지표만 기준 이상)`;

    const depthExpl =
      d == null || !Number.isFinite(d)
        ? `Depth는 전역 평균 절약률(%)로, 고빈도 경로에 가중을 둔 값입니다. 아직 산출할 수 없어 이 항목만으로는 판단할 수 없습니다. 목표는 ${DEPTH_MEANINGFUL_PCT}% 이상입니다.`
        : `Depth는 전역 평균 절약률입니다. 지금 값은 ${d.toFixed(1)}%이고, 가설 1에서는 최소 ${DEPTH_MEANINGFUL_PCT}% 이상을 통과선으로 둡니다. ${
            dOk ? "통과선을 넘었습니다." : "통과선에 미달합니다."
          }`;

    const coverageExpl =
      c == null || !Number.isFinite(c)
        ? `Coverage는 각 구에서 ‘절약률이 ${coverageThrPct}% 이상인 경로’ 비중을 구한 뒤, 25구의 산술평균입니다. 평균을 구할 수 없어 판정이 어렵습니다. 목표는 구 평균 ${COVERAGE_MEANINGFUL_AVG_PCT}% 이상입니다.`
        : `Coverage는 위와 같은 정의의 구 평균입니다. 지금 구 평균은 ${c.toFixed(1)}%이고, 가설 1에서는 ${COVERAGE_MEANINGFUL_AVG_PCT}% 이상을 요구합니다. ${
            cOk ? "요구 수준을 만족합니다." : "요구 수준에 이르지 못했습니다."
          }`;

    const f1Expl =
      f == null || !Number.isFinite(f)
        ? `F1은 구별 Depth와 Coverage를 0~1로 맞춘 뒤 조화평균으로 만든 값의 25구 평균입니다. 값이 없어 요약할 수 없습니다. 목표는 평균 ${F1_MEANINGFUL.toFixed(2)} 이상입니다.`
        : `F1은 절약 규모(Depth)와 범위(Coverage)가 동시에 큰지를 한 숫자로 보여 줍니다. 지금 25구 평균은 ${f.toFixed(3)}이고, 가설 1에서는 ${F1_MEANINGFUL.toFixed(2)} 이상을 목표로 둡니다. ${
            fOk ? "목표를 달성한 상태입니다." : "목표보다 낮습니다."
          }`;

    return {
      verdict,
      depthExpl,
      coverageExpl,
      f1Expl,
    };
  }, [
    avg,
    coverageAvg,
    f1Avg,
    overallCoverageOk,
    overallDepthOk,
    overallF1Ok,
    coverageThrPct,
  ]);

  const hypo2Verdict = useMemo(() => {
    if (!overlayFactorH2) {
      return {
        label: "—",
        detail: `요인 선택 후: |Pearson r| ≥ ${hypo2AbsRThreshold} 이고 n ≥ ${hypo2MinN}이면 소가설 2를 지지합니다.`,
        tone: "muted" as const,
      };
    }
    const r = overlayF1CorrH2.pearson_r;
    const n = overlayF1CorrH2.n;
    if (r == null || n == null || !Number.isFinite(Number(r)) || !Number.isFinite(Number(n))) {
      return {
        label: "판정 불가",
        detail: "상관(r) 또는 표본 수(n)를 읽지 못했습니다.",
        tone: "muted" as const,
      };
    }
    const rn = Number(r);
    const nn = Number(n);
    if (nn < hypo2MinN) {
      return {
        label: "판정 불가",
        detail: `n=${nn} (판정 최소 n ${hypo2MinN} 미만).`,
        tone: "muted" as const,
      };
    }
    const absr = Math.abs(rn);
    if (absr >= hypo2AbsRThreshold) {
      return {
        label: "가설 지지",
        detail: `가나다순 25구 F1과 「${factorLabelKr(overlayFactorH2)}」: Pearson r=${rn.toFixed(3)}, |r| ≥ ${hypo2AbsRThreshold}, n=${nn}.`,
        tone: "ok" as const,
      };
    }
    return {
      label: "가설 불만족",
      detail: `가나다순 25구 F1과 「${factorLabelKr(overlayFactorH2)}」: |r|=${absr.toFixed(3)} < ${hypo2AbsRThreshold}, n=${nn}.`,
      tone: "fail" as const,
    };
  }, [overlayFactorH2, overlayF1CorrH2, hypo2AbsRThreshold, hypo2MinN, factorLabelKr]);

  const hypo2VerdictBorder =
    hypo2Verdict.tone === "ok"
      ? "3px solid rgba(74, 222, 128, 0.85)"
      : hypo2Verdict.tone === "fail"
        ? "3px solid rgba(248, 113, 113, 0.85)"
        : "3px solid rgba(148, 163, 184, 0.45)";

  const overallF1VerdictBorder =
    overallF1Ok === true
      ? "3px solid rgba(74, 222, 128, 0.85)"
      : overallF1Ok === false
        ? "3px solid rgba(248, 113, 113, 0.85)"
        : "3px solid rgba(148, 163, 184, 0.45)";

  /** 소가설 2 · 1단계: 구별 F1 동질성 검정 해석 */
  const hypo2F1HomogeneityNarrative = useMemo(() => {
    if (f1TestErr) return { tone: "err" as const, body: `검정 API 오류: ${f1TestErr}` };
    if (!f1Test) return { tone: "muted" as const, body: "검정 결과를 불러오는 중입니다." };
    if (f1Test.empty) return { tone: "muted" as const, body: f1Test.error ?? "검정을 수행할 수 없습니다." };
    const a = f1Test.alpha ?? 0.05;
    const p = f1Test.p_value;
    if (p == null || !Number.isFinite(Number(p))) return { tone: "muted" as const, body: "p-value를 읽지 못했습니다." };
    const rej = Boolean(f1Test.reject_h0);
    const boot =
      f1Test.test_mode === "bootstrap_f1_iid"
        ? " 이번 결과는 TMAP·OD로 맞춘 trip 풀이 부족해 구별 F1 부트스트랩 귀무로 계산된 보조 검정입니다."
        : "";
    if (rej) {
      return {
        tone: "ok" as const,
        body: `유의수준 α=${a}에서 p=${Number(p).toFixed(4)}이므로, 「25개 자치구의 F1에 구간 차이가 없다」는 귀무가설 H₀을 기각합니다. 실제 구별 F1 분산은 귀무 분포에서 보기 드물 정도로 크다는 뜻이며, 이후 요인과의 선형 연관을 볼 때 구 단위 F1이 서로 다른 수준임을 전제로 삼을 근거가 됩니다.${boot}`,
      };
    }
    return {
      tone: "fail" as const,
      body: `p=${Number(p).toFixed(4)} ≥ α=${a}이므로 H₀을 기각하지 못했습니다. 관측된 구별 F1 퍼짐이 귀무(구 라벨이 무의미) 과정에서도 자주 나올 수 있다는 뜻으로, 아래 요인 상관·회귀류 해석은 보수적으로 읽는 것이 좋습니다.${boot}`,
    };
  }, [f1Test, f1TestErr]);

  return (
    <div className="panel district-root">
      <section className="borrow-slider" aria-label="따릉이 대여 소요시간 보정">
        <div className="borrow-slider__head">
          <h3 className="borrow-slider__title">
            따릉이 대여 소요시간 보정
            {borrowApplied > 1e-9 ? (
              <span className="borrow-slider__busy"> · 적용됨 +{borrowApplied.toFixed(2)}분</span>
            ) : null}
            {applying ? <span className="borrow-slider__busy"> · 계산 중…</span> : null}
          </h3>
          <span className="borrow-slider__value mono">+{borrowDraft.toFixed(2)}분</span>
        </div>
        <div className="borrow-slider__row">
          <input
            className="borrow-slider__range"
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={borrowDraft}
            onChange={(e) => setBorrowDraft(Number(e.target.value))}
            aria-label="대여 소요시간(분)"
          />
          <button
            type="button"
            className="btn btn-primary borrow-slider__apply"
            onClick={applyBorrow}
            disabled={applying || Math.abs(borrowDraft - borrowApplied) < 1e-9}
          >
            {applying ? "적용 중…" : "적용"}
          </button>
        </div>
        <div className="borrow-slider__scale mono">
          <span>0분</span>
          <span>0.5분</span>
          <span>1분(공식 대여신청 창)</span>
        </div>
        <p className="charts-meta" style={{ marginTop: 6 }}>
          따릉이 이용시간에 <strong>대여 동작 시간(분)</strong>을 더해{" "}
          <strong>아래 모든 결론(Depth·Coverage·F1·절감·상관)</strong>을 다시 계산합니다. 슬라이더로 값을 고르고{" "}
          <strong>‘적용’</strong>을 누르세요. <strong>0분 = 현재 레포 기준</strong>.
        </p>
      </section>

      <section className="hypo-banner hypo-banner--static" aria-label="가설 1">
        <div className="hypo-title">
          <div className="hypo-badge">가설 1</div>
          <div>
            <h2 className="panel-title" style={{ marginBottom: 4 }}>
              따릉이는 대중교통 대비 시간을 줄인다
            </h2>
            <p className="charts-meta">Depth(절약률)와 Coverage(임계 이상으로 절약되는 경로 비율)로 검증합니다.</p>
          </div>
        </div>

        <div className="subhypo-banner">
          <div className="subhypo-head">
            <div className="subhypo-badge">소가설 1</div>
            <div className="subhypo-meta">
              구별로 “대중교통-따릉이” 절약 효과를 정량화
            </div>
          </div>

          <div className="subhypo-grid">
            <div className="mini-banner">
              <div className="mini-label">Depth</div>
              <div className="mini-value highlight">{fmtPct(avg)}</div>
              <div className="mini-sub">전체 평균 절약률(%) · 빈도 가중</div>
            </div>

            <div className="mini-banner">
              <div className="mini-label">Coverage</div>
              <div className="mini-value">{fmtPct(coverageAvg)}</div>
              <div className="mini-sub">
                절약률 임계 {coverageThrPct}%로 정의한 경로 비율(구 평균). 전역 판정 Coverage는 ≥{COVERAGE_MEANINGFUL_AVG_PCT}%
              </div>
            </div>

            <div className="mini-banner" style={{ borderLeft: overallF1VerdictBorder, paddingLeft: 10 }}>
              <div className="mini-label">F1 Score</div>
              <div className={`mini-value${overallF1Ok === true ? " highlight" : ""}`}>
                {f1Avg == null || !Number.isFinite(f1Avg) ? "—" : f1Avg.toFixed(3)}
              </div>
              <div className="mini-sub">
                25개 구 F1 산술평균 · 가설 1 기준 ≥{F1_MEANINGFUL.toFixed(2)} · Depth×Coverage 조화
                {factors?.mean_f1_stats?.p_value_mean_gt_threshold_t != null &&
                Number.isFinite(factors.mean_f1_stats.p_value_mean_gt_threshold_t) ? (
                  <>
                    {" "}
                    · 단측 t (평균 &gt; {F1_MEANINGFUL.toFixed(2)}, 구를 독립 표본으로 가정){" "}
                    <span className="mono">p={fmtPvalue(factors.mean_f1_stats.p_value_mean_gt_threshold_t)}</span>
                    {factors.mean_f1_stats.bootstrap_mean_ci95?.length === 2 ? (
                      <>
                        {" "}
                        · 부트스트랩 평균 95% CI{" "}
                        <span className="mono">
                          [{factors.mean_f1_stats.bootstrap_mean_ci95[0].toFixed(3)},{" "}
                          {factors.mean_f1_stats.bootstrap_mean_ci95[1].toFixed(3)}]
                        </span>
                      </>
                    ) : null}
                  </>
                ) : null}
              </div>
            </div>

            <div className="mini-controls">
              <div className="seg-label">토글(지도/임계)</div>
              <label>
                Coverage 계산용 절약률 임계 (%)
                <input
                  type="number"
                  min={0}
                  max={95}
                  value={coverageThrPct}
                  onChange={(e) => setCoverageThrPct(Math.max(0, Math.min(95, Number(e.target.value) || 0)))}
                />
              </label>
              <div className="seg-toggle">
                <div className="seg-label">히트맵 기준</div>
                <div className="seg-buttons" role="tablist" aria-label="히트맵 기준 토글">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={mapMode === "depth"}
                    className={mapMode === "depth" ? "seg-btn active" : "seg-btn"}
                    onClick={() => setMapMode("depth")}
                  >
                    Depth
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={mapMode === "coverage"}
                    className={mapMode === "coverage" ? "seg-btn active" : "seg-btn"}
                    onClick={() => setMapMode("coverage")}
                  >
                    Coverage
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={mapMode === "f1"}
                    className={mapMode === "f1" ? "seg-btn active" : "seg-btn"}
                    onClick={() => setMapMode("f1")}
                  >
                    F1
                  </button>
                </div>
              </div>
              <label className="check">
                <input
                  type="checkbox"
                  checked={showCoverageLabels}
                  onChange={(e) => setShowCoverageLabels(e.target.checked)}
                />
                지도에 값 라벨 표시
              </label>
            </div>
          </div>

          <div className="hypo1-readout" aria-label="가설 1 지표별 판정 설명">
            <div className="hypo1-readout__head">
              <span className="hypo1-readout__title">가설 1 판정 요약</span>
              <span className="hypo1-readout__verdict-pill">{overallHypo1Summary.verdict}</span>
            </div>
            <ul className="hypo1-readout__list">
              <li>
                <span className="hypo1-readout__metric">Depth</span>
                <span
                  className={
                    "hypo1-readout__tag" +
                    (overallDepthOk === true ? " hypo1-readout__tag--ok" : "") +
                    (overallDepthOk === false ? " hypo1-readout__tag--no" : "") +
                    (overallDepthOk === null ? " hypo1-readout__tag--na" : "")
                  }
                >
                  {overallDepthOk == null ? "판정 불가" : overallDepthOk ? "충족" : "미충족"}
                </span>
                <p className="hypo1-readout__text">{overallHypo1Summary.depthExpl}</p>
              </li>
              <li>
                <span className="hypo1-readout__metric">Coverage</span>
                <span
                  className={
                    "hypo1-readout__tag" +
                    (overallCoverageOk === true ? " hypo1-readout__tag--ok" : "") +
                    (overallCoverageOk === false ? " hypo1-readout__tag--no" : "") +
                    (overallCoverageOk === null ? " hypo1-readout__tag--na" : "")
                  }
                >
                  {overallCoverageOk == null ? "판정 불가" : overallCoverageOk ? "충족" : "미충족"}
                </span>
                <p className="hypo1-readout__text">{overallHypo1Summary.coverageExpl}</p>
              </li>
              <li>
                <span className="hypo1-readout__metric">F1</span>
                <span
                  className={
                    "hypo1-readout__tag" +
                    (overallF1Ok === true ? " hypo1-readout__tag--ok" : "") +
                    (overallF1Ok === false ? " hypo1-readout__tag--no" : "") +
                    (overallF1Ok === null ? " hypo1-readout__tag--na" : "")
                  }
                >
                  {overallF1Ok == null ? "판정 불가" : overallF1Ok ? "충족" : "미충족"}
                </span>
                {factors?.mean_f1_stats?.t_stat != null &&
                Number.isFinite(factors.mean_f1_stats.t_stat) &&
                factors?.mean_f1_stats?.p_value_mean_gt_threshold_t != null &&
                Number.isFinite(factors.mean_f1_stats.p_value_mean_gt_threshold_t) ? (
                  <span
                    className="hypo1-readout__stat mono"
                    title={`단측 1표본 t검정 · H₀: 25구 F1 평균 = ${F1_MEANINGFUL.toFixed(2)}, H₁: 평균 > ${F1_MEANINGFUL.toFixed(2)} (구를 독립 표본으로 가정, df = ${(factors.mean_f1_stats.n_gu ?? 25) - 1})`}
                  >
                    검정통계량 t = {factors.mean_f1_stats.t_stat.toFixed(3)} · p ={" "}
                    {fmtPvalue(factors.mean_f1_stats.p_value_mean_gt_threshold_t)}
                  </span>
                ) : null}
                <p className="hypo1-readout__text">{overallHypo1Summary.f1Expl}</p>
              </li>
            </ul>
          </div>

          <details className="hypo1-threshold-rationale" id="hypo1-threshold-rationale">
            <summary>가설 1 기준값(Depth {DEPTH_MEANINGFUL_PCT}% · Coverage 구평균 {COVERAGE_MEANINGFUL_AVG_PCT}% · F1 {F1_MEANINGFUL.toFixed(2)}) 근거</summary>
            <div className="hypo1-threshold-rationale__inner">
              <p className="hypo1-threshold-rationale__lead">
                아래 수치는 <strong>외부 규범이 아니라</strong> 이 프로젝트의 <strong>운영 정의</strong>입니다. 정당화는 (1) 왜 이 정도를
                ‘보수적/해석 가능’한 컷으로 보는지, (2) 바꾸면 결론이 어떻게 흔들리는지 <strong>민감도</strong>로 보완하는 방식이 맞습니다.
              </p>
              <p className="hypo1-threshold-rationale__lead">
                경로 단위 절약률 임계(기본 {COVERAGE_PATH_THRESHOLD_DEFAULT_PCT}%)는 상단 입력으로 바꿀 수 있고, 상단 탭{" "}
                <strong>「임계 승률」</strong>에서 다른 컷의 승률·분포를 함께 확인할 수 있습니다.
              </p>
              {THRESHOLD_RATIONALE_BLOCKS.map((b) => (
                <div key={b.title} className="hypo1-threshold-rationale__block">
                  <h4 className="hypo1-threshold-rationale__block-title">{b.title}</h4>
                  {b.paragraphs.map((p, i) => (
                    <p key={i} className="hypo1-threshold-rationale__para">
                      {p}
                    </p>
                  ))}
                </div>
              ))}
            </div>
          </details>
        </div>
      </section>

      <section className="f1-banner" aria-label="F1 조화 지표">
        <div className="f1-head">
          <div className="f1-badge">F1</div>
          <div>
            <h3 className="f1-title">Depth × Coverage 조화 (F1)</h3>
            <p className="f1-meta">
              구별 Depth(%)와 Coverage(%)를 각각 0~1로 환산한 뒤{" "}
              <code className="mono">F1 = 2·d·c / (d + c)</code> (d=Depth/100, c=Coverage/100)로 구별 F1을 계산합니다.
              아래 <strong>평균 F1</strong>은 구별 F1의 산술평균입니다.
            </p>
          </div>
        </div>
        <div className="f1-grid">
          <div className="f1-stat">
            <div className="f1-stat-label">평균 F1</div>
            <div className="f1-stat-value mono">
              {f1Banner.avgF1 == null ? "—" : f1Banner.avgF1.toFixed(3)}
            </div>
            <div className="f1-stat-sub">
              유효 구 {f1Banner.rows.length}개 · Coverage 임계 {coverageThrPct}%
            </div>
          </div>
          <div className="f1-stat">
            <div className="f1-stat-label">정의</div>
            <div className="f1-stat-value mono small">
              d=Depth/100, c=Coverage/100
            </div>
            <div className="f1-stat-sub mono">F1=2dc/(d+c)</div>
          </div>
          <div className="f1-stat">
            <div className="f1-stat-label">해석</div>
            <div className="f1-stat-value mono small">1에 가까울수록</div>
            <div className="f1-stat-sub">절약 규모(Depth)와 범위(Coverage)가 동시에 큼</div>
          </div>
        </div>
      </section>

      {err && <p className="err">{err}</p>}

      <section className="district-section district-map">
        <div className="district-section-head">
          <h3>가설 1 · 구별 히트맵</h3>
          <div className="district-map-actions">
            <p className="charts-meta">
              색 기준: {mapMode === "depth" ? "Depth" : mapMode === "coverage" ? "Coverage" : "F1"}
            </p>
            <button type="button" className="btn btn-ghost" disabled={rebuilding} onClick={onRebuildNow}>
              {rebuilding ? "업데이트 중…" : "지금 업데이트"}
            </button>
          </div>
        </div>
        <div className="district-map-box">
          <MapContainer
            key="district-savings-map"
            center={SEOUL_CENTER}
            zoom={11}
            minZoom={9}
            maxZoom={15}
            zoomSnap={0.25}
            zoomDelta={0.25}
            maxBounds={seoulMaxBounds}
            maxBoundsViscosity={1}
            scrollWheelZoom={false}
            style={{ height: 420 }}
          >
            {seoulDistrictFitBounds && <DistrictHeatmapFitBounds bounds={seoulDistrictFitBounds} />}
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
            {/* mask everything except Seoul 25 districts */}
            {seoulMaskGeoJson && (
              <GeoJSON
                data={seoulMaskGeoJson as any}
                interactive={false}
                style={{
                  color: "transparent",
                  weight: 0,
                  fillColor: "rgba(10, 14, 20, 0.92)",
                  fillOpacity: 1,
                }}
              />
            )}
            {geo && (
              <GeoJSON
                ref={districtHeatmapGeoRef}
                data={geo as any}
                style={(feat) => {
                  const name = (feat?.properties as any)?.name as string | undefined;
                  const mv = name ? metricsByGu.get(name) : undefined;
                  const row = name ? byGu.get(name) : undefined;
                  const cov = mv?.coverage_pct ?? null;
                  const f1 = mv?.f1 ?? null;
                  const hasData =
                    mapMode === "depth"
                      ? row?.depth_pct != null && Number.isFinite(row.depth_pct)
                      : mapMode === "coverage"
                        ? cov != null && Number.isFinite(cov)
                        : f1 != null && Number.isFinite(f1);

                  if (!hasData) {
                    return {
                      color: "rgba(150, 155, 165, 0.9)",
                      weight: 1.1,
                      opacity: 1,
                      dashArray: "5 4",
                      fillColor: "#ffffff",
                      fillOpacity: 1,
                    } as any;
                  }
                  const fill = heatmapGrayscaleFill(mapMode, row?.depth_pct, cov, f1, mapRanges);
                  return {
                    color: "rgba(42, 42, 48, 0.55)",
                    weight: 1.15,
                    opacity: 0.95,
                    fillColor: fill,
                    fillOpacity: 0.96,
                  };
                }}
                onEachFeature={(feature: SeoulGuFeature, layer) => {
                  // cleanup prior marker if any (re-render)
                  const prev = (layer as any).__coverageMarker as L.Marker | undefined;
                  if (prev) {
                    try {
                      prev.remove();
                    } catch {
                      // ignore
                    }
                    (layer as any).__coverageMarker = undefined;
                  }
                  const name = (feature.properties as any)?.name as string | undefined;
                  const label = name ?? "—";
                  // hover tooltip: read latest metrics via ref so borrow/threshold 변경이 즉시 반영됨
                  layer.bindTooltip(() => {
                    const mv = name ? metricsByGuRef.current.get(name) : undefined;
                    const ratio = mv?.depth_pct ?? null;
                    const cov = mv?.coverage_pct ?? null;
                    const f1 = mv?.f1 ?? null;
                    const thr = coverageThrPctRef.current;
                    const depthLine = `Depth: ${ratio == null ? "—" : ratio.toFixed(1) + "%"}`;
                    const covLine = `Coverage(≥${thr}%): ${cov == null ? "—" : cov.toFixed(1) + "%"}`;
                    const f1Line = `F1: ${f1 == null ? "—" : f1.toFixed(3)}`;
                    return `<strong>${label}</strong><br/>${depthLine}<br/>${covLine}<br/>${f1Line}`;
                  }, { sticky: true, direction: "top", className: "district-hover-tip" } as any);

                  layer.on("mouseover", () => {
                    (layer as any).setStyle?.({
                      weight: 2.6,
                      color: "rgba(18,18,22,0.92)",
                      fillOpacity: 1,
                    });
                    (layer as any).bringToFront?.();
                    (layer as any).openTooltip?.();
                  });
                  layer.on("mouseout", () => {
                    const gj = districtHeatmapGeoRef.current;
                    if (gj) gj.resetStyle(layer);
                    (layer as any).closeTooltip?.();
                  });

                  // 값 라벨(마커)은 별도 useEffect에서 생성/갱신한다
                  // (모드 전환·borrow 적용 시 onEachFeature가 재실행되지 않으므로)
                }}
              />
            )}
          </MapContainer>
        </div>
        <p className="charts-meta">
          지도는 서울시 경계 안에서만 이동합니다(밖으로는 안 나감). 데이터 없는 구는 흰색입니다. Depth/Coverage/F1은 각각 구별 최소~최대 범위로 검정(큼)~흰색(작음) 그레이스케일입니다.
        </p>
      </section>

      <section className="district-section district-bars">
        <div className="district-section-head">
          <h3>Depth · 구별 절약률 막대 (25개)</h3>
          <p className="charts-meta">정렬: 구 가나다순 · 점선은 차트 안, 설명은 아래 범례</p>
        </div>
        <div className="district-chart-refs" aria-label="기준선 범례">
          {avg != null && Number.isFinite(avg) ? (
            <div className="district-chart-refs__item">
              <span className="district-chart-refs__sw district-chart-refs__sw--avg" aria-hidden />
              <span style={{ color: "rgba(235,240,250,0.92)" }}>평균 {avg.toFixed(1)}%</span>
            </div>
          ) : null}
          <div className="district-chart-refs__item">
            <span className="district-chart-refs__sw district-chart-refs__sw--thr" aria-hidden />
            <span style={{ color: "rgba(255, 220, 160, 0.95)" }}>유의미 기준 {DEPTH_MEANINGFUL_PCT}%</span>
          </div>
        </div>
        <div className="chart-box">
          <ResponsiveContainer width="100%" height={460}>
            <BarChart data={chartData} margin={{ top: 14, right: 12, bottom: 96, left: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(90,110,140,0.25)" />
              <XAxis
                dataKey="gu"
                angle={-35}
                textAnchor="end"
                interval={0}
                height={110}
                tick={{ fill: "rgba(205,215,230,0.9)", fontSize: 11 }}
              />
              <YAxis
                yAxisId="left"
                tick={{ fill: "rgba(205,215,230,0.9)", fontSize: 11 }}
                label={{ value: "Depth(%)", angle: -90, position: "insideLeft", fill: "rgba(205,215,230,0.75)" }}
              />
              <Tooltip
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null;
                  const p = payload[0]?.payload as any;
                  const raw = p.depth_pct_raw as number | null;
                  return (
                    <div className="district-tip">
                      <div className="district-tip-title">{p.gu}</div>
                      <div className="district-tip-row">
                        Depth: <strong>{raw == null ? "—" : `${raw.toFixed(1)}%`}</strong>
                      </div>
                      <div className="district-tip-row">
                        Coverage:{" "}
                        <span className="mono">
                          {p.coverage == null || !Number.isFinite(p.coverage) ? "—" : `${p.coverage.toFixed(1)}%`}
                        </span>
                      </div>
                      <div className="district-tip-row">
                        매칭쌍: <span className="mono">{p.pairs_matched}</span>
                      </div>
                    </div>
                  );
                }}
              />
              {avg != null && (
                <ReferenceLine yAxisId="left" y={avg} stroke="rgba(255,255,255,0.75)" strokeDasharray="6 6" />
              )}
              <ReferenceLine
                yAxisId="left"
                y={DEPTH_MEANINGFUL_PCT}
                stroke="rgba(255, 210, 140, 0.85)"
                strokeDasharray="4 6"
              />
              <Bar yAxisId="left" dataKey="depth_bar" radius={[4, 4, 0, 0]}>
                {chartData.map((d, i) => (
                  <Cell
                    key={`d-${d.gu}-${i}`}
                    fill={barFillByMeaningful(d.depth_pct_raw, DEPTH_MEANINGFUL_PCT, "rgba(61,156,240,0.85)")}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
        <p className="charts-meta">
          <strong>Depth</strong>는 구 내 비교 가능 경로 가운데, 대중교통 총 소요 대비 따릉이 절약이 20% 이상인 경로가 차지하는 비중(%)입니다.{" "}
          <strong>유의미 기준 Depth ≥ {DEPTH_MEANINGFUL_PCT}%</strong>를 충족하면 해당 구에서 절약 규모가 표본적으로 의미 있는 수준으로
          나타난다고 해석합니다.
        </p>
      </section>

      <section className="district-section district-bars">
        <div className="district-section-head">
          <h3>Coverage · 임계 {coverageThrPct}% 이상 절약 경로 비율</h3>
          <p className="charts-meta">구 평균·유의미 기준은 아래 범례(차트와 겹치지 않음)</p>
        </div>
        <div className="district-chart-grid">
          <div className="district-chart-card">
            <div className="district-chart-title">Coverage · 임계 {coverageThrPct}% 이상 절약 경로 비율</div>
            <div className="district-chart-refs" aria-label="기준선 범례">
              {coverageAvg != null && Number.isFinite(coverageAvg) ? (
                <div className="district-chart-refs__item">
                  <span className="district-chart-refs__sw district-chart-refs__sw--avg" aria-hidden />
                  <span style={{ color: "rgba(235,240,250,0.92)" }}>구 평균 {coverageAvg.toFixed(1)}%</span>
                </div>
              ) : null}
              <div className="district-chart-refs__item">
                <span className="district-chart-refs__sw district-chart-refs__sw--thr" aria-hidden />
                <span style={{ color: "rgba(255, 220, 160, 0.95)" }}>유의미 기준 {COVERAGE_MEANINGFUL_AVG_PCT}%</span>
              </div>
            </div>
            <div className="chart-box compact">
              <ResponsiveContainer width="100%" height={460}>
                <BarChart data={chartData} margin={{ top: 14, right: 12, bottom: 96, left: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(90,110,140,0.25)" />
                  <XAxis
                    dataKey="gu"
                    angle={-35}
                    textAnchor="end"
                    interval={0}
                    height={110}
                    tick={{ fill: "rgba(205,215,230,0.9)", fontSize: 11 }}
                  />
                  <YAxis
                    yAxisId="left"
                    tick={{ fill: "rgba(205,215,230,0.9)", fontSize: 11 }}
                    label={{
                      value: "Coverage(%)",
                      angle: -90,
                      position: "insideLeft",
                      fill: "rgba(205,215,230,0.75)",
                    }}
                  />
                  <Tooltip
                    content={({ active, payload }) => {
                      if (!active || !payload?.length) return null;
                      const p = payload[0]?.payload as any;
                      const cov = p.coverage as number | null;
                      return (
                        <div className="district-tip">
                          <div className="district-tip-title">{p.gu}</div>
                          <div className="district-tip-row">
                            Coverage: <strong>{cov == null ? "—" : `${cov.toFixed(1)}%`}</strong>
                          </div>
                          <div className="district-tip-row">
                            임계: <span className="mono">{coverageThrPct}%</span>
                          </div>
                        </div>
                      );
                    }}
                  />
                  {coverageAvg != null && (
                    <ReferenceLine yAxisId="left" y={coverageAvg} stroke="rgba(255,255,255,0.75)" strokeDasharray="6 6" />
                  )}
                  <ReferenceLine
                    yAxisId="left"
                    y={COVERAGE_MEANINGFUL_AVG_PCT}
                    stroke="rgba(255, 210, 140, 0.85)"
                    strokeDasharray="4 6"
                  />
                  <Bar yAxisId="left" dataKey="coverage_bar" radius={[4, 4, 0, 0]}>
                    {chartData.map((d, i) => (
                      <Cell
                        key={`c-${d.gu}-${i}`}
                        fill={barFillByMeaningful(d.coverage as any, COVERAGE_MEANINGFUL_AVG_PCT, colorForDepthBarBlue())}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            <p className="charts-meta">
              <strong>Coverage</strong>는 구별 OD를 빈도로 가중했을 때, 상단에서 설정한 절약률 임계({coverageThrPct}%) 이상을 만족하는 경로가
              차지하는 비중(%)의 <strong>구 평균</strong>입니다. <strong>유의미 기준 Coverage ≥ {COVERAGE_MEANINGFUL_AVG_PCT}%</strong>는 고빈도
              경로에서도 절약이 넓게 관측되는지(소수 OD 편중 여부)를 판별하는 용도로 둡니다.
            </p>
          </div>
        </div>
      </section>

      <section className="district-section district-bars">
        <div className="district-section-head">
          <h3>F1 · 구별 조화 지표 (25개)</h3>
          <p className="charts-meta">정렬: 구 가나다순 · 막대는 0~0.5 스케일(넘치는 값은 막대 상단에서 잘림) · 외부 요인 선 없음</p>
        </div>
        <div className="district-chart-refs" aria-label="기준선 범례">
          {f1Avg != null && Number.isFinite(f1Avg) ? (
            <div className="district-chart-refs__item">
              <span className="district-chart-refs__sw district-chart-refs__sw--avg" aria-hidden />
              <span style={{ color: "rgba(235,240,250,0.92)" }}>구 평균 F1 {f1Avg.toFixed(3)}</span>
            </div>
          ) : null}
          <div className="district-chart-refs__item">
            <span className="district-chart-refs__sw district-chart-refs__sw--thr" aria-hidden />
            <span style={{ color: "rgba(255, 220, 160, 0.95)" }}>유의미 기준 {F1_MEANINGFUL.toFixed(2)}</span>
          </div>
        </div>
        <div className="chart-box">
          <ResponsiveContainer width="100%" height={460}>
            <BarChart data={chartDataForF1BarsHypo1} margin={{ top: 14, right: 12, bottom: 96, left: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(90,110,140,0.25)" />
              <XAxis
                dataKey="gu"
                angle={-35}
                textAnchor="end"
                interval={0}
                height={110}
                tick={{ fill: "rgba(205,215,230,0.9)", fontSize: 11 }}
              />
              <YAxis
                yAxisId="left"
                domain={[0, 0.5]}
                tick={{ fill: "rgba(205,215,230,0.9)", fontSize: 11 }}
                label={{ value: "F1 (0~0.5)", angle: -90, position: "insideLeft", fill: "rgba(205,215,230,0.75)" }}
              />
              <Tooltip
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null;
                  const p = payload[0]?.payload as { gu?: string; f1?: number | null };
                  const v = p.f1;
                  return (
                    <div className="district-tip">
                      <div className="district-tip-title">{p.gu}</div>
                      <div className="district-tip-row">
                        F1: <strong>{v == null || !Number.isFinite(v) ? "—" : v.toFixed(3)}</strong>
                      </div>
                    </div>
                  );
                }}
              />
              {f1Avg != null && Number.isFinite(f1Avg) ? (
                <ReferenceLine
                  yAxisId="left"
                  y={Math.min(0.5, f1Avg)}
                  stroke="rgba(255,255,255,0.75)"
                  strokeDasharray="6 6"
                />
              ) : null}
              <ReferenceLine
                yAxisId="left"
                y={F1_MEANINGFUL}
                stroke="rgba(255, 210, 140, 0.85)"
                strokeDasharray="4 6"
              />
              <Bar yAxisId="left" dataKey="f1_bar_clamped" radius={[4, 4, 0, 0]}>
                {chartDataForF1BarsHypo1.map((d, i) => (
                  <Cell
                    key={`f1h1-${d.gu}-${i}`}
                    fill={barFillByMeaningful(d.f1 as number | null, F1_MEANINGFUL, colorForDepthBarBlue())}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
        <p className="charts-meta">
          <strong>소가설 1</strong>용입니다. Depth·Coverage로 만든 구별 F1만 막대로 보여 주며, 소가설 2의 요인 오버레이(빨간 선)는 넣지 않습니다. F1
          구간별 <strong>도수 히스토그램</strong>은 소가설 2에서 봅니다. 연관 요인 산점도는 상단 탭{" "}
          <strong>「F1·요인 산점도」</strong>에서 봅니다.
        </p>
      </section>

      <section className="hypo-banner hypo-banner--static" aria-label="소가설 2">
        <div className="hypo-title">
          <div className="hypo-badge">소가설 2</div>
          <div>
            <h2 className="panel-title" style={{ marginBottom: 4 }}>
              가나다순 25구 F1과 외부 요인 사이에 선형 연관이 있다
            </h2>
            <p className="charts-meta">
              <strong>1단계</strong>에서는 먼저 <a href="#hypo2-f1-homogeneity">구별 F1에 차이가 없다</a>는 귀무가설을, 가설 1과 동일한 방식으로
              구한 구별 F1 값으로 통계적으로 점검합니다. <strong>2단계</strong>에서 요인과의 Pearson 상관 등을 봅니다.
            </p>
            <p className="charts-meta" style={{ marginTop: 8 }}>
              <strong>2단계 — 요인 상관</strong> <strong>대립가설(H₁)</strong>: 선택한 요인과 가나다순 25구 F1의 Pearson 상관이 0이 아니다.{" "}
              <strong>귀무가설(H₀)</strong>: 상관이 0이다(선형 연관 없음).{" "}
              아래 표·구별 F1+요인 차트·<a href="#f1-hist-hypo2">F1 분포 히스토그램</a>과 함께 쓰며, 공선성은 VIF로 점검합니다.
            </p>
          </div>
        </div>

        <div className="hypo2-homogeneity" id="hypo2-f1-homogeneity" aria-label="소가설 2 1단계 F1 동질성 검정">
          <div className="hypo2-homogeneity__head">
            <span className="hypo2-step-badge">1단계</span>
            <h3 className="hypo2-homogeneity__title">25개 자치구 F1 — 구간 차이(동질성) 검정</h3>
          </div>
          <div className="hypo2-homogeneity__hypotheses">
            <div>
              <div className="hypo2-homogeneity__hlabel">귀무가설 H₀</div>
              <p className="hypo2-homogeneity__htext">
                {f1Test?.h0_ko ??
                  "25개 자치구의 F1(Depth·Coverage 조화)이 구에 관계없이 같은 과정에서 나왔다고 본다. 구 라벨을 무작위로 바꿔도 지금과 비슷한 구별 F1 분산이 자주 나온다."}
              </p>
            </div>
            <div>
              <div className="hypo2-homogeneity__hlabel">대립가설 H₁</div>
              <p className="hypo2-homogeneity__htext">
                {f1Test?.h1_ko ?? "실제 구별 F1 분산은 위 귀무 과정보다 크다(구마다 다른 수준)."}
              </p>
            </div>
          </div>
          <p className="hypo2-homogeneity__method">
            <strong>검정통계량</strong> <span className="mono">{f1Test?.test_stat ?? "Var(F1_gu)"}</span> — 구별 F1의 표본분산(분모 n 그대로).{" "}
            Coverage 임계는 가설 1과 동일 <strong>{coverageThrPct}%</strong>이고, 귀무분포는 Monte Carlo{" "}
            <strong>10,000회</strong>로 고정해 추정합니다(p = P(null Var ≥ 관측 Var)).
            {f1Test?.method_ko ? (
              <>
                {" "}
                {f1Test.method_ko}
              </>
            ) : null}
          </p>

          <div className="hypo2-homogeneity__tiles">
            <div className="hypo2-homogeneity__tile">
              <div className="hypo2-homogeneity__tile-label">H₀ 판정 (α = {f1Test?.alpha ?? 0.05})</div>
              <div className="hypo2-homogeneity__tile-value mono">
                {!f1Test
                  ? f1TestErr
                    ? "오류"
                    : "불러오는 중"
                  : f1Test.empty
                    ? "—"
                    : f1Test.reject_h0
                      ? "기각"
                      : "기각 못함"}
              </div>
              <div className="hypo2-homogeneity__tile-sub mono">
                {f1Test?.p_value != null && Number.isFinite(Number(f1Test.p_value))
                  ? `p = ${fmtPvalue(Number(f1Test.p_value))}${
                      f1Test?.null?.ge_count != null && f1Test?.null?.b_total != null
                        ? ` (귀무 ≥ 관측 ${f1Test.null.ge_count}/${f1Test.null.b_total.toLocaleString()}, add-one)`
                        : ""
                    } · MC ${f1Test?.null?.mc_sims ?? "—"}회 · ${
                      f1Test.test_mode === "bootstrap_f1_iid" ? "구 수" : "표본"
                    } ${f1Test?.null?.sample_n ?? "—"}${f1Test.test_mode === "bootstrap_f1_iid" ? "개" : "건/회"}${
                      f1Test.test_mode ? ` · ${f1Test.test_mode}` : ""
                    }`
                  : f1TestErr
                    ? String(f1TestErr)
                    : "—"}
              </div>
            </div>
            <div className="hypo2-homogeneity__tile">
              <div className="hypo2-homogeneity__tile-label">관측 Var(F1)</div>
              <div className="hypo2-homogeneity__tile-value mono">
                {f1Test?.observed?.var_f1 != null ? f1Test.observed.var_f1.toFixed(6) : "—"}
              </div>
              <div className="hypo2-homogeneity__tile-sub mono">
                null 평균 {f1Test?.null?.var_f1_mean != null ? f1Test.null.var_f1_mean.toFixed(6) : "—"} · null 95%분위{" "}
                {f1Test?.null?.var_f1_p95 != null ? f1Test.null.var_f1_p95.toFixed(6) : "—"}
              </div>
            </div>
            <div className="hypo2-homogeneity__tile">
              <div className="hypo2-homogeneity__tile-label">검정에 쓴 구 수 · 평균 F1</div>
              <div className="hypo2-homogeneity__tile-value mono">
                {f1Test?.observed?.districts_n != null ? `${f1Test.observed.districts_n}구` : "—"}
                {f1Test?.observed?.mean_f1 != null && Number.isFinite(f1Test.observed.mean_f1) ? (
                  <span style={{ marginLeft: 8 }}>· mean {f1Test.observed.mean_f1.toFixed(3)}</span>
                ) : null}
              </div>
              <div className="hypo2-homogeneity__tile-sub">
                아래 표는 API가 Var(F1) 계산에 사용한 구별 값과 동일합니다(가설 1 상단 F1과 정의 동기화).
              </div>
            </div>
          </div>

          {f1Test?.observed?.by_gu && f1Test.observed.by_gu.length > 0 ? (
            <details className="hypo2-homogeneity__details">
              <summary>
                검정에 사용한 구별 F1 전체 표 ({f1Test.observed.by_gu.length}개 구)
              </summary>
              <div className="hypo2-homogeneity__table-wrap">
                <table className="geo-table hypo2-homogeneity__table">
                  <thead>
                    <tr>
                      <th>구</th>
                      <th className="num">Depth(%)</th>
                      <th className="num">Coverage(%)</th>
                      <th className="num">F1</th>
                    </tr>
                  </thead>
                  <tbody>
                    {f1Test.observed.by_gu.map((row) => (
                      <tr key={row.gu}>
                        <td className="label">{row.gu}</td>
                        <td className="num mono">{Number(row.depth_pct).toFixed(1)}</td>
                        <td className="num mono">{Number(row.coverage_pct).toFixed(1)}</td>
                        <td className="num mono">{Number(row.f1).toFixed(3)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          ) : null}

          <p
            className={
              "hypo2-homogeneity__verdict" +
              (hypo2F1HomogeneityNarrative.tone === "ok"
                ? " hypo2-homogeneity__verdict--ok"
                : hypo2F1HomogeneityNarrative.tone === "fail"
                  ? " hypo2-homogeneity__verdict--fail"
                  : hypo2F1HomogeneityNarrative.tone === "err"
                    ? " hypo2-homogeneity__verdict--err"
                    : " hypo2-homogeneity__verdict--muted")
            }
          >
            {hypo2F1HomogeneityNarrative.body}
          </p>
          {f1Test?.note ? (
            <p className="charts-meta hypo2-homogeneity__note-en mono" style={{ marginTop: 6 }}>
              Note (EN): {f1Test.note}
            </p>
          ) : null}
        </div>

        <div className="subhypo-banner">
          <div className="subhypo-head">
            <div className="subhypo-badge">2단계 · 요인 데이터</div>
            <div className="subhypo-meta">
              자치구 단위 기본 7개 + 보조 CSV 슬롯 3개(1인 가구·고용률·공원면적) · Coverage 임계는 가설 1과 동일(
              {coverageThrPct}%) · 상관·VIF는 저장된 구 요약과 동기화됩니다.
            </div>
          </div>
          <div className="charts-meta" style={{ marginTop: 8 }}>
            <strong>요인 정의(보고용)</strong>
            <ul className="factor-def-list">
              <li>
                <strong>평균소득(월, 원)</strong>: 국민연금공단이 공표하는 시·군·구 단위 평균소득월액(가입·소득 신고 기반). 구 간 상대적
                생활수준 비교에 사용합니다.
              </li>
              <li>
                <strong>외국인 거주비율(%)</strong>: 주민등록상 등록외국인 수를 구 총인구로 나눈 비율입니다. 행정 통계 정의이며, 단기 체류
                등록 미비 외국인은 반영되지 않습니다.
              </li>
              <li>
                <strong>인구밀도(명/㎢)</strong>: 주민등록 인구를 행정구역 면적으로 나눈 값입니다. 이동·혼잡도 등의 맥락 변수로 취급합니다.
              </li>
              <li>
                <strong>서울 중심과의 거리(km)</strong>: 구청(또는 대표 좌표)과 서울시청 기준점 사이의 대원거리입니다. 도심 접근성의 단순
                공간 지표입니다.
              </li>
              <li>
                <strong>산림·야지 비율(%)</strong>: 토지이용 분류에서 산림·야지로 잡힌 면적 비중입니다. 고도·경사가 아니라 토지 피복 구조를
                나타냅니다.
              </li>
              <li>
                <strong>고령 인구 비율(%)</strong>: 만 65세 이상 주민등록 인구를 총인구로 나눈 비율입니다.
              </li>
              <li>
                <strong>소득 분산 proxy(원/월)</strong>: 시군구 공식 소득 분산이 없을 때, 평균소득에 가정 변동계수를 곱해 만든 보조 지표입니다.
                절대값보다는 구 간 상대 비교에 적합합니다.
              </li>
              <li>
                <strong>1인 가구 비율(%)</strong>: 전체 가구 중 1인 가구 비중.{" "}
                <span className="mono">data/factors/supplemental/single_person_household_ratio_pct.csv</span>
              </li>
              <li>
                <strong>고용률(%)</strong>: 자료 출처 정의에 따른 구별 고용률.{" "}
                <span className="mono">data/factors/supplemental/employment_rate_pct.csv</span>
              </li>
              <li>
                <strong>공원 면적(㎡)</strong>: 구 관내 공원 면적 합계.{" "}
                <span className="mono">data/factors/supplemental/park_area_total_m2.csv</span>
              </li>
            </ul>
            {(factorsTable?.supplemental?.length ?? 0) > 0 ? (
              <div className="charts-meta" style={{ marginTop: 8 }}>
                <strong>보조 CSV 슬롯</strong>
                <ul className="factor-def-list">
                  {(factorsTable?.supplemental ?? []).map((s) => (
                    <li key={s.factor}>
                      <span className="mono">{s.file}</span>:{" "}
                      {s.loaded ? (
                        <>
                          로드됨 (<span className="mono">{s.rows}</span>행)
                        </>
                      ) : s.exists ? (
                        "파일 있음 · 값 없음(헤더만)"
                      ) : (
                        "대기(파일에 gu,value 채우기)"
                      )}
                      {s.error ? (
                        <>
                          {" "}
                          · <span style={{ color: "rgba(248, 113, 113, 0.95)" }}>{s.error}</span>
                        </>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>

          <div className="subhypo-grid" style={{ marginTop: 12 }}>
            <div className="mini-banner" style={{ borderLeft: overallF1VerdictBorder, paddingLeft: 10 }}>
              <div className="mini-label">전체 평균 F1 (가설 1과 동일 기준)</div>
              <div className="mini-value mono">{f1Avg == null ? "—" : f1Avg.toFixed(3)}</div>
              <div className="mini-sub">
                기준: 25구 F1 산술평균 ≥ {F1_MEANINGFUL.toFixed(2)} →{" "}
                <strong>{overallF1Ok == null ? "판정 불가" : overallF1Ok ? "충족" : "미충족"}</strong>
                {overallF1Ok != null ? " (절약 ‘규모·범위’ 조화가 전역적으로 요구 수준에 도달했는지)." : null}
                {factors?.mean_f1_stats?.p_value_mean_gt_threshold_t != null &&
                Number.isFinite(factors.mean_f1_stats.p_value_mean_gt_threshold_t) ? (
                  <>
                    {" "}
                    통계 보조: 단측 t (평균 &gt; {F1_MEANINGFUL.toFixed(2)}){" "}
                    <span className="mono">p={fmtPvalue(factors.mean_f1_stats.p_value_mean_gt_threshold_t)}</span>
                    {factors.mean_f1_stats.bootstrap_mean_ci95?.length === 2 ? (
                      <>
                        , 부트스트랩 평균 95% CI{" "}
                        <span className="mono">
                          [{factors.mean_f1_stats.bootstrap_mean_ci95[0].toFixed(3)},{" "}
                          {factors.mean_f1_stats.bootstrap_mean_ci95[1].toFixed(3)}]
                        </span>
                      </>
                    ) : null}
                    .
                  </>
                ) : null}
              </div>
            </div>
            <div className="mini-banner" style={{ borderLeft: hypo2VerdictBorder, paddingLeft: 10 }}>
              <div className="mini-label">소가설 2 판정 (선형 연관)</div>
              <div className="mini-value">{hypo2Verdict.label}</div>
              <div className="mini-sub">{hypo2Verdict.detail}</div>
            </div>
          </div>

          <div className="subhypo-grid">
            <div className="mini-banner">
              <div className="mini-label">범주</div>
              <div className="seg-buttons seg-buttons--3">
                <button
                  type="button"
                  className={factorCategory === "population" ? "seg active" : "seg"}
                  onClick={() => setFactorCategory("population")}
                >
                  인구
                </button>
                <button
                  type="button"
                  className={factorCategory === "income" ? "seg active" : "seg"}
                  onClick={() => setFactorCategory("income")}
                >
                  소득
                </button>
                <button
                  type="button"
                  className={factorCategory === "geo" ? "seg active" : "seg"}
                  onClick={() => setFactorCategory("geo")}
                >
                  지리
                </button>
              </div>
              <div className="mini-value">—</div>
              <div className="mini-sub">
                선택:{" "}
                <span className="mono">
                  {factorCategory === "population" ? "population" : factorCategory === "income" ? "income" : "geo"}
                </span>
              </div>
            </div>
            <div className="mini-banner">
              <div className="mini-label">데이터 상태</div>
              <div className="mini-value">{factors?.empty ? "없음" : "로딩/OK"}</div>
              <div className="mini-sub mono">
                {factorsErr ? `err: ${factorsErr}` : `targets=${factors?.targets_n ?? "—"}, factors=${factors?.factors_n ?? "—"}`}
              </div>
            </div>
            <div className="mini-controls">
              <div className="seg-label">다음 단계</div>
              <div className="charts-meta">
                상관이 높게 나오더라도, 요인 간 공선성(VIF/상관행렬)을 함께 보고 해석합니다.
              </div>
            </div>
          </div>

          <div className="district-chart-card" id="f1-district-overlay-hypo2" style={{ marginTop: 14 }}>
            <div className="district-chart-title">25구 가나다순 · F1(막대, 0~0.5) + 선택 요인(빨간 선)</div>
            <div className="geo-controls" style={{ marginTop: 8, flexWrap: "wrap", gap: 12, alignItems: "flex-start" }}>
              <label>
                오버레이 요인
                <select value={overlayFactorH2} onChange={(e) => setOverlayFactorH2(e.target.value)}>
                  <option value="">(없음)</option>
                  {(factorOptionsByCategory[factorCategory] ?? []).map((f) => (
                    <option key={f} value={f}>
                      {factorLabelKr(f)}
                    </option>
                  ))}
                </select>
              </label>
              {overlayFactorH2 ? (
                <div className="mini-banner" style={{ padding: "8px 12px" }}>
                  <div className="mini-label">Pearson r (가나다순 F1 vs 요인)</div>
                  <div className="mini-value mono" style={{ fontSize: 22, lineHeight: 1.15 }}>
                    {overlayF1CorrH2.pearson_r == null ? "—" : Number(overlayF1CorrH2.pearson_r).toFixed(3)}
                  </div>
                  <div className="mini-sub mono">n={overlayF1CorrH2.n == null ? "—" : String(overlayF1CorrH2.n)}</div>
                </div>
              ) : null}
              {factorsTableErr ? (
                <p className="charts-meta" style={{ color: "rgba(248, 113, 113, 0.95)", margin: 0 }}>
                  요인 테이블 로드 오류: {factorsTableErr}
                </p>
              ) : null}
            </div>
            <div className="district-chart-refs" style={{ marginTop: 8 }} aria-label="기준선·축 범례">
              {f1Avg != null && Number.isFinite(f1Avg) ? (
                <div className="district-chart-refs__item">
                  <span className="district-chart-refs__sw district-chart-refs__sw--avg" aria-hidden />
                  <span style={{ color: "rgba(235,240,250,0.92)" }}>구 평균 F1 {f1Avg.toFixed(3)} (왼쪽 Y)</span>
                </div>
              ) : null}
              <div className="district-chart-refs__item">
                <span className="district-chart-refs__sw district-chart-refs__sw--thr" aria-hidden />
                <span style={{ color: "rgba(255, 220, 160, 0.95)" }}>유의미 기준 F1 {F1_MEANINGFUL.toFixed(2)} (왼쪽 Y)</span>
              </div>
              {overlayFactorH2 ? (
                <div className="district-chart-refs__item">
                  <span
                    className="district-chart-refs__sw"
                    style={{ background: "rgba(255, 85, 85, 0.95)", borderRadius: 2 }}
                    aria-hidden
                  />
                  <span style={{ color: "rgba(252, 165, 165, 0.95)" }}>오른쪽 Y: {factorLabelKr(overlayFactorH2)}</span>
                </div>
              ) : null}
            </div>
            <div className="chart-box compact" style={{ marginTop: 10 }}>
              <ResponsiveContainer width="100%" height={480}>
                <ComposedChart
                  data={chartDataForF2}
                  margin={{ top: 14, right: overlayFactorH2 ? 56 : 12, bottom: 96, left: 8 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(90,110,140,0.25)" />
                  <XAxis
                    dataKey="gu"
                    angle={-35}
                    textAnchor="end"
                    interval={0}
                    height={110}
                    tick={{ fill: "rgba(205,215,230,0.9)", fontSize: 11 }}
                  />
                  <YAxis
                    yAxisId="left"
                    tick={{ fill: "rgba(205,215,230,0.9)", fontSize: 11 }}
                    domain={[0, 0.5]}
                    label={{
                      value: "F1 (0~0.5)",
                      angle: -90,
                      position: "insideLeft",
                      fill: "rgba(205,215,230,0.65)",
                      fontSize: 11,
                    }}
                  />
                  {overlayFactorH2 ? (
                    <YAxis
                      yAxisId="right"
                      orientation="right"
                      tick={{ fill: "rgba(255, 85, 85, 0.95)", fontSize: 11 }}
                      axisLine={{ stroke: "rgba(255, 85, 85, 0.6)" }}
                      tickLine={{ stroke: "rgba(255, 85, 85, 0.6)" }}
                      width={48}
                      domain={["auto", "auto"]}
                    />
                  ) : null}
                  <Tooltip
                    content={({ active, payload }) => {
                      if (!active || !payload?.length) return null;
                      const p = payload[0]?.payload as {
                        gu?: string;
                        f1?: number | null;
                        overlay_raw?: number | null;
                      };
                      const v = p.f1;
                      const oRaw = p.overlay_raw;
                      return (
                        <div className="district-tip">
                          <div className="district-tip-title">{p.gu}</div>
                          <div className="district-tip-row">
                            F1: <strong>{v == null || !Number.isFinite(v) ? "—" : v.toFixed(3)}</strong>
                          </div>
                          {overlayFactorH2 ? (
                            <div className="district-tip-row">
                              {factorLabelKr(overlayFactorH2)}:{" "}
                              <span className="mono">{oRaw == null || !Number.isFinite(oRaw) ? "—" : String(oRaw)}</span>
                            </div>
                          ) : null}
                        </div>
                      );
                    }}
                  />
                  {f1Avg != null && Number.isFinite(f1Avg) ? (
                    <ReferenceLine yAxisId="left" y={f1Avg} stroke="rgba(255,255,255,0.75)" strokeDasharray="6 6" />
                  ) : null}
                  <ReferenceLine
                    yAxisId="left"
                    y={F1_MEANINGFUL}
                    stroke="rgba(255, 210, 140, 0.85)"
                    strokeDasharray="4 6"
                  />
                  <Bar yAxisId="left" dataKey="f1_bar_clamped" radius={[4, 4, 0, 0]}>
                    {chartDataForF2.map((d, i) => (
                      <Cell
                        key={`h2-f1-${d.gu}-${i}`}
                        fill={barFillByMeaningful(d.f1 as number | null, F1_MEANINGFUL, colorForDepthBarBlue())}
                      />
                    ))}
                  </Bar>
                  {overlayFactorH2 ? (
                    <Line
                      type="linear"
                      yAxisId="right"
                      dataKey="overlay_raw"
                      stroke="rgba(255, 85, 85, 0.95)"
                      strokeWidth={2}
                      dot={{ r: 3, strokeWidth: 0, fill: "rgba(255, 85, 85, 0.95)" }}
                      isAnimationActive={false}
                    />
                  ) : null}
                </ComposedChart>
              </ResponsiveContainer>
            </div>
            <p className="charts-meta" style={{ marginTop: 10 }}>
              <strong>소가설 2 전용</strong> 차트입니다. 막대는 각 구 F1(왼쪽 Y, 0~0.5 표시), 요인을 고르면 빨간 <strong>직선</strong>·점(오른쪽 Y)이
              겹칩니다. 아래 <a href="#f1-hist-hypo2">F1 히스토그램</a>에는 요인 선을 두지 않습니다. 선형 연관 판정은 Pearson r과 규칙(|r| ≥
              {hypo2AbsRThreshold}, n ≥ {hypo2MinN})을 사용합니다.
            </p>
          </div>

          <div className="district-chart-card" id="f1-hist-hypo2" style={{ marginTop: 14 }}>
            <div className="district-chart-title">F1 분포 히스토그램 (25구 · 구간 폭 0.1 · 요인 빨간 선 없음)</div>
            {f1ThrBinLabel ? (
              <div className="district-chart-refs" style={{ marginTop: 6 }} aria-label="F1 기준 구간">
                <div className="district-chart-refs__item">
                  <span className="district-chart-refs__sw district-chart-refs__sw--thr" aria-hidden />
                  <span style={{ color: "rgba(255, 220, 160, 0.95)" }}>
                    유의미 기준 F1 {F1_MEANINGFUL.toFixed(2)} → 구간 <span className="mono">{f1ThrBinLabel}</span> (차트 세로 점선)
                  </span>
                </div>
              </div>
            ) : null}
            <div className="chart-box compact">
              <ResponsiveContainer width="100%" height={400}>
                <BarChart data={f1HistChartData} margin={{ top: 12, right: 12, bottom: 56, left: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(90,110,140,0.25)" />
                  <XAxis
                    dataKey="binLabel"
                    angle={-28}
                    textAnchor="end"
                    interval={0}
                    height={72}
                    tick={{ fill: "rgba(205,215,230,0.9)", fontSize: 10 }}
                    label={{
                      value: "F1 구간 (0–1, 폭 0.1)",
                      position: "insideBottom",
                      offset: -2,
                      fill: "rgba(205,215,230,0.65)",
                      fontSize: 12,
                    }}
                  />
                  <YAxis
                    yAxisId="left"
                    allowDecimals={false}
                    tick={{ fill: "rgba(147, 197, 253, 0.95)", fontSize: 11 }}
                    axisLine={{ stroke: "rgba(96, 165, 250, 0.65)" }}
                    tickLine={{ stroke: "rgba(96, 165, 250, 0.45)" }}
                    label={{
                      value: "구 개수(도수)",
                      angle: -90,
                      position: "insideLeft",
                      fill: "rgba(147, 197, 253, 0.85)",
                      fontSize: 12,
                    }}
                  />
                  <Tooltip
                    content={({ active, payload }) => {
                      if (!active || !payload?.length) return null;
                      const p = payload[0]?.payload as { binLabel?: string; count?: number };
                      return (
                        <div className="district-tip">
                          <div className="district-tip-title">F1 ∈ {p.binLabel}</div>
                          <div className="district-tip-row">
                            구 개수: <strong>{p.count ?? 0}</strong>
                          </div>
                        </div>
                      );
                    }}
                  />
                  {f1ThrBinLabel ? (
                    <ReferenceLine
                      yAxisId="left"
                      stroke="rgba(255, 210, 140, 0.75)"
                      strokeDasharray="5 5"
                      x={f1ThrBinLabel}
                    />
                  ) : null}
                  <Bar
                    yAxisId="left"
                    dataKey="count"
                    name="F1 구간 도수"
                    fill="rgba(96, 165, 250, 0.55)"
                    stroke="rgba(59, 130, 246, 0.95)"
                    strokeWidth={1}
                    radius={[3, 3, 0, 0]}
                    maxBarSize={48}
                  />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <p className="charts-meta">
              위 막대+요인 차트와 동일한 구별 F1로 만든 <strong>도수</strong>입니다. 예전 소가설 2 구성에서 <strong>요인 빨간 선만 제거</strong>한 형태이며, 유의미
              기준 구간 표시(노란 점선)는 그대로 둡니다.
            </p>
          </div>

          <div className="geo-table-wrap" style={{ marginTop: 12 }}>
            <table className="geo-table">
              <thead>
                <tr>
                  <th style={{ width: "26%" }}>요인</th>
                  <th style={{ width: "14%" }}>target</th>
                  <th className="num">Pearson r</th>
                  <th className="num">Pearson p</th>
                  <th className="num">Spearman ρ</th>
                  <th className="num">Spearman p</th>
                  <th className="num">n</th>
                </tr>
              </thead>
              <tbody>
                {(factors?.corr_rows ?? [])
                  .filter((r: FactorsCorrelationRow) => r.category === factorCategory)
                  .filter((r: FactorsCorrelationRow) => r.target === "f1")
                  .slice()
                  .sort((a, b) => (Math.abs(b.pearson_r ?? 0) - Math.abs(a.pearson_r ?? 0)))
                  .slice(0, 12)
                  .map((r) => (
                    <tr key={`${r.factor}-${r.target}`}>
                      <td className="label">{factorLabelKr(r.factor)}</td>
                      <td className="mono">{r.target}</td>
                      <td className="num mono">
                        {r.pearson_r == null || !Number.isFinite(Number(r.pearson_r)) ? "—" : Number(r.pearson_r).toFixed(3)}
                      </td>
                      <td className="num mono">{fmtPvalue(r.pearson_p)}</td>
                      <td className="num mono">
                        {r.spearman_r == null || !Number.isFinite(Number(r.spearman_r)) ? "—" : Number(r.spearman_r).toFixed(3)}
                      </td>
                      <td className="num mono">{fmtPvalue(r.spearman_p)}</td>
                      <td className="num mono">{r.n}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>

          <div className="geo-table-wrap" style={{ marginTop: 12 }}>
            <table className="geo-table">
              <thead>
                <tr>
                  <th style={{ width: "45%" }}>다중공선성(VIF)</th>
                  <th className="num">VIF</th>
                  <th className="num">R²</th>
                </tr>
              </thead>
              <tbody>
                {(factors?.vif ?? []).slice(0, 12).map((r) => {
                  const vifN = Number(r.vif);
                  const r2N = Number((r as { r2?: unknown }).r2);
                  return (
                    <tr key={r.factor}>
                      <td className="label">{factorLabelKr(r.factor)}</td>
                      <td className="num mono">
                        {Number.isFinite(vifN) ? vifN.toFixed(2) : "∞"}
                      </td>
                      <td className="num mono">{Number.isFinite(r2N) ? r2N.toFixed(3) : "—"}</td>
                    </tr>
                  );
                })}
                {(!factors?.vif || factors.vif.length === 0) && (
                  <tr>
                    <td className="label" colSpan={3}>
                      VIF 계산을 위한 요인 수/완전사례가 부족합니다. (요인을 더 추가하면 자동으로 표시됩니다.)
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {correlationTableRows.length > 0 ? (
        <section className="factor-corr-summary" aria-label="요인별 상관분석 요약표">
          <h3 className="panel-title" style={{ marginTop: 24, marginBottom: 4 }}>
            자치구별 F1-Score 편차 — 요인 상관분석 (전체 {correlationTableRows.length}개)
          </h3>
          <p className="charts-meta">
            대상 = 자치구 F1 (n={correlationTableRows[0]?.n ?? "—"})
            {borrowApplied > 1e-9 ? <>, 대여시간 +{borrowApplied.toFixed(2)}분 적용</> : null}. 상관계수 가설검정{" "}
            <span className="mono">H₀: 상관=0</span> 양측, <strong>p &lt; 0.05면 유의</strong>. Pearson r = 선형 상관,
            Spearman ρ = 순위(단조) 상관.
          </p>
          <div className="geo-table-wrap" style={{ marginTop: 10 }}>
            <table className="geo-table">
              <thead>
                <tr>
                  <th>변수</th>
                  <th className="num">Pearson r</th>
                  <th className="num">p (Pearson)</th>
                  <th className="num">Spearman ρ</th>
                  <th className="num">p (Spearman)</th>
                  <th>유의성 (α=0.05)</th>
                </tr>
              </thead>
              <tbody>
                {correlationTableRows.map((row) => {
                  const verdict =
                    row.sigPearson && row.sigSpearman
                      ? { text: "유의 (둘 다)", color: "rgba(248, 113, 113, 0.95)" }
                      : row.sigPearson
                        ? { text: "Pearson만 유의", color: "rgba(251, 191, 36, 0.95)" }
                        : row.sigSpearman
                          ? { text: "Spearman만 유의", color: "rgba(251, 191, 36, 0.95)" }
                          : { text: "유의하지 않음", color: "rgba(148, 163, 184, 0.85)" };
                  return (
                    <tr key={row.factor}>
                      <td className="label">{row.label}</td>
                      <td className="num mono">
                        {row.pearson_r == null ? "—" : Number(row.pearson_r).toFixed(3)}
                      </td>
                      <td className="num mono">{fmtPvalue(row.pearson_p)}</td>
                      <td className="num mono">
                        {row.spearman_r == null ? "—" : Number(row.spearman_r).toFixed(3)}
                      </td>
                      <td className="num mono">{fmtPvalue(row.spearman_p)}</td>
                      <td className="label" style={{ color: verdict.color, fontWeight: 600 }}>
                        {verdict.text}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="charts-meta" style={{ marginTop: 8, fontSize: "0.78rem" }}>
            p-value는 <span className="mono">scipy.stats.pearsonr</span> ·{" "}
            <span className="mono">spearmanr</span>의 양측검정 값입니다. 대여시간 슬라이더를 ‘적용’하면 F1이 바뀌며 이 표도
            함께 재계산됩니다.
          </p>
        </section>
      ) : null}
    </div>
  );
}

