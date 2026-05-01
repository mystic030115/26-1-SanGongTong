import { useCallback, useEffect, useState } from "react";
import ChartsPanel from "./ChartsPanel";
import MapPanel from "./MapPanel";
import ThresholdWinPanel from "./ThresholdWinPanel";
import {
  fetchOdsayUsage,
  fetchGeoOdDistanceTable,
  fetchStats,
  fetchStations,
  lookupPair,
  postBatchRefreshTop,
  type BatchRefreshResult,
  type GlobalStats,
  type GeoOdDistanceTable,
  type LookupResult,
  type OdsayUsage,
  type Station,
} from "./api";
import "./App.css";

type TabKey = "lookup" | "charts" | "threshold" | "map";

function pct(rate: number | null): string {
  if (rate == null || Number.isNaN(rate)) return "—";
  return `${(rate * 100).toFixed(1)}%`;
}

export default function App() {
  const [stations, setStations] = useState<Station[]>([]);
  const [stats, setStats] = useState<GlobalStats | null>(null);
  const [startId, setStartId] = useState("");
  const [endId, setEndId] = useState("");
  const [fetchIfMissing, setFetchIfMissing] = useState(true);
  const [forceRefresh, setForceRefresh] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [result, setResult] = useState<LookupResult | null>(null);
  const [lookupErr, setLookupErr] = useState<string | null>(null);
  const [tab, setTab] = useState<TabKey>("lookup");
  const [usage, setUsage] = useState<OdsayUsage | null>(null);
  const [geo, setGeo] = useState<GeoOdDistanceTable | null>(null);
  const [geoErr, setGeoErr] = useState<string | null>(null);
  const [geoSortBy, setGeoSortBy] = useState<"dist_m" | "trips">("dist_m");
  const [geoSortDir, setGeoSortDir] = useState<"asc" | "desc">("asc");
  const [geoLimit, setGeoLimit] = useState(200);
  const [geoOffset, setGeoOffset] = useState(0);
  const [batchN, setBatchN] = useState(20);
  const [batchForce, setBatchForce] = useState(false);
  const [batchLoading, setBatchLoading] = useState(false);
  const [batchInfo, setBatchInfo] = useState<string | null>(null);
  const [batchErr, setBatchErr] = useState<string | null>(null);
  const [batchLast, setBatchLast] = useState<BatchRefreshResult | null>(null);
  /** 임계 승률 탭 「적용」과 지도 선 색 기준을 맞춤 */
  const [mapThresholdPct, setMapThresholdPct] = useState(50);

  const refreshStats = useCallback(() => {
    fetchStats()
      .then(setStats)
      .catch(() => setStats(null));
  }, []);

  const refreshUsage = useCallback(() => {
    fetchOdsayUsage()
      .then(setUsage)
      .catch(() => setUsage(null));
  }, []);

  const refreshGeo = useCallback(
    (next?: Partial<{ sortBy: "dist_m" | "trips"; sortDir: "asc" | "desc"; limit: number; offset: number }>) => {
      const sortBy = next?.sortBy ?? geoSortBy;
      const sortDir = next?.sortDir ?? geoSortDir;
      const limit = next?.limit ?? geoLimit;
      const offset = next?.offset ?? geoOffset;
      setGeoErr(null);
      fetchGeoOdDistanceTable({
        thresholdM: 700,
        sortBy,
        sortDir,
        limit,
        offset,
      })
        .then((d) => {
          setGeo(d);
          setGeoSortBy(sortBy);
          setGeoSortDir(sortDir);
          setGeoLimit(limit);
          setGeoOffset(offset);
        })
        .catch((e) => {
          setGeo(null);
          setGeoErr(String(e));
        });
    },
    [geoLimit, geoOffset, geoSortBy, geoSortDir]
  );

  useEffect(() => {
    let cancelled = false;
    fetchStations()
      .then((s) => {
        if (cancelled) return;
        setStations(s);
        setLoadErr(null);
        setStartId((p) => p || s[0]?.id || "");
        setEndId((p) => p || (s.length > 1 ? s[1].id : s[0]?.id) || "");
      })
      .catch((e) => {
        if (!cancelled) setLoadErr(String(e));
      });
    fetchStats()
      .then((st) => {
        if (!cancelled) setStats(st);
      })
      .catch(() => {
        if (!cancelled) setStats(null);
      });
    fetchOdsayUsage()
      .then((u) => {
        if (!cancelled) setUsage(u);
      })
      .catch(() => {
        if (!cancelled) setUsage(null);
      });
    fetchGeoOdDistanceTable({
      thresholdM: 700,
      sortBy: "dist_m",
      sortDir: "asc",
      limit: 200,
      offset: 0,
    })
      .then((d) => {
        if (!cancelled) setGeo(d);
      })
      .catch((e) => {
        if (!cancelled) setGeoErr(String(e));
      });
    const t = window.setInterval(() => {
      fetchOdsayUsage()
        .then((u) => {
          if (!cancelled) setUsage(u);
        })
        .catch(() => {});
    }, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, []);

  const onLookup = async () => {
    if (!startId || !endId) return;
    setLookupErr(null);
    setResult(null);
    setLoading(true);
    try {
      const r = await lookupPair(startId, endId, {
        fetchIfMissing,
        forceRefresh,
      });
      setResult(r);
      refreshStats();
      refreshUsage();
      refreshGeo();
    } catch (e) {
      setLookupErr(String(e));
    } finally {
      setLoading(false);
    }
  };

  const onBatchRefresh = async () => {
    const n = Math.max(1, Math.min(400, Math.floor(batchN) || 1));
    setBatchInfo(null);
    setBatchErr(null);
    setBatchLast(null);
    setBatchLoading(true);
    try {
      const res = await postBatchRefreshTop(n, batchForce);
      setBatchLast(res);
      if (res.usage) setUsage(res.usage);
      setBatchInfo(
        `갱신 완료 · 이번 실행 fetch 시도 ${res.fetch_path_attempts ?? "—"}회 · 스킵(캐시 OK) ${res.skipped_cached_ok ?? "—"}건`
      );
      refreshStats();
      refreshUsage();
      refreshGeo();
    } catch (e) {
      setBatchErr(String(e));
    } finally {
      setBatchLoading(false);
    }
  };

  return (
    <div className="app">
      <div className="top-banners">
        <div className="usage-banner" role="status">
          <span className="usage-label">오늘 ODsay 호출</span>
          <span className="usage-count mono">
            {usage != null ? `${usage.count}회` : "—"}
          </span>
          {usage && (
            <span className="usage-meta">
              (KST {usage.kst_date} · 다음 0시 리셋{" "}
              {new Date(usage.next_reset_kst).toLocaleString("ko-KR", {
                timeZone: "Asia/Seoul",
                month: "numeric",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
              })}
              )
            </span>
          )}
        </div>
      </div>
      <h1>따릉이 vs 대중교통</h1>
      <p className="sub">
        출발·도착 대여소를 고르고 조회하세요. 캐시에 있으면 ODsay를 다시
        부르지 않습니다.
      </p>

      <nav className="tabs" aria-label="메인 메뉴">
        <button
          type="button"
          className={tab === "lookup" ? "tab active" : "tab"}
          onClick={() => setTab("lookup")}
        >
          조회 · 요약
        </button>
        <button
          type="button"
          className={tab === "charts" ? "tab active" : "tab"}
          onClick={() => setTab("charts")}
        >
          통계 차트
        </button>
        <button
          type="button"
          className={tab === "threshold" ? "tab active" : "tab"}
          onClick={() => setTab("threshold")}
        >
          임계 승률
        </button>
        <button
          type="button"
          className={tab === "map" ? "tab active" : "tab"}
          onClick={() => setTab("map")}
        >
          Map
        </button>
      </nav>

      {loadErr && <p className="err">{loadErr}</p>}

      {tab === "charts" ? (
        <div className="panel charts-panel-wrap">
          <h2 className="panel-title">전체 데이터 시각화</h2>
          <ChartsPanel />
        </div>
      ) : null}

      {tab === "threshold" ? (
        <div className="panel charts-panel-wrap">
          <h2 className="panel-title">임계 비율별 승패</h2>
          <ThresholdWinPanel onAppliedThresholdChange={setMapThresholdPct} />
        </div>
      ) : null}

      {tab === "map" ? (
        <div className="panel charts-panel-wrap map-panel-wrap">
          <h2 className="panel-title">대여소 지도 · 구간별 따릉이 승률</h2>
          <MapPanel thresholdPct={mapThresholdPct} />
        </div>
      ) : null}

      {tab === "lookup" ? (
        <>
      <section className="panel geo-banner">
        <div className="geo-head">
          <div>
            <h2 className="panel-title" style={{ marginBottom: 6 }}>
              조회·요약 · OD 직선거리(좌표)
            </h2>
            <p className="geo-meta">
              {geo?.total_pairs_with_coords != null ? (
                <>
                  좌표 있는 OD 쌍 <strong>{geo.total_pairs_with_coords}</strong>개 · 700m 초과{" "}
                  <strong>{geo.over_threshold_pairs ?? "—"}</strong>개 (
                  <strong>
                    {geo.over_threshold_ratio != null
                      ? `${(geo.over_threshold_ratio * 100).toFixed(1)}%`
                      : "—"}
                  </strong>
                  )
                </>
              ) : (
                "trips.csv에 등장한 출발-도착 쌍 기준으로 계산"
              )}
            </p>
          </div>
          <div className="geo-controls">
            <label>
              정렬
              <select
                value={geoSortBy}
                onChange={(e) => refreshGeo({ sortBy: e.target.value as "dist_m" | "trips", offset: 0 })}
              >
                <option value="dist_m">거리(m)</option>
                <option value="trips">트립 수</option>
              </select>
            </label>
            <label>
              방향
              <select
                value={geoSortDir}
                onChange={(e) => refreshGeo({ sortDir: e.target.value as "asc" | "desc", offset: 0 })}
              >
                <option value="asc">오름차순</option>
                <option value="desc">내림차순</option>
              </select>
            </label>
            <label>
              표시
              <input
                type="number"
                min={10}
                max={5000}
                value={geoLimit}
                onChange={(e) => setGeoLimit(Number(e.target.value))}
                onBlur={() => refreshGeo({ limit: Math.max(10, Math.min(5000, Math.floor(geoLimit) || 200)), offset: 0 })}
              />
            </label>
            <button type="button" className="btn btn-ghost" onClick={() => refreshGeo({ offset: 0 })}>
              새로고침
            </button>
          </div>
        </div>
        {geoErr && <p className="err">{geoErr}</p>}
        <div className="geo-table-wrap">
          <table className="geo-table">
            <thead>
              <tr>
                <th style={{ width: "55%" }}>OD</th>
                <th className="num">거리(m)</th>
                <th className="num">트립</th>
                <th style={{ width: 110 }}>700m</th>
              </tr>
            </thead>
            <tbody>
              {(geo?.rows ?? []).map((r) => (
                <tr key={`${r.start_id}-${r.end_id}`} className={r.over_threshold ? "over" : ""}>
                  <td className="label">{r.label}</td>
                  <td className="num mono">{r.dist_m.toFixed(1)}</td>
                  <td className="num mono">{r.trips}</td>
                  <td className="mono">{r.over_threshold ? "초과" : "이하"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="geo-pager">
          <button
            type="button"
            className="btn btn-ghost"
            disabled={geoOffset <= 0}
            onClick={() => refreshGeo({ offset: Math.max(0, geoOffset - geoLimit) })}
          >
            이전
          </button>
          <span className="geo-meta mono">
            offset {geoOffset} · limit {geoLimit}
          </span>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => refreshGeo({ offset: geoOffset + geoLimit })}
          >
            다음
          </button>
        </div>
      </section>
      <section className="grid-stats">
        <div className="stat-card">
          <div className="label">비교 가능 트립</div>
          <div className="value">
            {stats?.comparable_rows ?? "—"}
            <span className="mono" style={{ fontSize: "0.85rem", color: "var(--muted)" }}>
              {" "}
              / {stats?.trip_rows ?? "—"}
            </span>
          </div>
        </div>
        <div className="stat-card">
          <div className="label">따릉이 더 빠른 비율</div>
          <div className="value highlight">{pct(stats?.bike_faster_rate ?? null)}</div>
        </div>
        <div className="stat-card">
          <div className="label">따릉이 우위일 때 평균 절약</div>
          <div className="value">
            {stats?.avg_saved_min_when_bike_faster != null
              ? `${stats.avg_saved_min_when_bike_faster}분`
              : "—"}
          </div>
        </div>
        <div className="stat-card">
          <div className="label">평균 대중교통(분)</div>
          <div className="value mono">
            {stats?.avg_transit_min ?? "—"}
          </div>
        </div>
        <div className="stat-card">
          <div className="label">평균 따릉이(분)</div>
          <div className="value mono">{stats?.avg_bike_min ?? "—"}</div>
        </div>
      </section>
      {stats?.trip_filter_note ? (
        <p className="charts-meta trip-filter-note lookup-stats-note">
          {stats.trip_filter_note}
        </p>
      ) : null}

      <div className="panel">
        <h2>출발·도착 조회</h2>
        <div className="row">
          <div className="field">
            <label htmlFor="start">출발 대여소</label>
            <select
              id="start"
              value={startId}
              onChange={(e) => setStartId(e.target.value)}
            >
              {stations.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="end">도착 대여소</label>
            <select
              id="end"
              value={endId}
              onChange={(e) => setEndId(e.target.value)}
            >
              {stations.map((s) => (
                <option key={`e-${s.id}`} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="options">
          <label>
            <input
              type="checkbox"
              checked={fetchIfMissing}
              onChange={(e) => setFetchIfMissing(e.target.checked)}
            />
            캐시 없으면 ODsay 호출
          </label>
          <label>
            <input
              type="checkbox"
              checked={forceRefresh}
              onChange={(e) => setForceRefresh(e.target.checked)}
            />
            강제 새로고침
          </label>
        </div>
        <button type="button" className="btn" disabled={loading} onClick={onLookup}>
          {loading ? "조회 중…" : "조회"}
        </button>
        <button
          type="button"
          className="btn btn-ghost"
          disabled={loading}
          onClick={refreshStats}
        >
          통계만 새로고침
        </button>
        <div className="batch-block">
          <h3 className="batch-title">이용 많은 출발·도착 쌍 일괄 갱신</h3>
          <p className="batch-hint">
            trips.csv 기준 이용 횟수가 많은 순으로 N개 출발·도착 쌍에 대해 ODsay를
            돌려 <span className="mono">transit_pairs.csv</span>를
            갱신합니다. 통계·차트 탭에 반영됩니다.
          </p>
          <div className="batch-row">
            <label className="field batch-n">
              <span>N (1–400)</span>
              <input
                type="number"
                min={1}
                max={400}
                value={batchN}
                onChange={(e) => setBatchN(Number(e.target.value))}
              />
            </label>
            <label className="batch-check">
              <input
                type="checkbox"
                checked={batchForce}
                onChange={(e) => setBatchForce(e.target.checked)}
              />
              강제 전부 재조회
            </label>
            <button
              type="button"
              className="btn"
              disabled={batchLoading || loading}
              onClick={onBatchRefresh}
            >
              {batchLoading ? "갱신 중…" : "상위 N개 캐시 갱신"}
            </button>
          </div>
          {batchErr && <p className="err">{batchErr}</p>}
          {batchInfo && <p className="batch-ok">{batchInfo}</p>}
          {batchLast?.usage && (
            <p className="batch-ok mono">
              갱신 후 누적 호출: {batchLast.usage.count}회 (KST{" "}
              {batchLast.usage.kst_date})
            </p>
          )}
        </div>
        {lookupErr && <p className="err">{lookupErr}</p>}

        {result && (
          <div className="result">
            <h3>
              결과
              <span className={`badge ${result.from_cache ? "cache" : "live"}`}>
                {result.from_cache ? "캐시" : "실시간/갱신"}
              </span>
            </h3>
            <div className="kv">
              <div>
                <span>상태</span>
                <span className="mono">{result.transit_status}</span>
              </div>
              <div>
                <span>대중교통 총(분)</span>
                <span className="mono">
                  {result.transit_total_min ?? "—"}
                </span>
              </div>
              <div>
                <span>대중교통 탑승(분)</span>
                <span className="mono">
                  {result.transit_riding_min ?? "—"}
                </span>
              </div>
              <div>
                <span>데이터 속 평균 따릉이(분)</span>
                <span className="mono">
                  {result.bike_time_min != null
                    ? result.bike_time_min.toFixed(1)
                    : "해당 구간 트립 없음"}
                </span>
              </div>
              <div>
                <span>이 구간 트립 수</span>
                <span className="mono">{result.trip_count_for_pair}</span>
              </div>
              <div>
                <span>따릉이가 더 빠름?</span>
                <span
                  className={
                    result.bike_faster === true
                      ? "highlight"
                      : result.bike_faster === false
                        ? "warn-text"
                        : ""
                  }
                >
                  {result.bike_faster === true
                    ? "예"
                    : result.bike_faster === false
                      ? "아니오"
                      : "판단 불가 (시간/상태 부족)"}
                </span>
              </div>
              <div>
                <span>절약 가능 시간(분)</span>
                <span className="mono highlight">
                  {result.bike_saved_min != null
                    ? `약 ${result.bike_saved_min}분`
                    : "—"}
                </span>
              </div>
            </div>
            {result.api_detail && (
              <p className="detail mono">api_detail: {result.api_detail}</p>
            )}
          </div>
        )}
      </div>
        </>
      ) : null}
    </div>
  );
}
