import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fetchSupplyAnalysis, type SupplyAnalysis } from "./api";

function fmtP(p: number | null | undefined): string {
  if (p == null || !Number.isFinite(p)) return "—";
  if (p < 1e-4) return "<0.0001";
  return p.toFixed(4);
}

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toFixed(digits);
}

const NETFLOW_COLORS: Record<string, string> = {
  "+_순유입/과적": "rgba(248, 113, 113, 0.9)",
  "-_순유출/고갈": "rgba(96, 165, 250, 0.9)",
  "0_균형(기각 안 됨)": "rgba(148, 163, 184, 0.85)",
};

const NETFLOW_LABELS: Record<string, string> = {
  "+_순유입/과적": "+ 순유입 / 과적",
  "-_순유출/고갈": "− 순유출 / 고갈",
  "0_균형(기각 안 됨)": "0 균형 (기각 안 됨)",
};

const SIX_COLORS = [
  "rgba(248, 113, 113, 0.92)",
  "rgba(252, 165, 165, 0.85)",
  "rgba(96, 165, 250, 0.92)",
  "rgba(147, 197, 253, 0.85)",
  "rgba(148, 163, 184, 0.9)",
  "rgba(203, 213, 225, 0.8)",
];

function formatSixGroupLabel(label: string): string {
  return label
    .replace(/^\d+_/, "")
    .replace(/__/g, " · ")
    .replace(/Capa부족하지않음/g, "Capa 부족하지 않음")
    .replace(/Capa부족/g, "Capa 부족");
}

