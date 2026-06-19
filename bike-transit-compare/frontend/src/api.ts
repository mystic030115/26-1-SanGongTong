export type Station = { id: string; label: string; lat: number | null; lon: number | null };

export type GlobalStats = {
  trip_rows: number;
  comparable_rows: number;
  bike_faster_count: number;
  bike_faster_rate: number | null;
  avg_transit_min: number | null;
  avg_bike_min: number | null;
  avg_saved_min_when_bike_faster: number | null;
  trip_filter_note?: string;
};

export type LookupResult = {
  start_station_id: string;
  end_station_id: string;
  from_cache: boolean;
  transit_total_min: number | null;
  transit_riding_min: number | null;
  transit_status: string;
  api_detail: string | null;
  bike_time_min: number | null;
  bike_faster: boolean | null;
  bike_saved_min: number | null;
  trip_count_for_pair: number;
  start_lat: number | null;
  start_lon: number | null;
  end_lat: number | null;
  end_lon: number | null;
};

export async function fetchStations(): Promise<Station[]> {
  const r = await fetch("/api/stations");
  if (!r.ok) throw new Error("stations 로드 실패");
  const j = await r.json();
  return j.stations as Station[];
}

export async function fetchStats(): Promise<GlobalStats> {
  const r = await fetch("/api/stats");
  if (!r.ok) throw new Error("통계 로드 실패");
  return r.json();
}

export type OdsayUsage = {
  count: number;
  by_file?: Record<string, number>;
  last_updated_utc?: string;
};

export async function fetchOdsayUsage(): Promise<OdsayUsage> {
  const r = await fetch("/api/usage");
  if (!r.ok) throw new Error("사용량 로드 실패");
  return r.json();
}

export type TmapByDistrictSummaryRow = {
  gu: string;
  file: string;
  total_rows: number;
  ok_rows: number;
  no_path_rows: number;
  api_error_rows: number;
  other_rows: number;
  expected_pairs_total?: number | null;
  completion_ratio?: number | null;
  ok_ratio?: number | null;
  /** 총 CSV 행 수 / 기대쌍(재시도로 행만 많을 때 참고) */
  rows_per_expected_ratio?: number | null;
  last_written_at_utc?: string | null;
};

export type TmapByDistrictSummary = {
  dir: string;
  rows: TmapByDistrictSummaryRow[];
  overall?: {
    expected_pairs_total_sum?: number | null;
    cached_rows_sum?: number;
    ok_rows_sum?: number;
    completion_ratio?: number | null;
    eta?: {
      rows_per_min?: number | null;
      eta_minutes?: number | null;
      eta_finish_at_kst?: string | null;
      window_sec?: number | null;
    };
  };
};

export async function fetchTmapByDistrictSummary(): Promise<TmapByDistrictSummary> {
  const r = await fetch("/api/tmap-by-district/summary");
  if (!r.ok) throw new Error("구별 TMAP 캐시 요약 로드 실패");
  return r.json();
}

export type TmapFillBatchRow = {
  batch_index: number;
  returncode: number;
  api_error_rows_sum: number;
  completion_ratio?: number | null;
  ok_rows_sum?: number | null;
  expected_pairs_total_sum?: number | null;
};

export type TmapFillLast = {
  empty?: boolean;
  ok?: boolean;
  batches?: TmapFillBatchRow[];
  error?: string | null;
  finished_at_utc?: string;
};

export type TmapFillStatus = {
  active: boolean;
  last: TmapFillLast;
};

export async function fetchTmapFillStatus(): Promise<TmapFillStatus> {
  const r = await fetch("/api/tmap-by-district/fill-status");
  if (!r.ok) throw new Error("TMAP 채움 상태 로드 실패");
  return r.json();
}

export type TmapFillStartParams = {
  workers?: number;
  pair_workers?: number;
  max_batches?: number;
  sleep_sec_between_batches?: number;
  single_pass?: boolean;
};

export async function postTmapFillUntilComplete(
  params?: TmapFillStartParams
): Promise<{ ok: boolean; started: boolean; message?: string; params?: TmapFillStartParams }> {
  const r = await fetch("/api/tmap-by-district/fill-until-complete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params ?? {}),
  });
  if (r.status === 409) throw new Error("이미 TMAP 구별 채움이 실행 중입니다.");
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export type GuFactorRow = {
  gu: string;
  factor: string;
  value: number;
  unit?: string;
  source?: string;
  year?: string;
};

