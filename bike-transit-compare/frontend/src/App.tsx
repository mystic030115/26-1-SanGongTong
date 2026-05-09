import { Component, type ErrorInfo, type ReactNode, useCallback, useEffect, useState } from "react";
import DistrictSavingsPanel from "./DistrictSavingsPanel";
import MapPanel from "./MapPanel";
import ThresholdWinPanel from "./ThresholdWinPanel";
import {
  fetchOdsayUsage,
  fetchGeoOdDistanceTable,
  fetchTmapByDistrictSummary,
  fetchTmapFillStatus,
  fetchStats,
  fetchStations,
  lookupPair,
  postBatchRefreshTop,
  postTmapFillUntilComplete,
  type BatchRefreshResult,
  type GlobalStats,
  type GeoOdDistanceTable,
  type LookupResult,
  type OdsayUsage,
  type Station,
  type TmapByDistrictSummary,
  type TmapFillStatus,
} from "./api";
import "./App.css";

type TabKey = "lookup" | "charts" | "threshold" | "map";

/** 하위 패널 렌더 예외 시 전체 앱이 하얗게 죽는 것 방지 */
class PanelErrorBoundary extends Component<
  { label: string; children: ReactNode },
  { err: Error | null }
> {
  state: { err: Error | null } = { err: null };

  static getDerivedStateFromError(e: Error) {
    return { err: e };
  }

  componentDidCatch(e: Error, info: ErrorInfo) {
    console.error(`[PanelErrorBoundary ${this.props.label}]`, e, info.componentStack);
  }

  render() {
    if (this.state.err) {
      return (
        <div className="panel charts-panel-wrap" role="alert">
          <h2 className="panel-title">{this.props.label}</h2>
          <p className="err" style={{ whiteSpace: "pre-wrap" }}>
            {this.state.err.message}
          </p>
          <p className="charts-meta">개발자 도구 콘솔에 스택이 기록되었습니다. 새로고침 후에도 반복되면 이 메시지를 알려 주세요.</p>
        </div>
      );
    }
    return this.props.children;
  }
}

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
  const [districtCache, setDistrictCache] = useState<TmapByDistrictSummary | null>(null);
  const [districtCacheErr, setDistrictCacheErr] = useState<string | null>(null);
  const [districtCacheLoading, setDistrictCacheLoading] = useState(false);
  const [tmapFill, setTmapFill] = useState<TmapFillStatus | null>(null);
  const [tmapFillErr, setTmapFillErr] = useState<string | null>(null);
  const [tmapFillStarting, setTmapFillStarting] = useState(false);
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
  const [rawGu, setRawGu] = useState<string>("강남구");
  const [rawList, setRawList] = useState<string[]>([]);
  const [rawStatus, setRawStatus] = useState<string>("");
  const [rawRows, setRawRows] = useState<any[]>([]);
  const [rawOffset, setRawOffset] = useState(0);
  const [rawLimit, setRawLimit] = useState(200);
  const [rawErr, setRawErr] = useState<string | null>(null);
  const [rawLoading, setRawLoading] = useState(false);

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

  const refreshDistrictCache = useCallback(() => {
    setDistrictCacheLoading(true);
    setDistrictCacheErr(null);
    fetchTmapByDistrictSummary()
      .then(setDistrictCache)
      .catch((e) => {
        setDistrictCache(null);
        setDistrictCacheErr(String(e));
      })
      .finally(() => setDistrictCacheLoading(false));
  }, []);

  const refreshTmapFill = useCallback(() => {
    fetchTmapFillStatus()
      .then((s) => {
        setTmapFill(s);
        setTmapFillErr(null);
      })
      .catch((e) => setTmapFillErr(String(e)));
  }, []);

  const onStartTmapFill = useCallback(async () => {
    setTmapFillStarting(true);
    setTmapFillErr(null);
    try {
      await postTmapFillUntilComplete({});
      refreshTmapFill();
      refreshDistrictCache();
    } catch (e) {
      setTmapFillErr(String(e));
    } finally {
      setTmapFillStarting(false);
    }
  }, [refreshDistrictCache, refreshTmapFill]);

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
    fetchTmapByDistrictSummary()
      .then((s) => {
        if (!cancelled) setDistrictCache(s);
      })
      .catch((e) => {
        if (!cancelled) setDistrictCacheErr(String(e));
      });
    fetchTmapFillStatus()
      .then((fs) => {
        if (!cancelled) setTmapFill(fs);
      })
      .catch(() => {});
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
      fetchTmapByDistrictSummary()
        .then((s) => {
          if (!cancelled) setDistrictCache(s);
        })
        .catch(() => {});
      fetchTmapFillStatus()
        .then((fs) => {
          if (!cancelled) setTmapFill(fs);
        })
        .catch(() => {});
    }, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, []);

  const refreshRawList = useCallback(() => {
    fetch("/api/tmap-by-district/list")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("raw list 로드 실패"))))
      .then((j) => {
        const gus = (j?.gus as string[]) || [];
        setRawList(gus);
        if (gus.length && !gus.includes(rawGu)) setRawGu(gus[0]);
      })
      .catch(() => setRawList([]));
  }, [rawGu]);

  const refreshRawTable = useCallback(
    (next?: Partial<{ gu: string; status: string; offset: number; limit: number }>) => {
      const gu = next?.gu ?? rawGu;
      const status = next?.status ?? rawStatus;
      const offset = next?.offset ?? rawOffset;
      const limit = next?.limit ?? rawLimit;
      setRawLoading(true);
      setRawErr(null);
      const q = new URLSearchParams({
        gu,
        offset: String(offset),
        limit: String(limit),
        status,
      });
      fetch(`/api/tmap-by-district/table?${q}`)
        .then(async (r) => {
          if (!r.ok) throw new Error(await r.text());
          return r.json();
        })
        .then((j) => {
          setRawRows((j?.rows as any[]) || []);
          setRawGu(gu);
          setRawStatus(status);
          setRawOffset(offset);
          setRawLimit(limit);
        })
        .catch((e) => setRawErr(String(e)))
        .finally(() => setRawLoading(false));
    },
    [rawGu, rawLimit, rawOffset, rawStatus]
  );

  useEffect(() => {
    refreshRawList();
  }, [refreshRawList]);

  useEffect(() => {
    if (!tmapFill?.active) return;
    const t = window.setInterval(() => {
      refreshTmapFill();
      refreshDistrictCache();
    }, 5000);
    return () => window.clearInterval(t);
  }, [tmapFill?.active, refreshDistrictCache, refreshTmapFill]);

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
          <span className="usage-label">TMAP 호출(캐시 행 누적)</span>
          <span className="usage-count mono">
            {usage != null ? `${usage.count}회` : "—"}
          </span>
          {usage?.last_updated_utc && (
            <span className="usage-meta">
              (업데이트{" "}
              {new Date(usage.last_updated_utc).toLocaleString("ko-KR", {
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
        출발·도착 대여소를 고르고 조회하세요. 캐시에 있으면 TMAP을 다시
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
          가설 1 · Depth/Coverage
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
        <PanelErrorBoundary label="가설 1 · Depth/Coverage">
          <div className="panel charts-panel-wrap">
            <DistrictSavingsPanel />
          </div>
        </PanelErrorBoundary>
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
              조회·요약 · 구별 TMAP 캐시 현황
            </h2>
            <p className="geo-meta">
              `data/cache/tmap_by_district`에 쌓인 캐시를 기준으로 각 구별 경로(OD쌍) 누적량과 상태를 요약합니다.
            </p>
            {districtCache?.overall?.expected_pairs_total_sum ? (
              <p className="geo-meta mono" style={{ marginTop: 6 }}>
                전체 진행률(OK/기대쌍){" "}
                <strong>
                  {districtCache.overall.completion_ratio == null
                    ? "—"
                    : `${(districtCache.overall.completion_ratio * 100).toFixed(1)}%`}
                </strong>{" "}
                ({districtCache.overall.ok_rows_sum ?? 0}/{districtCache.overall.expected_pairs_total_sum}
                {districtCache.overall.cached_rows_sum != null ? (
                  <>
                    {" "}
                    · 총 CSV 행 {districtCache.overall.cached_rows_sum}
                  </>
                ) : null}
                )
                {districtCache.overall.eta?.eta_finish_at_kst && districtCache.overall.eta?.rows_per_min ? (
                  <>
                    {" "}
                    · 속도{" "}
                    <strong>{districtCache.overall.eta.rows_per_min.toFixed(1)}</strong> 행/분 · 예상 종료{" "}
                    <strong>
                      {new Date(districtCache.overall.eta.eta_finish_at_kst).toLocaleString("ko-KR", {
                        timeZone: "Asia/Seoul",
                        month: "numeric",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </strong>
                  </>
                ) : (
                  " · 예상 종료: 계산 중(속도 추정 데이터 부족)"
                )}
              </p>
            ) : null}
          </div>
          <div className="geo-controls">
            <button
              type="button"
              className="btn btn-ghost"
              disabled={districtCacheLoading}
              onClick={refreshDistrictCache}
            >
              {districtCacheLoading ? "로딩…" : "새로고침"}
            </button>
            <button
              type="button"
              className="btn"
              disabled={tmapFillStarting || Boolean(tmapFill?.active) || districtCacheLoading}
              onClick={() => void onStartTmapFill()}
            >
              {tmapFill?.active ? "구별 TMAP 채우는 중…" : "누락 재조회(백그라운드)"}
            </button>
          </div>
          {tmapFillErr ? <p className="err">{tmapFillErr}</p> : null}
          {tmapFill?.active ? (
            <p className="geo-meta" style={{ marginTop: 8 }}>
              서버가 <code className="mono">fill_tmap_cache</code>를 반복 실행 중입니다. API_ERROR가 없어질 때까지(또는 상한) 돌립니다. 진행은
              위 진행률·5초마다 자동 새로고침으로 확인하세요.
            </p>
          ) : null}
          {!tmapFill?.active && tmapFill?.last && !tmapFill.last.empty ? (
            <p className="geo-meta mono" style={{ marginTop: 6 }}>
              마지막 배치 작업: {tmapFill.last.ok ? "완료(또는 API_ERROR 0)" : "중단/실패"} · 배치 수 {tmapFill.last.batches?.length ?? 0}
              {tmapFill.last.error ? ` · ${tmapFill.last.error}` : ""}
              {tmapFill.last.finished_at_utc
                ? ` · 종료(UTC) ${new Date(tmapFill.last.finished_at_utc).toLocaleString("ko-KR", { timeZone: "UTC" })}`
                : ""}
            </p>
          ) : null}
        </div>
        {districtCacheErr && <p className="err">{districtCacheErr}</p>}
        {geoErr && <p className="err">{geoErr}</p>}
        {geo && (
          <p className="geo-meta mono" style={{ marginTop: 6 }}>
            구간 OD(700m+): {geo.rows?.length ?? 0}행 · 정렬 {geoSortBy}/{geoSortDir} · offset {geoOffset} / limit {geoLimit}
          </p>
        )}
        <div className="geo-table-wrap">
          <table className="geo-table">
            <thead>
              <tr>
                <th style={{ width: "16%" }}>구</th>
                <th className="num">전체쌍</th>
                <th className="num">총</th>
                <th className="num">OK</th>
                <th className="num">NO_PATH</th>
                <th className="num">API_ERROR</th>
                <th className="num">기타</th>
                <th className="num">진행률</th>
                <th style={{ width: 210 }}>마지막 기록(KST)</th>
              </tr>
            </thead>
            <tbody>
              {(districtCache?.rows ?? [])
                .slice()
                .sort((a, b) => String(a.gu ?? "").localeCompare(String(b.gu ?? ""), "ko"))
                .map((r) => (
                  <tr key={r.gu}>
                    <td className="label">{r.gu}</td>
                    <td className="num mono">{r.expected_pairs_total ?? "—"}</td>
                    <td className="num mono">{r.total_rows ?? 0}</td>
                    <td className="num mono">{r.ok_rows ?? 0}</td>
                    <td className="num mono">{r.no_path_rows ?? 0}</td>
                    <td className="num mono">{r.api_error_rows ?? 0}</td>
                    <td className="num mono">{r.other_rows ?? 0}</td>
                    <td className="num mono">
                      {r.completion_ratio == null ? "—" : `${(r.completion_ratio * 100).toFixed(1)}%`}
                    </td>
                    <td className="mono">
                      {r.last_written_at_utc
                        ? new Date(r.last_written_at_utc).toLocaleString("ko-KR", {
                            timeZone: "Asia/Seoul",
                            month: "numeric",
                            day: "numeric",
                            hour: "2-digit",
                            minute: "2-digit",
                          })
                        : "—"}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </section>

      <details className="panel geo-banner">
        <summary className="panel-title">구별 TMAP raw 테이블 (상세)</summary>
        <div className="geo-head" style={{ marginTop: 12 }}>
          <p className="geo-meta">
            `data/cache/tmap_by_district/{rawGu}_tmap_pairs.csv` 일부를 페이지로 보여줍니다. (API 호출 없이 로컬 파일 조회)
          </p>
          <div className="geo-controls">
            <label>
              구
              <select
                value={rawGu}
                onChange={(e) => {
                  const gu = e.target.value;
                  setRawGu(gu);
                  refreshRawTable({ gu, offset: 0 });
                }}
              >
                {(rawList.length ? rawList : [rawGu]).map((g) => (
                  <option key={g} value={g}>
                    {g}
                  </option>
                ))}
              </select>
            </label>
            <label>
              상태
              <select value={rawStatus} onChange={(e) => refreshRawTable({ status: e.target.value, offset: 0 })}>
                <option value="">전체</option>
                <option value="OK">OK</option>
                <option value="NO_PATH_OR_TOO_CLOSE">NO_PATH_OR_TOO_CLOSE</option>
                <option value="API_ERROR">API_ERROR</option>
              </select>
            </label>
            <label>
              표시
              <input
                type="number"
                min={50}
                max={2000}
                value={rawLimit}
                onChange={(e) => setRawLimit(Number(e.target.value))}
                onBlur={() =>
                  refreshRawTable({ limit: Math.max(50, Math.min(2000, Math.floor(rawLimit) || 200)), offset: 0 })
                }
              />
            </label>
            <button type="button" className="btn btn-ghost" disabled={rawLoading} onClick={() => refreshRawTable({ offset: 0 })}>
              {rawLoading ? "로딩…" : "불러오기"}
            </button>
          </div>
        </div>
        {rawErr && <p className="err">{rawErr}</p>}
        <div className="geo-table-wrap">
          <table className="geo-table">
            <thead>
              <tr>
                <th style={{ width: "24%" }}>a_id</th>
                <th style={{ width: "24%" }}>b_id</th>
                <th style={{ width: "14%" }}>status</th>
                <th className="num">total(분)</th>
                <th className="num">ride(분)</th>
                <th className="num">dist(m)</th>
              </tr>
            </thead>
            <tbody>
              {(rawRows ?? []).map((r, i) => (
                <tr key={`${r.a_id}-${r.b_id}-${i}`} className={r.transit_status !== "OK" ? "over" : ""}>
                  <td className="mono">{r.a_id}</td>
                  <td className="mono">{r.b_id}</td>
                  <td className="mono">{r.transit_status}</td>
                  <td className="num mono">{r.transit_total_min_1dp ?? "—"}</td>
                  <td className="num mono">{r.transit_riding_min_1dp ?? "—"}</td>
                  <td className="num mono">{r.transit_total_dist_m ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="geo-pager">
          <button
            type="button"
            className="btn btn-ghost"
            disabled={rawLoading || rawOffset <= 0}
            onClick={() => refreshRawTable({ offset: Math.max(0, rawOffset - rawLimit) })}
          >
            이전
          </button>
          <span className="geo-meta mono">
            offset {rawOffset} · limit {rawLimit}
          </span>
          <button type="button" className="btn btn-ghost" disabled={rawLoading} onClick={() => refreshRawTable({ offset: rawOffset + rawLimit })}>
            다음
          </button>
        </div>
      </details>
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
            캐시 없으면 TMAP 호출
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
            trips.csv 기준 이용 횟수가 많은 순으로 N개 출발·도착 쌍에 대해 TMAP을
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
              갱신 후 누적 호출: {batchLast.usage.count}회
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