export default function SupplyDemandPanel() {
  const [data, setData] = useState<SupplyAnalysis | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const j = await fetchSupplyAnalysis();
      setData(j);
    } catch (e) {
      setErr(String(e));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const corr = data?.correlation;
  const scatterData = useMemo(
    () => (corr?.points ?? []).map((p) => ({ x: p.capa, y: p.flow, gu: p.gu })),
    [corr?.points]
  );

  const netFlowBars = useMemo(
    () =>
      (data?.net_flow.groups ?? []).map((g) => ({
        key: g.key,
        label: NETFLOW_LABELS[g.key] ?? g.key,
        count: g.count,
      })),
    [data?.net_flow.groups]
  );

  const sixBars = useMemo(
    () =>
      (data?.six_group.groups ?? []).map((g) => ({
        label: formatSixGroupLabel(g.label),
        count: g.count,
      })),
    [data?.six_group.groups]
  );

  return (
    <div className="panel district-root">
      <section className="hypo-banner hypo-banner--static" aria-label="가설 2">
        <div className="hypo-title">
          <div className="hypo-badge">가설 2</div>
          <div>
            <h2 className="panel-title" style={{ marginBottom: 4 }}>
              따릉이 대여소의 수급(Capa·유동량)은 균형을 이루지 못한다
            </h2>
            <p className="charts-meta">
              대여소 용량(Capa)과 실제 유동량·순유입을 4단계로 검정합니다. ① Capa ↔ 일평균 유동량 상관 → ② 대여소별 순유입량 0
              검정으로 과적(+)/고갈(−)/균형(0) 분류 → ③ Capa 부족 검정(ITDP 회전율 기준) → ④ 두 축을 결합한 6집단 분류.
              분석 기간은 2025-01-01~2025-10-31({data?.n_days ?? 304}일)입니다.
            </p>
          </div>
        </div>
      </section>

      {loading ? <p className="charts-meta">불러오는 중…</p> : null}
      {err ? <p className="err">분석 로드 오류: {err}</p> : null}

      {data && !data.empty ? (
        <>
          {/* ── 1단계: Capa ↔ 유동량 상관 ── */}
          <section className="district-section" aria-label="1단계 상관">
            <div className="district-section-head">
              <h3>1단계 · Capa ↔ 일평균 유동량 상관</h3>
              <p className="charts-meta">
                유동량 = 일별 |대여 수|+|반납 수| 합의 {corr?.calendar_days ?? 304}일 평균. H₀: 상관계수 = 0 (양측).
              </p>
            </div>
            <div className="hypo2-homogeneity__tiles" style={{ marginBottom: 10 }}>
              <div className="hypo2-homogeneity__tile">
                <div className="hypo2-homogeneity__tile-label">Pearson r</div>
                <div className="hypo2-homogeneity__tile-value mono">{fmtNum(corr?.pearson_r, 3)}</div>
                <div className="hypo2-homogeneity__tile-sub mono">
                  p = {fmtP(corr?.pearson_p)} · 95% CI [{fmtNum(corr?.pearson_ci95?.[0], 3)},{" "}
                  {fmtNum(corr?.pearson_ci95?.[1], 3)}]
                </div>
              </div>
              <div className="hypo2-homogeneity__tile">
                <div className="hypo2-homogeneity__tile-label">Spearman ρ</div>
                <div className="hypo2-homogeneity__tile-value mono">{fmtNum(corr?.spearman_rho, 3)}</div>
                <div className="hypo2-homogeneity__tile-sub mono">p = {fmtP(corr?.spearman_p)}</div>
              </div>
              <div className="hypo2-homogeneity__tile">
                <div className="hypo2-homogeneity__tile-label">분석 대여소 n</div>
                <div className="hypo2-homogeneity__tile-value mono">{corr?.n ?? "—"}</div>
                <div className="hypo2-homogeneity__tile-sub">약한 양(+)의 상관 — Capa가 클수록 유동량도 다소 큼</div>
              </div>
            </div>
            <div className="chart-box">
              <ResponsiveContainer width="100%" height={380}>
                <ScatterChart margin={{ top: 12, right: 16, bottom: 44, left: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(90,110,140,0.25)" />
                  <XAxis
                    type="number"
                    dataKey="x"
                    name="Capa"
                    tick={{ fill: "rgba(200,210,228,0.8)", fontSize: 11 }}
                    label={{ value: "기존 Capa", position: "insideBottom", offset: -18, fill: "rgba(200,210,228,0.8)" }}
                  />
                  <YAxis
                    type="number"
                    dataKey="y"
                    name="일평균 유동량"
                    tick={{ fill: "rgba(200,210,228,0.8)", fontSize: 11 }}
                    label={{
                      value: "일평균 유동량",
                      angle: -90,
                      position: "insideLeft",
                      fill: "rgba(200,210,228,0.8)",
                    }}
                  />
                  <Tooltip
                    cursor={{ strokeDasharray: "3 3" }}
                    contentStyle={{ background: "rgba(15,20,30,0.95)", border: "1px solid rgba(90,110,140,0.5)" }}
                    formatter={(v: number, n: string) => [fmtNum(v, 1), n === "x" ? "Capa" : "일평균 유동량"]}
                  />
                  <Scatter data={scatterData} fill="rgba(96,165,250,0.45)" />
                </ScatterChart>
              </ResponsiveContainer>
            </div>
          </section>

          {/* ── 2단계: 순유입 0 검정 ── */}
          <section className="district-section" aria-label="2단계 순유입 검정">
            <div className="district-section-head">
              <h3>2단계 · 순유입량 0 검정 → 과적/고갈/균형 분류</h3>
              <p className="charts-meta">
                대여소별 단일표본 t검정. H₀: 일평균 순유입량 = 0 (양측, n_days={data.n_days}, α={data.alpha}). 기각 시 평균
                부호로 +(과적)/−(고갈), 기각 못하면 0(균형). 대상 {data.net_flow.stations_n}개 대여소.
              </p>
            </div>
            <div className="chart-box">
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={netFlowBars} margin={{ top: 18, right: 12, bottom: 8, left: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(90,110,140,0.25)" />
                  <XAxis dataKey="label" tick={{ fill: "rgba(200,210,228,0.85)", fontSize: 12 }} />
                  <YAxis tick={{ fill: "rgba(200,210,228,0.8)", fontSize: 11 }} />
                  <Tooltip
                    contentStyle={{ background: "rgba(15,20,30,0.95)", border: "1px solid rgba(90,110,140,0.5)" }}
                    formatter={(v: number) => [`${v}개`, "대여소 수"]}
                  />
                  <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                    {netFlowBars.map((b) => (
                      <Cell key={b.key} fill={NETFLOW_COLORS[b.key] ?? "rgba(148,163,184,0.85)"} />
                    ))}
                    <LabelList dataKey="count" position="top" fill="rgba(220,228,240,0.9)" fontSize={12} />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="hypo2-homogeneity__table-wrap" style={{ marginTop: 8 }}>
              <table className="geo-table">
                <thead>
                  <tr>
                    <th>집단</th>
                    <th className="num">대여소 수</th>
                    <th className="num">비중</th>
                    <th className="num">평균 순유입</th>
                    <th className="num">중앙값</th>
                    <th className="num">평균 Capa</th>
                  </tr>
                </thead>
                <tbody>
                  {data.net_flow.groups.map((g) => (
                    <tr key={g.key}>
                      <td className="label" style={{ color: NETFLOW_COLORS[g.key], fontWeight: 600 }}>
                        {NETFLOW_LABELS[g.key] ?? g.key}
                      </td>
                      <td className="num mono">{g.count}</td>
                      <td className="num mono">{(g.share * 100).toFixed(1)}%</td>
                      <td className="num mono">{fmtNum(g.mean_net_flow, 2)}</td>
                      <td className="num mono">{fmtNum(g.median_net_flow, 2)}</td>
                      <td className="num mono">{fmtNum(g.mean_capa, 1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          {/* ── 3단계: Capa 부족 검정 ── */}
          <section className="district-section" aria-label="3단계 Capa 부족 검정">
            <div className="district-section-head">
              <h3>3단계 · Capa 부족 검정 (회전율 기준)</h3>
              <p className="charts-meta">
                단측 t검정. H₀: E(시간당 대여+반납) ≤ Capa/4, H₁: &gt; Capa/4 (ITDP 일 6회 회전율의 시간 환산). 기각 시 「Capa
                부족」, 기각하지 않으면 「Capa 부족하지 않음」. 집단별 부족 비율을 봅니다.
              </p>
            </div>
            <div className="hypo2-homogeneity__table-wrap">
              <table className="geo-table">
                <thead>
                  <tr>
                    <th>순유입 집단</th>
                    <th className="num">대여소 수</th>
                    <th className="num">Capa 부족 수</th>
                    <th className="num">부족 비율</th>
                    <th className="num">평균 일유동/Capa</th>
                    <th className="num">중앙값 일유동/Capa</th>
                    <th className="num">평균 시간당 유동</th>
                    <th className="num">평균 Capa</th>
                  </tr>
                </thead>
                <tbody>
                  {data.capacity.by_group.map((g) => (
                    <tr key={g.group}>
                      <td className="label" style={{ color: NETFLOW_COLORS[g.group], fontWeight: 600 }}>
                        {NETFLOW_LABELS[g.group] ?? g.group}
                      </td>
                      <td className="num mono">{g.count}</td>
                      <td className="num mono">{g.issue_count}</td>
                      <td className="num mono">{g.issue_share == null ? "—" : `${(g.issue_share * 100).toFixed(1)}%`}</td>
                      <td className="num mono">{fmtNum(g.mean_ratio, 2)}</td>
                      <td className="num mono">{fmtNum(g.median_ratio, 2)}</td>
                      <td className="num mono">{fmtNum(g.mean_hourly_flow, 2)}</td>
                      <td className="num mono">{fmtNum(g.mean_capa, 1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="charts-meta" style={{ marginTop: 6 }}>
              세 집단 모두 부족 비율이 64~93%로 높아, 현 Capa가 실제 회전율을 못 따라가는 대여소가 많습니다.
            </p>
          </section>

          {/* ── 4단계: 6집단 분류 ── */}
          <section className="district-section" aria-label="4단계 6집단">
            <div className="district-section-head">
              <h3>4단계 · 6집단 분류 ((+/−/0) × Capa 부족 여부)</h3>
              <p className="charts-meta">
                순유입 3집단 × Capa 부족 2분류 = 6집단. 집단별로 다른 Capa 산정 방법을 적용하기 위한 기준표입니다. 총{" "}
                {data.six_group.total}개 대여소.
              </p>
            </div>
            <div className="chart-box">
              <ResponsiveContainer width="100%" height={300}>
                <BarChart data={sixBars} margin={{ top: 18, right: 12, bottom: 70, left: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(90,110,140,0.25)" />
                  <XAxis
                    dataKey="label"
                    tick={{ fill: "rgba(200,210,228,0.8)", fontSize: 10 }}
                    angle={-20}
                    textAnchor="end"
                    interval={0}
                    height={70}
                  />
                  <YAxis tick={{ fill: "rgba(200,210,228,0.8)", fontSize: 11 }} />
                  <Tooltip
                    contentStyle={{ background: "rgba(15,20,30,0.95)", border: "1px solid rgba(90,110,140,0.5)" }}
                    formatter={(v: number) => [`${v}개`, "대여소 수"]}
                  />
                  <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                    {sixBars.map((b, i) => (
                      <Cell key={b.label} fill={SIX_COLORS[i % SIX_COLORS.length]} />
                    ))}
                    <LabelList dataKey="count" position="top" fill="rgba(220,228,240,0.9)" fontSize={11} />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="hypo2-homogeneity__table-wrap" style={{ marginTop: 8 }}>
              <table className="geo-table">
                <thead>
                  <tr>
                    <th>6집단</th>
                    <th className="num">대여소 수</th>
                    <th className="num">평균 Capa</th>
                    <th className="num">중앙값 일유동/Capa</th>
                    <th className="num">평균 순유입</th>
                  </tr>
                </thead>
                <tbody>
                  {data.six_group.groups.map((g, i) => (
                    <tr key={g.label}>
                      <td className="label" style={{ color: SIX_COLORS[i % SIX_COLORS.length], fontWeight: 600 }}>
                        {formatSixGroupLabel(g.label)}
                      </td>
                      <td className="num mono">{g.count}</td>
                      <td className="num mono">{fmtNum(g.mean_capa, 1)}</td>
                      <td className="num mono">{fmtNum(g.median_ratio, 2)}</td>
                      <td className="num mono">{fmtNum(g.mean_net_flow, 2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      ) : null}
    </div>
  );
}