export type SupplementalFactorStatus = {
  factor: string;
  file: string;
  category?: string;
  exists: boolean;
  loaded: boolean;
  rows: number;
  path?: string;
  error?: string;
};

export type FactorsTable = {
  empty: boolean;
  csv: string;
  supplemental_dir?: string;
  supplemental?: SupplementalFactorStatus[];
  rows: GuFactorRow[];
  meta?: any;
};

export type FactorsCorrelationRow = {
  factor: string;
  category: string;
  target: "f1" | "depth_pct" | "coverage_pct";
  n: number;
  pearson_r: number | null;
  /** H0: ρ=0 (Pearson), 양측 */
  pearson_p?: number | null;
  spearman_r: number | null;
  /** H0: ρ=0 (Spearman rank), 양측 */
  spearman_p?: number | null;
};

export type MeanF1Stats = {
  n_gu: number;
  mean_f1: number;
  threshold_f1: number;
  t_stat: number | null;
  /** 단측 t: 구별 F1 평균 > threshold (구를 i.i.d.로 둔 단순화) */
  p_value_mean_gt_threshold_t: number | null;
  bootstrap_b: number;
  bootstrap_mean_ci95: [number, number];
};

export type FactorsAnalysis = {
  empty: boolean;
  coverage_thr_pct: number;
  /** 따릉이 대여 소요시간(분)을 더해 재계산한 경우의 값 */
  borrow_min?: number;
  targets_n?: number;
  factors_n?: number;
  mean_f1_stats?: MeanF1Stats | null;
  corr_rows?: FactorsCorrelationRow[];
  /** borrow_min 적용 후 구별 F1 (slider 재계산용) */
  f1_by_gu?: Record<string, number>;
  factor_corr?: { factors: string[]; matrix: number[][] };
  vif?: { factor: string; vif: number; r2: number }[];
  meta?: any;
  error?: string;
};

export async function fetchFactorsTable(): Promise<FactorsTable> {
  const r = await fetch("/api/factors/table");
  if (!r.ok) throw new Error("요인 테이블 로드 실패");
  return r.json();
}

export async function fetchFactorsAnalysis(
  coverageThrPct: number,
  borrowMin = 0
): Promise<FactorsAnalysis> {
  const q = new URLSearchParams({
    coverage_thr_pct: String(coverageThrPct),
    borrow_min: String(borrowMin),
  });
  const r = await fetch(`/api/factors/analysis?${q}`);
  if (!r.ok) throw new Error("요인 분석 로드 실패");
  return r.json();
}

// ── 가설 2 · 수급 균형 (ANOVA) ─────────────────────────────
/** 가설 2 · 수급 균형 — Capa·유동량 4단계 분석 */
export type SupplyCorrPoint = { capa: number; flow: number; gu: string | null };

export type SupplyNetFlowGroup = {
  key: string;
  count: number;
  share: number;
  mean_net_flow: number;
  median_net_flow: number;
  mean_capa: number;
};

export type SupplyCapacityGroup = {
  group: string;
  count: number;
  issue_count: number;
  issue_share: number | null;
  mean_ratio: number;
  median_ratio: number;
  mean_hourly_flow: number;
  mean_capa: number;
};

export type SupplySixGroup = {
  label: string;
  net_flow_group: string;
  capacity_group: string;
  count: number;
  mean_capa: number | null;
  median_ratio: number | null;
  mean_net_flow: number | null;
};

export type SupplyAnalysis = {
  empty: boolean;
  error?: string;
  alpha: number;
  n_days: number;
  correlation: {
    n: number;
    pearson_r: number;
    pearson_p: number;
    spearman_rho: number;
    spearman_p: number;
    pearson_ci95: [number | null, number | null];
    calendar_days: number;
    points: SupplyCorrPoint[];
  };
  net_flow: {
    stations_n: number;
    groups: SupplyNetFlowGroup[];
  };
  capacity: {
    threshold_def: string;
    stations_n: number;
    by_group: SupplyCapacityGroup[];
  };
  six_group: {
    total: number;
    groups: SupplySixGroup[];
  };
};

