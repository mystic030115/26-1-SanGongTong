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
  kst_date: string;
  next_reset_kst: string;
};

export async function fetchOdsayUsage(): Promise<OdsayUsage> {
  const r = await fetch("/api/usage");
  if (!r.ok) throw new Error("사용량 로드 실패");
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
