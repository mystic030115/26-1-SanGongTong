import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  fetchOdThresholdSummary,
  type OdThresholdRow,
  type OdThresholdSummary,
} from "./api";

const COL = {
  bike: "#4ade80",
  transit: "#f87171",
  insufficient: "#6b7280",
  grid: "#2a3344",
  text: "#b8c0d0",
  muted: "#5c6a7e",
  /** 임계 히스토그램: 임계 이하(왼쪽) 구간 */
  histBelowThr: "#3b82f6",
  /** 임계 히스토그램: 임계 초과 방(오른쪽) 구간 */
  histAboveThr: "#64748b",
  refLine: "#cbd5e1",
};

const CLS_LABEL: Record<string, string> = {
  bike_win: "따릉이 유리",
  transit_win: "대중교통 유리",
  insufficient: "비교 불가",
};

const TIP_STYLE = {
  backgroundColor: "#1a2230",
  border: "1px solid #2a3344",
  borderRadius: 8,
};

type ThresholdWinPanelProps = {
  /** 「적용」 시 지도 등 다른 탭과 동일 임계를 맞출 때 사용 */
  onAppliedThresholdChange?: (pct: number) => void;
};

export default function ThresholdWinPanel({
  onAppliedThresholdChange,
}: ThresholdWinPanelProps) {
  const [inputPct, setInputPct] = useState(50);
  const [appliedPct, setAppliedPct] = useState(50);
  const [data, setData] = useState<OdThresholdSummary | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filterCls, setFilterCls] = useState<string>("all");
  const [search, setSearch] = useState("");

  const load = useCallback((pct: number) => {
    setLoading(true);
    setErr(null);
    fetchOdThresholdSummary(pct)
      .then(setData)
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load(appliedPct);
  }, [appliedPct, load]);

  const filteredRows = useMemo(() => {
    let r = data?.rows ?? [];
    if (filterCls !== "all") {
      r = r.filter((x) => x.classification === filterCls);
    }
    const q = search.trim().toLowerCase();
    if (q) {
      r = r.filter(
        (x) =>
          x.label_short.toLowerCase().includes(q) ||
          x.label_long.toLowerCase().includes(q) ||
          x.start_id.includes(q) ||
          x.end_id.includes(q)
      );
    }
    return r;
  }, [data, filterCls, search]);

  const histNumeric = useMemo(() => {
    const histRows = data?.hist_od_bike_rate ?? [];
    return histRows.map((e) => ({
      ...e,
      binMid:
        e.bin_lo != null && e.bin_hi != null ? (e.bin_lo + e.bin_hi) / 2 : 0,
    }));
  }, [data]);

  /** 막대 색과 동일: 구간 상한(bin_hi) ≤ 임계 → 파랑, 그 외 → 회색. */
  const histBinColorSummary = useMemo(() => {
    const rows = data?.hist_od_bike_rate ?? [];
    const thr = data?.threshold_pct ?? 0;
    let total = 0;
    let grey = 0;
    for (const e of rows) {
      const c = Number(e.count) || 0;
      total += c;
      const hi = e.bin_hi;
      const isBlue = hi != null && hi <= thr;
      if (!isBlue) grey += c;
    }
    if (total <= 0) {
      return { total: 0, grey: 0, blue: 0, greyPct: null as number | null };
    }
    return {
      total,
      grey,
      blue: total - grey,
      greyPct: (grey / total) * 100,
    };
  }, [data?.hist_od_bike_rate, data?.threshold_pct]);

  const apply = () => {
    const v = Math.min(100, Math.max(0, Number(inputPct)));
    if (Number.isNaN(v)) return;
    setInputPct(v);
    setAppliedPct(v);
    onAppliedThresholdChange?.(v);
  };

  if (loading && !data) {
    return <p className="charts-hint">불러오는 중…</p>;
  }
  if (err) {
    return (
      <div>
        <p className="err">{err}</p>
        <button type="button" className="btn btn-ghost" onClick={() => load(appliedPct)}>
          다시 시도
        </button>
      </div>
    );
  }
  if (!data) return null;

  if (data.empty) {
    return (
      <div className="charts-root threshold-root">
        <p className="charts-hint">
          비교 가능한 트립이 1건 이상인 출발·도착 쌍이 없습니다. 대중교통 캐시를 채운 뒤 다시 시도하세요.
        </p>
        <button type="button" className="btn btn-ghost" onClick={() => load(appliedPct)}>
          다시 시도
        </button>
      </div>
    );
  }

  const pieData = (data.pie_od_class ?? []).filter((p) => p.value > 0);

  return (
    <div className="charts-root threshold-root">
      <section className="threshold-control">
        <h3>임계 비율 설정</h3>
        <p className="chart-desc">
          <strong>비교 가능 트립이 1건 이상인 출발·도착 쌍만</strong> 대상입니다(비교 불가 쌍은 제외).
          각 쌍마다 그 비교 가능 트립 중 「따릉이가 더 빠른 비율」을 구하고, 아래{" "}
          <strong>{appliedPct}%를 초과</strong>하면 <span className="tag-bike">따릉이 유리</span>,
          그렇지 않으면 <span className="tag-transit">대중교통 유리</span>로 나눕니다. 이 기준은 상단
          요약·통계 차트의 트립 필터와 같습니다.
        </p>
        <div className="threshold-row">
          <label className="threshold-label">
            <span className="mono">x = </span>
            <input
              type="number"
              min={0}
              max={100}
              step={0.5}
              value={inputPct}
              onChange={(e) => setInputPct(Number(e.target.value))}
            />
            <span>% 초과 시 따릉이 유리 구간</span>
          </label>
          <input
            type="range"
            min={0}
            max={100}
            step={0.5}
            value={inputPct}
            onChange={(e) => setInputPct(Number(e.target.value))}
            className="threshold-slider"
          />
          <button type="button" className="btn" onClick={apply}>
            적용 · 새로고침
          </button>
        </div>
        <p className="mono charts-meta">
          현재 적용: <strong>{data.threshold_pct}%</strong> · 비교 가능한 출발·도착 쌍{" "}
          {data.total_od_pairs.toLocaleString()}개 (따릉이·대중교통 유리만 집계)
        </p>
      </section>

      <section className="chart-section">
        <h3>구간 유형 개수</h3>
        {pieData.length === 0 ? (
          <p className="charts-hint">표시할 데이터가 없습니다.</p>
        ) : (
          <div className="chart-box chart-box-short">
            <ResponsiveContainer width="100%" height={300}>
              <PieChart>
                <Pie
                  data={pieData}
                  dataKey="value"
                  nameKey="name"
                  cx="50%"
                  cy="50%"
                  innerRadius={52}
                  outerRadius={86}
                  paddingAngle={2}
                  label={({ name, value }) => `${name}: ${value}`}
                >
                  {pieData.map((entry, i) => (
                    <Cell
                      key={i}
                      fill={
                        entry.name.includes("따릉이")
                          ? COL.bike
                          : entry.name.includes("대중교통")
                            ? COL.transit
                            : COL.insufficient
                      }
                      stroke="#0c0f14"
                    />
                  ))}
                </Pie>
                <Tooltip contentStyle={TIP_STYLE} />
                <Legend />
              </PieChart>
            </ResponsiveContainer>
          </div>
        )}
      </section>

      <div className="charts-two-col">
        <section className="chart-section">
          <h3>따릉이 유리 구간 — 승률 상위</h3>
          <div className="chart-box">
            <ResponsiveContainer width="100%" height={320}>
              <BarChart
                layout="vertical"
                data={data.bars_top_bike_od ?? []}
                margin={{ left: 8, right: 16 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke={COL.grid} horizontal={false} />
                <XAxis type="number" domain={[0, 100]} tick={{ fill: COL.text, fontSize: 11 }} />
                <YAxis
                  type="category"
                  dataKey="label"
                  width={168}
                  tick={{ fill: COL.text, fontSize: 10 }}
                  interval={0}
                />
                <Tooltip contentStyle={TIP_STYLE} />
                <Bar dataKey="rate_pct" fill={COL.bike} radius={[0, 6, 6, 0]} name="따릉이 승률%" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>
        <section className="chart-section">
          <h3>대중교통 유리 구간 — 따릉이 승률 낮은 순</h3>
          <div className="chart-box">
            <ResponsiveContainer width="100%" height={320}>
              <BarChart
                layout="vertical"
                data={data.bars_weakest_bike_od ?? []}
                margin={{ left: 8, right: 16 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke={COL.grid} horizontal={false} />
                <XAxis type="number" domain={[0, 100]} tick={{ fill: COL.text, fontSize: 11 }} />
                <YAxis
                  type="category"
                  dataKey="label"
                  width={168}
                  tick={{ fill: COL.text, fontSize: 10 }}
                  interval={0}
                />
                <Tooltip contentStyle={TIP_STYLE} />
                <Bar dataKey="rate_pct" fill={COL.transit} radius={[0, 6, 6, 0]} name="따릉이 승률%" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>
      </div>

      <section className="chart-section">
        <h3>따릉이 승률 분포 (출발·도착 쌍 기준, 5%p 구간)</h3>
        <div className="chart-desc threshold-hist-copy">
          <p>
            <strong>무엇을 세나요.</strong> 비교 가능 트립이 있는 출발·도착 쌍마다, 그 트립들 가운데
            따릉이가 더 빠른 비율(%)을 구합니다. 그 비율을{" "}
            <strong>5%p</strong> 폭 구간(0–5%, 5–10%, …, 95–100%)으로 나누고, 구간마다{" "}
            <strong>쌍이 몇 개</strong>인지 세로 막대로 보여 줍니다.
          </p>
          <p>
            <strong>색과 임계선.</strong> 적용 임계는 <strong>{data.threshold_pct}%</strong>입니다. 각
            구간의 <strong>오른쪽 끝(상한)</strong>이 이 임계 <strong>이하</strong>이면{" "}
            <strong style={{ color: COL.histBelowThr }}>파란 막대</strong>, 상한이 임계{" "}
            <strong>보다 크면</strong>{" "}
            <strong style={{ color: COL.histAboveThr }}>회색 막대</strong>입니다. (위쪽에서 정한
            「임계 초과 시 따릉이 유리」와 같은 방향으로, 승률이 높은 쪽을 회색으로
            묶었습니다.) 세로 점선은 임계가 0–100% 축에서 어느 지점인지 표시합니다.
          </p>
          {histBinColorSummary.total > 0 && histBinColorSummary.greyPct != null ? (
            <p className="threshold-hist-stat">
              <strong>회색 막대 구간에만 해당하는 쌍</strong>은 전체{" "}
              <span className="mono">{histBinColorSummary.total.toLocaleString()}</span>개 중{" "}
              <span className="mono">{histBinColorSummary.grey.toLocaleString()}</span>개(
              <strong>{histBinColorSummary.greyPct.toFixed(1)}%</strong>). 나머지{" "}
              <span className="mono">{histBinColorSummary.blue.toLocaleString()}</span>개는 파란 구간에
              속합니다.
            </p>
          ) : null}
        </div>
        <div className="chart-box chart-box-tall">
          <ResponsiveContainer width="100%" height={360}>
            <BarChart data={histNumeric} margin={{ top: 28, right: 12, bottom: 8, left: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={COL.grid} />
              <XAxis
                type="number"
                dataKey="binMid"
                domain={[0, 100]}
                ticks={[0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]}
                tick={{ fill: COL.text, fontSize: 10 }}
                label={{
                  value: "따릉이 승률 (%)",
                  position: "insideBottom",
                  offset: -2,
                  fill: COL.muted,
                  fontSize: 12,
                }}
              />
              <YAxis
                tick={{ fill: COL.text, fontSize: 11 }}
                allowDecimals={false}
                label={{
                  value: "쌍 개수",
                  angle: -90,
                  position: "insideLeft",
                  fill: COL.muted,
                  fontSize: 12,
                }}
              />
              <Tooltip
                contentStyle={TIP_STYLE}
                formatter={(value: number) => [`${value}개`, "구간"]}
                labelFormatter={(_, payload) => {
                  const row = payload?.[0]?.payload as { name?: string } | undefined;
                  return row?.name ? `승률 구간 ${row.name}` : "";
                }}
              />
              {histNumeric.length > 0 ? (
                <ReferenceLine
                  x={Math.min(100, Math.max(0, data.threshold_pct))}
                  stroke={COL.refLine}
                  strokeDasharray="5 5"
                  label={{
                    value: `임계 ${data.threshold_pct}%`,
                    fill: COL.refLine,
                    fontSize: 11,
                    position: "top",
                  }}
                />
              ) : null}
              <Bar dataKey="count" name="구간 수" radius={[3, 3, 0, 0]} maxBarSize={22}>
                {histNumeric.map((e) => {
                  const hi = e.bin_hi;
                  const thr = data.threshold_pct;
                  const fill =
                    hi != null && hi <= thr ? COL.histBelowThr : COL.histAboveThr;
                  return <Cell key={e.name} fill={fill} />;
                })}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section className="chart-section">
        <h3>출발·도착 쌍 목록</h3>
        <div className="table-toolbar">
          <select
            value={filterCls}
            onChange={(e) => setFilterCls(e.target.value)}
            className="table-select"
          >
            <option value="all">전체 분류</option>
            <option value="bike_win">따릉이 유리</option>
            <option value="transit_win">대중교통 유리</option>
          </select>
          <input
            type="search"
            placeholder="ID·라벨 검색…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="table-search"
          />
          <span className="charts-meta">{filteredRows.length}행 표시</span>
        </div>
        <div className="od-table-wrap">
          <table className="od-table">
            <thead>
              <tr>
                <th>분류</th>
                <th>출발→도착</th>
                <th>전체트립</th>
                <th>비교가능</th>
                <th>따릉이빠름</th>
                <th>승률%</th>
              </tr>
            </thead>
            <tbody>
              {filteredRows.map((r: OdThresholdRow) => (
                <tr key={`${r.start_id}-${r.end_id}`}>
                  <td>
                    <span className={`pill pill-${r.classification}`}>
                      {CLS_LABEL[r.classification] ?? r.classification}
                    </span>
                  </td>
                  <td className="mono" title={r.label_long}>
                    {r.label_short}
                  </td>
                  <td className="mono">{r.total_trips}</td>
                  <td className="mono">{r.comparable}</td>
                  <td className="mono">{r.bike_wins}</td>
                  <td className="mono">{r.rate_pct ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