export async function fetchSupplyAnalysis(signal?: AbortSignal): Promise<SupplyAnalysis> {
  const r = await fetch(`/api/supply/analysis`, { signal });
  if (!r.ok) throw new Error("수급 균형 분석 로드 실패");
  return r.json();
}

export type NonlinearFactorModel = {
  model_type?: string;
  equation?: string;
  beta0?: number;
  beta1?: number;
  beta2?: number;
  r2?: number;
  error?: string;
};

export type SimpleLinearFactorRow = {
  factor: string;
  label_ko?: string;
  equation?: string;
  beta0?: number;
  beta1?: number;
  beta2?: number;
  r2?: number;
  pearson_r?: number | null;
  t_stat?: number | null;
  p_value_slope?: number | null;
  significant_at_alpha?: boolean;
  lasso_selected?: boolean;
  lasso_alpha?: number;
  model_type?: string;
  nonlinear_model?: NonlinearFactorModel;
  recommended_equation?: string;
  recommended_model_type?: string;
  fit_kind?: "linear" | "quadratic";
  model_selection_reason_ko?: string;
  nonlinear_alternatives_ko?: string;
  r2_linear?: number;
  r2_quadratic?: number;
  delta_r2_quad_vs_linear?: number;
  linear_model?: NonlinearFactorModel;
  analysis_path?: string;
  interpretation_ko?: string;
  error?: string;
};

export type F1RegressionCoefRow = {
  term: string;
  factor?: string | null;
  label_ko?: string;
  beta?: number;
};

export type F1GuPrediction = {
  gu: string;
  f1_actual: number;
  f1_predicted: number;
  residual: number;
};

export type F1MultipleRegression = {
  n?: number;
  predictors?: string[];
  subset?: string;
  equation?: string;
  equation_ko?: string;
  r2?: number;
  r2_adj?: number;
  rmse?: number | null;
  mae?: number | null;
  p_value_model?: number | null;
  coefficients?: Record<string, number>;
  coefficient_rows?: F1RegressionCoefRow[];
  by_gu?: F1GuPrediction[];
  error?: string;
};

export type CcaResult = {
  n?: number;
  canonical_correlations?: number[];
  x_weights?: { pair: number; weights: Record<string, number> }[];
  y_weights?: { pair: number; weights: Record<string, number> }[];
  wilks_lambda?: number;
  p_overall_approx?: number | null;
  note?: string;
  error?: string;
};

export type LinearCanoAnalysis = {
  empty?: boolean;
  error?: string;
  alpha?: number;
  associated_factors?: string[];
  methodology?: {
    step1_ko?: string;
    step2_ko?: string;
    step3_ko?: string;
    step4_ko?: string;
    caution_ko?: string[];
  };
  simple_linear?: SimpleLinearFactorRow[];
  nonsignificant_factors?: string[];
  significant_factors?: string[];
  multiple_regression_f1?: F1MultipleRegression;
  f1_regression?: {
    recommended?: string;
    description_ko?: string;
    significant_only?: F1MultipleRegression;
    all_associated?: F1MultipleRegression;
  };
  cca?: {
    trigger?: string;
    trigger_reason_ko?: string;
    y_block_f1?: CcaResult;
    y_block_depth_coverage_f1?: CcaResult;
  };
};

export type FactorsLinearCanoResponse = {
  coverage_thr_pct: number;
  linear_cano?: LinearCanoAnalysis;
  linear_cano_empty?: boolean;
  error?: string;
};

export async function fetchFactorsLinearCano(
  coverageThrPct: number,
  alpha = 0.05
): Promise<FactorsLinearCanoResponse> {
  const q = new URLSearchParams({
    coverage_thr_pct: String(coverageThrPct),
    alpha: String(alpha),
    abs_r_threshold: "0.2",
    min_n: "15",
  });
  const r = await fetch(`/api/factors/linear-cano-analysis?${q}`);
  if (!r.ok) throw new Error("선형·CCA 분석 로드 실패");
  return r.json();
}

export type F1HomogeneityByGuRow = {
  gu: string;
  depth_pct: number;
  coverage_pct: number;
  f1: number;
};

