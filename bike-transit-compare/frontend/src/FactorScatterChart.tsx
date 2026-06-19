import type { ReactNode } from "react";
import {
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { yDomainFromScatterPoints } from "./scatterChartOverlay";
import { f1XDomainFromPoints, formatSlopeBeta1, type F1YPoint } from "./scatterTrendUtils";
import { F1_MEANINGFUL } from "./hypothesis1Thresholds";

export const FACTOR_CHART_W = 400;
export const FACTOR_CHART_H = 320;

/** Y축 라벨 폭 */
export const SCATTER_Y_AXIS_WIDTH = 56;

export function factorScatterChartMargin() {
  return { top: 16, right: 20, bottom: 36, left: SCATTER_Y_AXIS_WIDTH + 2 };
}

export type FactorScatterPoint = { gu: string; f1: number; y: number };

type Props = {
  title: string;
  yAxisLabel: string;
  points: FactorScatterPoint[];
  equationText?: string | null;
  /** F1 = β0 + β1·Y 의 β1 */
  slopeBeta1?: number | null;
  trendSegment?: F1YPoint[] | null;
  metaLine?: ReactNode;
  footer?: ReactNode;
};

export default function FactorScatterChart({
  title,
  yAxisLabel,
  points,
  equationText,
  slopeBeta1,
  trendSegment,
  metaLine,
  footer,
}: Props) {
  const f1y = points.map((p) => ({ f1: p.f1, y: p.y }));
  const yDomain = yDomainFromScatterPoints(f1y);
  const xDomain = f1XDomainFromPoints(f1y);
  const margin = factorScatterChartMargin();
  const trendData =
    trendSegment && trendSegment.length >= 2
      ? trendSegment.map((p) => ({ f1: p.f1, y: p.y }))
      : [];

  return (
    <article className="factor-scatter-card factor-scatter-card--linear">
      <header className="factor-scatter-card__head">
        <h3 className="factor-scatter-card__title">{title}</h3>
        {metaLine ? <p className="factor-scatter-card__meta mono">{metaLine}</p> : null}
        {equationText ? (
          <p className="factor-scatter-card__eq factor-scatter-card__eq--linear" title="OLS F1 ~ Y">
            {equationText}
          </p>
        ) : null}
      </header>
      <div
        className="factor-scatter-chart-wrap"
        style={{ width: FACTOR_CHART_W, height: FACTOR_CHART_H, minWidth: FACTOR_CHART_W, minHeight: FACTOR_CHART_H }}
      >
        <ComposedChart width={FACTOR_CHART_W} height={FACTOR_CHART_H} margin={margin}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(148, 163, 184, 0.35)" />
          <XAxis
            type="number"
            dataKey="f1"
            domain={xDomain}
            tickCount={5}
            tickFormatter={(v: number) => Number(v).toFixed(2)}
            scale="linear"
            allowDataOverflow
            tick={{ fill: "#b8c0d0", fontSize: 11 }}
            label={{ value: "F1 Score", position: "bottom", offset: 8, fill: "#9aa8bc", fontSize: 12 }}
          />
          <YAxis
            type="number"
            dataKey="y"
            domain={yDomain}
            scale="linear"
            allowDataOverflow
            tick={{ fill: "#b8c0d0", fontSize: 11 }}
            width={SCATTER_Y_AXIS_WIDTH}
            label={{ value: yAxisLabel, angle: -90, position: "insideLeft", fill: "#9aa8bc", fontSize: 10 }}
          />
          <Tooltip
            cursor={{ strokeDasharray: "4 4", stroke: "rgba(148,163,184,0.5)" }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const p = payload[0]?.payload as FactorScatterPoint;
              return (
                <div
                  style={{
                    background: "#1a2230",
                    border: "1px solid #2a3344",
                    borderRadius: 8,
                    padding: "8px 10px",
                    fontSize: 12,
                  }}
                >
                  <div style={{ fontWeight: 700, marginBottom: 4 }}>{p.gu}</div>
                  <div>
                    F1: <strong>{p.f1.toFixed(3)}</strong>
                  </div>
                  <div>
                    {yAxisLabel}: <strong>{p.y.toLocaleString()}</strong>
                  </div>
                </div>
              );
            }}
          />
          <ReferenceLine x={F1_MEANINGFUL} stroke="#fbbf24" strokeDasharray="5 5" />
          {trendData.length >= 2 ? (
            <Line
              data={trendData}
              type="linear"
              dataKey="y"
              stroke="rgba(248, 113, 113, 0.95)"
              strokeWidth={2.5}
              dot={false}
              isAnimationActive={false}
              legendType="none"
            />
          ) : null}
          <Scatter
            data={points}
            fill="#60a5fa"
            stroke="#1e3a5f"
            strokeWidth={1}
            isAnimationActive={false}
            shape={(props: { cx?: number; cy?: number }) => {
              const { cx, cy } = props;
              if (cx == null || cy == null) return <g />;
              return <circle cx={cx} cy={cy} r={5} fill="#60a5fa" stroke="#93c5fd" strokeWidth={1.5} />;
            }}
          />
        </ComposedChart>
        {slopeBeta1 != null && Number.isFinite(slopeBeta1) ? (
          <div className="factor-scatter-slope-badge" aria-label="회귀 기울기">
            {formatSlopeBeta1(slopeBeta1)}
          </div>
        ) : null}
      </div>
      {points.length === 0 ? (
        <p className="err" style={{ marginTop: 8, fontSize: "0.85rem" }}>
          점 0개 — 요인 CSV·구 이름 매칭을 확인하세요.
        </p>
      ) : (
        <p className="charts-meta" style={{ marginTop: 4, fontSize: "0.75rem" }}>
          {points.length}개 구 · <span style={{ color: "rgba(248, 113, 113, 0.95)" }}>빨간선 = OLS 추세</span>
        </p>
      )}
      {footer}
    </article>
  );
}
