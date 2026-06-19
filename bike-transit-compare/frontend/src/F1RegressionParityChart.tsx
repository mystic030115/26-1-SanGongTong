import { useMemo } from "react";
import {
  CartesianGrid,
  ComposedChart,
  Line,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { F1MultipleRegression } from "./api";
import { FACTOR_CHART_H, FACTOR_CHART_W, factorScatterChartMargin } from "./FactorScatterChart";

export type F1FitPoint = {
  gu: string;
  f1_actual: number;
  f1_predicted: number;
};

type Props = {
  regression: F1MultipleRegression;
  points: F1FitPoint[];
};

export default function F1RegressionParityChart({ regression, points }: Props) {
  const scatterPts = points.map((p) => ({
    gu: p.gu,
    f1: p.f1_actual,
    y: p.f1_predicted,
  }));

  const { xDomain, yDomain, parityLine } = useMemo(() => {
    const xs = scatterPts.map((p) => p.f1);
    const ys = scatterPts.map((p) => p.y);
    if (!xs.length) {
      return {
        xDomain: [0, 0.5] as [number, number],
        yDomain: [0, 0.5] as [number, number],
        parityLine: [] as { f1: number; y: number }[],
      };
    }
    const xLo = Math.min(...xs);
    const xHi = Math.max(...xs);
    const yLo = Math.min(...ys);
    const yHi = Math.max(...ys);
    const xPad = Math.max((xHi - xLo) * 0.08, 0.01);
    const yPad = Math.max((yHi - yLo) * 0.08, 0.01);
    const diagLo = Math.max(xLo, yLo);
    const diagHi = Math.min(xHi, yHi);
    const line =
      diagHi > diagLo + 1e-9
        ? [
            { f1: diagLo, y: diagLo },
            { f1: diagHi, y: diagHi },
          ]
        : [];
    return {
      xDomain: [xLo - xPad, xHi + xPad] as [number, number],
      yDomain: [yLo - yPad, yHi + yPad] as [number, number],
      parityLine: line,
    };
  }, [scatterPts]);

  const margin = factorScatterChartMargin();

  return (
    <article className="factor-scatter-card factor-scatter-card--parity">
      <header className="factor-scatter-card__head">
        <h3 className="factor-scatter-card__title">Lasso 다중회귀 적합 — 실측 F1 vs 예측 F1</h3>
        {regression.equation_ko || regression.equation ? (
          <p className="factor-scatter-card__eq factor-scatter-card__eq--linear">
            {regression.equation_ko ?? regression.equation}
          </p>
        ) : null}
        <p className="factor-scatter-card__meta mono">
          n={points.length}
          {regression.r2 != null ? <> · R²={Number(regression.r2).toFixed(3)}</> : null}
          {regression.rmse != null ? <> · RMSE={Number(regression.rmse).toFixed(4)}</> : null}
          {regression.mae != null ? <> · MAE={Number(regression.mae).toFixed(4)}</> : null}
        </p>
      </header>
      <p className="charts-meta" style={{ marginBottom: 8 }}>
        가로=실측, 세로=예측. 점 근처만 <span className="mono">y=x</span> 참고선(연한 회색).
      </p>
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
            tick={{ fill: "#b8c0d0", fontSize: 11 }}
            label={{ value: "실측 F1", position: "bottom", offset: 8, fill: "#9aa8bc", fontSize: 12 }}
          />
          <YAxis
            type="number"
            dataKey="y"
            domain={yDomain}
            tick={{ fill: "#b8c0d0", fontSize: 11 }}
            width={56}
            label={{ value: "예측 F1", angle: -90, position: "insideLeft", fill: "#9aa8bc", fontSize: 11 }}
          />
          <Tooltip
            cursor={{ strokeDasharray: "4 4", stroke: "rgba(148,163,184,0.5)" }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const p = payload[0]?.payload as { gu?: string; f1?: number; y?: number };
              const res =
                p.f1 != null && p.y != null && Number.isFinite(p.f1) && Number.isFinite(p.y) ? p.f1 - p.y : null;
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
                    실측: <strong>{p.f1?.toFixed(4)}</strong>
                  </div>
                  <div>
                    예측: <strong>{p.y?.toFixed(4)}</strong>
                  </div>
                  {res != null ? (
                    <div>
                      잔차: <strong>{res.toFixed(4)}</strong>
                    </div>
                  ) : null}
                </div>
              );
            }}
          />
          {parityLine.length >= 2 ? (
            <Line
              data={parityLine}
              type="linear"
              dataKey="y"
              stroke="rgba(148, 163, 184, 0.45)"
              strokeWidth={1.5}
              strokeDasharray="5 5"
              dot={false}
              isAnimationActive={false}
              legendType="none"
            />
          ) : null}
          <Scatter
            data={scatterPts}
            fill="#60a5fa"
            isAnimationActive={false}
            shape={(props: { cx?: number; cy?: number }) => {
              const { cx, cy } = props;
              if (cx == null || cy == null) return <g />;
              return <circle cx={cx} cy={cy} r={5} fill="#60a5fa" stroke="#93c5fd" strokeWidth={1.5} />;
            }}
          />
        </ComposedChart>
      </div>
    </article>
  );
}
