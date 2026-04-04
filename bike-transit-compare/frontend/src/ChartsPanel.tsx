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
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fetchChartSummary, type ChartSummary } from "./api";

const COL = {
  bike: "#4ade80",
  transit: "#f87171",
  neutral: "#3d9cf0",
  muted: "#5c6a7e",
  grid: "#2a3344",
  text: "#b8c0d0",
};

function DarkTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: { name?: string; value?: number; color?: string }[];
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div
      style={{
        background: "#1a2230",
        border: "1px solid #2a3344",
        borderRadius: 8,
        padding: "8px 12px",
        fontSize: 13,
      }}
    >
      {label != null && label !== "" && (
        <div style={{ marginBottom: 4, color: COL.text }}>{label}</div>
      )}
      {payload.map((p) => (
        <div key={String(p.name)} style={{ color: p.color || "#fff" }}>
          {p.name}: <strong>{p.value}</strong>
        </div>
      ))}
    </div>
  );
}

export default function ChartsPanel() {
  const [data, setData] = useState<ChartSummary | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    setErr(null);
    fetchChartSummary()
      .then(setData)
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const rideBucketChart = useMemo(() => {
    const rows = data?.bike_win_rate_by_ride_bucket ?? [];
    return rows.map((r) => ({
      bucket: r.bucket,
      comparable: r.comparable,
      bike_wins: r.bike_wins,
      rate_pct: r.rate_pct,
      barHeight: r.comparable > 0 ? (r.rate_pct ?? 0) : 0,
    }));
  }, [data?.bike_win_rate_by_ride_bucket]);

  if (loading) {
    return <p className="charts-hint">차트 데이터 불러오는 중…</p>;
  }
  if (err) {
    return (
      <div>
        <p className="err">{err}</p>
        <button type="button" className="btn btn-ghost" onClick={load}>
          다시 시도
        </button>
      </div>
    );
  }
  if (!data || data.empty || data.error) {
    return (
      <p className="charts-hint">
        {data?.error ??
          "병합할 데이터가 없습니다. 배치 실행 후 transit_pairs를 채워 주세요."}
      </p>
    );
  }

  const pieData = (data.pie_faster ?? []).filter((d) => d.value > 0);
  const diffStacked = data.hist_diff_min_stacked ?? [];
  const statusBar = data.status_bar ?? [];
  const rideRatio = data.hist_transit_ride_ratio_pct ?? [];

  return (
    <div className="charts-root">
      <div className="charts-toolbar">
        <div className="charts-toolbar-text">
          <span className="mono charts-meta">
            트립 {data.trip_rows.toLocaleString()}건 · 비교 가능{" "}
            {data.comparable_rows.toLocaleString()}건
          </span>
          {data.trip_filter_note ? (
            <p className="charts-meta trip-filter-note">{data.trip_filter_note}</p>
          ) : null}
        </div>
        <button type="button" className="btn btn-ghost" onClick={load}>
          차트 새로고침
        </button>
      </div>

      <section className="chart-section">
        <h3>시간 차이 분포 (대중교통 − 따릉이, 분)</h3>
        <p className="chart-desc">
          비교 가능한 트립만 사용합니다. 막대를 쌓아 올린 두 색은 각 구간 안에서{" "}
          <strong>따릉이가 더 빠른 트립</strong>과{" "}
          <strong>대중교통이 같거나 더 빠른 트립</strong> 수입니다. 양수 구간은
          대중교통이 더 오래 걸린 경우(따릉이 유리), 음수는 그 반대입니다.
        </p>
        {diffStacked.length === 0 ? (
          <p className="charts-hint">표시할 데이터가 없습니다.</p>
        ) : (
          <div className="chart-box">
            <ResponsiveContainer width="100%" height={320}>
              <BarChart data={diffStacked} margin={{ bottom: 8, left: 8, right: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={COL.grid} />
                <XAxis
                  dataKey="name"
                  tick={{ fill: COL.text, fontSize: 9 }}
                  angle={-38}
                  textAnchor="end"
                  height={78}
                  interval={0}
                />
                <YAxis tick={{ fill: COL.text, fontSize: 11 }} allowDecimals={false} />
                <Tooltip content={<DarkTooltip />} />
                <Legend />
                <Bar
                  dataKey="transit_faster"
                  stackId="diff"
                  name="대중교통 같거나 더 빠름"
                  fill={COL.transit}
                  radius={[0, 0, 0, 0]}
                />
                <Bar
                  dataKey="bike_faster"
                  stackId="diff"
                  name="따릉이 더 빠름"
                  fill={COL.bike}
                  radius={[4, 4, 0, 0]}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </section>

      <div className="charts-two-col">
        <section className="chart-section">
          <h3>전체 요약 (비교 가능 트립)</h3>
          <p className="chart-desc">
            같은 출발·도착 쌍에 대해 ODsay 총시간과 따릉이 이용시간을 둘 다 알 때만
            집계합니다.
          </p>
          {pieData.length === 0 ? (
            <p className="charts-hint">비교 가능한 트립이 없습니다.</p>
          ) : (
            <div className="chart-box chart-box-short">
              <ResponsiveContainer width="100%" height={260}>
                <PieChart>
                  <Pie
                    data={pieData}
                    dataKey="value"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    innerRadius={48}
                    outerRadius={78}
                    paddingAngle={2}
                    label={({ name, percent }) =>
                      `${name} ${((percent ?? 0) * 100).toFixed(0)}%`
                    }
                  >
                    {pieData.map((_, i) => (
                      <Cell
                        key={i}
                        fill={i === 0 ? COL.bike : COL.transit}
                        stroke="#0c0f14"
                      />
                    ))}
                  </Pie>
                  <Tooltip content={<DarkTooltip />} />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            </div>
          )}
        </section>

        <section className="chart-section">
          <h3>따릉이 이용시간 구간별 · 따릉이 더 빠른 비율</h3>
          <p className="chart-desc">
            따릉이 이용시간이 짧은 트립과 긴 트립에서 승패 비율이 어떻게 다른지
            봅니다. 구간에 비교 가능 트립이 없으면 막대는 0입니다.
          </p>
          <div className="chart-box">
            <ResponsiveContainer width="100%" height={260}>
              <BarChart
                data={rideBucketChart}
                margin={{ bottom: 8, left: 4, right: 8, top: 8 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke={COL.grid} />
                <XAxis
                  dataKey="bucket"
                  tick={{ fill: COL.text, fontSize: 11 }}
                />
                <YAxis
                  domain={[0, 100]}
                  tick={{ fill: COL.text, fontSize: 11 }}
                  label={{
                    value: "따릉이 더 빠른 비율 (%)",
                    angle: -90,
                    position: "insideLeft",
                    fill: COL.muted,
                    fontSize: 11,
                  }}
                />
                <Tooltip
                  content={({ active, payload }) => {
                    if (!active || !payload?.[0]) return null;
                    const p = payload[0].payload as {
                      bucket: string;
                      comparable: number;
                      bike_wins: number;
                      rate_pct: number | null;
                    };
                    return (
                      <div
                        style={{
                          background: "#1a2230",
                          border: "1px solid #2a3344",
                          borderRadius: 8,
                          padding: "8px 12px",
                          fontSize: 13,
                          color: COL.text,
                        }}
                      >
                        <div style={{ fontWeight: 600, marginBottom: 6 }}>
                          {p.bucket}
                        </div>
                        {p.comparable <= 0 ? (
                          <div>비교 가능 트립 없음</div>
                        ) : (
                          <>
                            <div>
                              따릉이 더 빠름:{" "}
                              <strong style={{ color: COL.bike }}>
                                {p.rate_pct?.toFixed(1)}%
                              </strong>{" "}
                              ({p.bike_wins}/{p.comparable}건)
                            </div>
                          </>
                        )}
                      </div>
                    );
                  }}
                />
                <Bar dataKey="barHeight" name="따릉이 승률(%)" radius={[6, 6, 0, 0]}>
                  {rideBucketChart.map((row, i) => (
                    <Cell
                      key={i}
                      fill={row.comparable > 0 ? COL.neutral : COL.grid}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>
      </div>

      <section className="chart-section">
        <h3>따릉이(가로) vs 대중교통 총시간(세로) · 샘플</h3>
        <p className="chart-desc">
          비교 가능한 트립만 표시합니다. 초록=따릉이 더 빠름, 빨강=대중교통이 같거나
          더 빠름. 점선은 동일 시간(x=y). 툴팁의 「차이」는 대중교통−따릉이(분)입니다.
        </p>
        <div className="chart-box chart-box-tall">
          <ResponsiveContainer width="100%" height={400}>
            <ScatterChart margin={{ top: 16, right: 16, bottom: 8, left: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={COL.grid} />
              <XAxis
                type="number"
                dataKey="x"
                name="따릉이(분)"
                tick={{ fill: COL.text, fontSize: 11 }}
                label={{
                  value: "따릉이 이용시간 (분)",
                  position: "bottom",
                  offset: 0,
                  fill: COL.muted,
                  fontSize: 12,
                }}
              />
              <YAxis
                type="number"
                dataKey="y"
                name="대중교통(분)"
                tick={{ fill: COL.text, fontSize: 11 }}
                label={{
                  value: "대중교통 총시간 (분)",
                  angle: -90,
                  position: "insideLeft",
                  fill: COL.muted,
                  fontSize: 12,
                }}
              />
              <Tooltip
                cursor={{ strokeDasharray: "3 3" }}
                content={({ active, payload }) => {
                  if (!active || !payload?.[0]) return null;
                  const p = payload[0].payload as {
                    x: number;
                    y: number;
                    diff: number;
                  };
                  return (
                    <DarkTooltip
                      active
                      label={`차이(대중−따릉): ${p.diff}분`}
                      payload={[
                        { name: "따릉이", value: p.x, color: COL.bike },
                        { name: "대중교통", value: p.y, color: COL.transit },
                      ]}
                    />
                  );
                }}
              />
              <ReferenceLine
                segment={[
                  { x: 0, y: 0 },
                  { x: 120, y: 120 },
                ]}
                stroke={COL.muted}
                strokeDasharray="6 6"
              />
              <Scatter
                name="따릉이 우위"
                data={data.scatter_bike_faster ?? []}
                fill={COL.bike}
              />
              <Scatter
                name="대중교통 우위"
                data={data.scatter_transit_faster ?? []}
                fill={COL.transit}
              />
              <Legend />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section className="chart-section">
        <h3>트립 수 상위 출발·도착 쌍 (승률·평균 시간차)</h3>
        <p className="chart-desc">
          막대 길이는 트립 건수입니다. 툴팁에 해당 쌍의 비교 가능 건수, 따릉이 더 빠른
          비율, 평균(대중교통−따릉이) 분을 표시합니다.
        </p>
        <div className="chart-box">
          <ResponsiveContainer
            width="100%"
            height={Math.max(320, (data.top_od_pairs ?? []).length * 36)}
          >
            <BarChart
              layout="vertical"
              data={data.top_od_pairs ?? []}
              margin={{ left: 8, right: 24, top: 8, bottom: 8 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke={COL.grid} horizontal={false} />
              <XAxis type="number" tick={{ fill: COL.text, fontSize: 11 }} />
              <YAxis
                type="category"
                dataKey="label"
                width={200}
                tick={{ fill: COL.text, fontSize: 10 }}
                interval={0}
              />
              <Tooltip
                content={({ active, payload }) => {
                  if (!active || !payload?.[0]) return null;
                  const p = payload[0].payload as ChartSummary["top_od_pairs"][0];
                  return (
                    <div
                      style={{
                        background: "#1a2230",
                        border: "1px solid #2a3344",
                        borderRadius: 8,
                        padding: "8px 12px",
                        fontSize: 12,
                        color: COL.text,
                        maxWidth: 280,
                      }}
                    >
                      <div className="mono" style={{ marginBottom: 6 }}>
                        {p.start_id} → {p.end_id}
                      </div>
                      <div>트립 수(전체): {p.trips}</div>
                      <div>비교 가능: {p.comparable}</div>
                      <div>
                        따릉이 더 빠른 비율:{" "}
                        <strong style={{ color: COL.bike }}>
                          {p.rate_pct != null ? `${p.rate_pct.toFixed(1)}%` : "—"}
                        </strong>
                      </div>
                      <div>
                        평균(대중−따릉):{" "}
                        <strong>
                          {p.avg_diff_min != null ? `${p.avg_diff_min}분` : "—"}
                        </strong>
                      </div>
                    </div>
                  );
                }}
              />
              <Bar dataKey="trips" fill={COL.neutral} radius={[0, 6, 6, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </section>

      <details className="charts-details">
        <summary>진단 · ODsay 응답·경로 구성 (펼치기)</summary>
        <section className="chart-section chart-section-nested">
          <h3>ODsay 응답 상태 (트립 행 기준)</h3>
          <div className="chart-box">
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={statusBar} margin={{ bottom: 60, left: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={COL.grid} />
                <XAxis
                  dataKey="name"
                  tick={{ fill: COL.text, fontSize: 11 }}
                  angle={-32}
                  textAnchor="end"
                  height={70}
                  interval={0}
                />
                <YAxis tick={{ fill: COL.text, fontSize: 11 }} />
                <Tooltip content={<DarkTooltip />} />
                <Bar dataKey="count" fill={COL.neutral} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>

        {rideRatio.length > 0 && (
          <section className="chart-section chart-section-nested">
            <h3>대중교통 중 탑승시간 비율 (탑승÷총, %)</h3>
            <p className="chart-desc">총시간이 0보다 큰 OK 응답만 사용합니다.</p>
            <div className="chart-box">
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={rideRatio}>
                  <CartesianGrid strokeDasharray="3 3" stroke={COL.grid} />
                  <XAxis
                    dataKey="name"
                    tick={{ fill: COL.text, fontSize: 9 }}
                    interval={0}
                    angle={-40}
                    textAnchor="end"
                    height={72}
                  />
                  <YAxis tick={{ fill: COL.text, fontSize: 11 }} />
                  <Tooltip content={<DarkTooltip />} />
                  <Bar dataKey="count" fill="#fbbf24" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </section>
        )}
      </details>
    </div>
  );
}
