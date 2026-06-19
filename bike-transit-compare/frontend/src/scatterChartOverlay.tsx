import type { F1YPoint } from "./scatterTrendUtils";

export type TrendOverlayLine = {
  points: F1YPoint[];
  stroke: string;
  strokeWidth?: number;
  dash?: string;
};

export const SCATTER_CHART_MARGIN = { top: 16, right: 20, bottom: 36, left: 52 };

export function yDomainFromScatterPoints(
  points: F1YPoint[],
  padRatio = 0.08
): [number, number] {
  if (!points.length) return [0, 1];
  const ys = points.map((p) => p.y);
  const lo = Math.min(...ys);
  const hi = Math.max(...ys);
  const pad = (hi - lo) * padRatio || Math.max(Math.abs(hi) * 0.05, 1);
  return [lo - pad, hi + pad];
}

/** Recharts margin·domain과 맞춘 SVG polyline 좌표 */
export function polylinePointsInChart(
  curve: F1YPoint[],
  width: number,
  height: number,
  margin = SCATTER_CHART_MARGIN,
  xDomain: [number, number] = [0, 0.5],
  yDomain: [number, number]
): string {
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;
  const [x0, x1] = xDomain;
  const [y0, y1] = yDomain;
  const xSpan = x1 - x0 || 1;
  const ySpan = y1 - y0 || 1;
  return curve
    .filter((p) => Number.isFinite(p.f1) && Number.isFinite(p.y))
    .map((p) => {
      const px = margin.left + ((p.f1 - x0) / xSpan) * plotW;
      const py = margin.top + plotH - ((p.y - y0) / ySpan) * plotH;
      return `${px},${py}`;
    })
    .join(" ");
}

/** Recharts ComposedChart 위 SVG 추세선 (f1·y 숫자축 전용) */
export function ChartTrendOverlay({
  width,
  height,
  yDomain,
  lines,
  xDomain = [0, 0.5] as [number, number],
  margin = SCATTER_CHART_MARGIN,
}: {
  width: number;
  height: number;
  yDomain: [number, number];
  lines: TrendOverlayLine[];
  xDomain?: [number, number];
  margin?: typeof SCATTER_CHART_MARGIN;
}) {
  return (
    <svg
      className="factor-scatter-overlay"
      width={width}
      height={height}
      style={{ position: "absolute", left: 0, top: 0, pointerEvents: "none" }}
      aria-hidden
    >
      {lines.map((ln, i) =>
        ln.points.length >= 2 ? (
          <polyline
            key={i}
            points={polylinePointsInChart(ln.points, width, height, margin, xDomain, yDomain)}
            fill="none"
            stroke={ln.stroke}
            strokeWidth={ln.strokeWidth ?? 2.5}
            strokeDasharray={ln.dash}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        ) : null
      )}
    </svg>
  );
}