export type F1HomogeneityTest = {
  empty: boolean;
  error?: string;
  alpha?: number;
  coverage_thr_pct?: number;
  /** trip_label_randomization | bootstrap_f1_iid (trip 풀 부족 시 보조) */
  test_mode?: string;
  observed?: {
    var_f1: number;
    districts_n: number;
    mean_f1?: number;
    by_gu?: F1HomogeneityByGuRow[];
  };
  null?: {
    mc_sims: number;
    sample_n: number;
    var_f1_mean: number;
    var_f1_p95: number;
    /** 귀무 분포에서 '관측 통계량 이상'이 나온 횟수(꼬리 개수) */
    ge_count?: number;
    b_total?: number;
  };
  p_value?: number;
  /** add_one_(b+1)/(B+1) */
  p_value_method?: string;
  /** p의 하한 = 1/(B+1) */
  p_value_floor?: number;
  reject_h0?: boolean;
  h0?: string;
  h0_ko?: string;
  h1_ko?: string;
  test_stat?: string;
  note?: string;
  method_ko?: string;
};

export async function fetchF1HomogeneityTest(opts: {
  coverageThrPct: number;
  alpha?: number;
  mcSims?: number;
  sampleN?: number;
  /** 브라우저가 지원하면 fetch 타임아웃(ms). 기본 180s. */
  timeoutMs?: number;
}): Promise<F1HomogeneityTest> {
  const q = new URLSearchParams({
    coverage_thr_pct: String(opts.coverageThrPct),
    alpha: String(opts.alpha ?? 0.05),
    mc_sims: String(opts.mcSims ?? 10000),
    sample_n: String(opts.sampleN ?? 10000),
  });
  const to = opts.timeoutMs ?? 180_000;
  const signal =
    typeof AbortSignal !== "undefined" && typeof (AbortSignal as any).timeout === "function"
      ? (AbortSignal as any).timeout(to)
      : undefined;
  const r = await fetch(`/api/f1/homogeneity-test?${q}`, { signal });
  if (!r.ok) throw new Error("F1 균일성 검정 로드 실패");
  return r.json();
}

export type OdDistanceRatio = {
  empty?: boolean;
  error?: string;
  threshold_m: number;
  total_ok_pairs_with_distance?: number;
  within_threshold_pairs?: number;
  ratio: number | null;
};

export async function fetchOdDistanceRatio(
  thresholdM: number
): Promise<OdDistanceRatio> {
  const q = new URLSearchParams({ threshold_m: String(thresholdM) });
  const r = await fetch(`/api/od-distance/ratio?${q}`);
  if (!r.ok) throw new Error("OD 거리 비율 로드 실패");
  return r.json();
}

export type GeoOdDistanceRow = {
  start_id: string;
  end_id: string;
  label: string;
  trips: number;
  dist_m: number;
  over_threshold: boolean;
};

export type GeoOdDistanceTable = {
  empty?: boolean;
  error?: string;
  threshold_m: number;
  total_pairs_with_coords?: number;
  over_threshold_pairs?: number;
  over_threshold_ratio?: number | null;
  sort_by?: "dist_m" | "trips";
  sort_dir?: "asc" | "desc";
  limit?: number;
  offset?: number;
  rows: GeoOdDistanceRow[];
};

export async function fetchGeoOdDistanceTable(opts: {
  thresholdM: number;
  sortBy: "dist_m" | "trips";
  sortDir: "asc" | "desc";
  limit: number;
  offset: number;
}): Promise<GeoOdDistanceTable> {
  const q = new URLSearchParams({
    threshold_m: String(opts.thresholdM),
    sort_by: opts.sortBy,
    sort_dir: opts.sortDir,
    limit: String(opts.limit),
    offset: String(opts.offset),
  });
  const r = await fetch(`/api/geo/od-distance-table?${q}`);
  if (!r.ok) throw new Error("OD 직선거리 표 로드 실패");
  return r.json();
}

export type BatchRefreshResult = {
  ok: boolean;
  pairs_in_run?: number;
  fetch_path_attempts?: number;
  skipped_cached_ok?: number;
  usage?: OdsayUsage;
};

export async function postBatchRefreshTop(
  n: number,
  forceRefresh: boolean
): Promise<BatchRefreshResult> {
  // GET: 조회·lookup과 동일 패턴(프록시/구 서버에서 POST만 404일 때 대비, 쿼리로 호출)
  const q = new URLSearchParams({
    n: String(n),
    force_refresh: String(forceRefresh),
  });
  const r = await fetch(`/api/batch/refresh-top-pairs?${q}`);
  if (!r.ok) {
    const t = await r.text();
    throw new Error(t || "배치 갱신 실패");
  }
  return r.json();
}

export type ChartSummary = {
  empty?: boolean;
  error?: string;
  trip_rows: number;
  comparable_rows: number;
  trip_filter_note?: string;
  pie_faster: { name: string; value: number }[];
  status_bar: { name: string; count: number }[];
  hist_diff_min_stacked: {
    name: string;
    bike_faster: number;
    transit_faster: number;
  }[];
  bike_win_rate_by_ride_bucket: {
    bucket: string;
    comparable: number;
    bike_wins: number;
    rate_pct: number | null;
  }[];
  hist_transit_ride_ratio_pct: { name: string; count: number }[];
  scatter_bike_faster: { x: number; y: number; diff: number }[];
  scatter_transit_faster: { x: number; y: number; diff: number }[];
  top_od_pairs: {
    label: string;
    trips: number;
    start_id: string;
    end_id: string;
    comparable: number;
    rate_pct: number | null;
    avg_diff_min: number | null;
  }[];
};

export async function fetchChartSummary(): Promise<ChartSummary> {
  const r = await fetch("/api/charts/summary");
  if (!r.ok) throw new Error("차트 데이터 로드 실패");
  return r.json();
}

export type MapGraphNode = {
  id: string;
  name: string;
  lat: number;
  lon: number;
};

export type MapGraphEdge = {
  from_id: string;
  to_id: string;
  from_name: string;
  to_name: string;
  rate_pct: number;
  comparable: number;
  total_trips: number;
};

export type MapGraphResponse = {
  empty?: boolean;
  error?: string;
  nodes: MapGraphNode[];
  edges: MapGraphEdge[];
  meta?: {
    min_comparable: number;
    max_edges: number;
    edge_count: number;
  };
};

export async function fetchMapGraph(
  minComparable: number,
  maxEdges: number
): Promise<MapGraphResponse> {
  const q = new URLSearchParams({
    min_comparable: String(minComparable),
    max_edges: String(maxEdges),
  });
  const r = await fetch(`/api/map/graph?${q}`);
  if (!r.ok) throw new Error("지도 데이터 로드 실패");
  return r.json();
}

export type OdThresholdRow = {
  start_id: string;
  end_id: string;
  label_short: string;
  label_long: string;
  total_trips: number;
  comparable: number;
  bike_wins: number;
  rate_pct: number | null;
  classification: "bike_win" | "transit_win";
};

export type OdThresholdSummary = {
  empty?: boolean;
  threshold_pct: number;
  total_od_pairs: number;
  pie_od_class: { name: string; value: number }[];
  pie_od_class_full: { name: string; value: number }[];
  scatter: { x: number; y: number; cls: string }[];
  hist_od_bike_rate: {
    name: string;
    count: number;
    bin_lo?: number;
    bin_hi?: number;
  }[];
  bars_top_bike_od: { label: string; rate_pct: number; comparable: number }[];
  bars_weakest_bike_od: { label: string; rate_pct: number; comparable: number }[];
  rows: OdThresholdRow[];
};

export async function fetchOdThresholdSummary(
  thresholdPct: number
): Promise<OdThresholdSummary> {
  const q = new URLSearchParams({
    threshold_pct: String(thresholdPct),
  });
  const r = await fetch(`/api/od-threshold/summary?${q}`);
  if (!r.ok) throw new Error("임계 승률 데이터 로드 실패");
  return r.json();
}

export async function lookupPair(
  startId: string,
  endId: string,
  opts: { fetchIfMissing: boolean; forceRefresh: boolean }
): Promise<LookupResult> {
  const q = new URLSearchParams({
    start_id: startId,
    end_id: endId,
    fetch_if_missing: String(opts.fetchIfMissing),
    force_refresh: String(opts.forceRefresh),
  });
  const r = await fetch(`/api/lookup?${q}`);
  if (!r.ok) {
    const t = await r.text();
    throw new Error(t || "lookup 실패");
  }
  return r.json();
}
