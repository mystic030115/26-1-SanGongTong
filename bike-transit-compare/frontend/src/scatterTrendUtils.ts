/** F1·요인 산점도용 추세선·비선형 곡선 (클라이언트 적합) */

export type F1YPoint = { f1: number; y: number };

export function mean(xs: number[]): number {
  return xs.reduce((a, b) => a + b, 0) / xs.length;
}

/** F1 = β0 + β1·Y */
export function olsF1FromY(points: F1YPoint[]): { beta0: number; beta1: number; r2: number } | null {
  const n = points.length;
  if (n < 3) return null;
  const ys = points.map((p) => p.y);
  const fs = points.map((p) => p.f1);
  const my = mean(ys);
  const mf = mean(fs);
  let sxy = 0;
  let sxx = 0;
  let sst = 0;
  for (let i = 0; i < n; i++) {
    const dy = ys[i] - my;
    const df = fs[i] - mf;
    sxy += dy * df;
    sxx += dy * dy;
    sst += df * df;
  }
  if (sxx < 1e-15) return null;
  const beta1 = sxy / sxx;
  const beta0 = mf - beta1 * my;
  const sse = fs.reduce((acc, f, i) => acc + (f - (beta0 + beta1 * ys[i])) ** 2, 0);
  const r2 = sst > 1e-15 ? 1 - sse / sst : 0;
  return { beta0, beta1, r2 };
}

/** F1 = β0 + β1·Y + β2·Y² */
export function olsQuadraticF1FromY(points: F1YPoint[]): { beta0: number; beta1: number; beta2: number; r2: number } | null {
  const n = points.length;
  if (n < 5) return null;
  const ys = points.map((p) => p.y);
  const fs = points.map((p) => p.f1);
  const mf = mean(fs);
  // X = [1, y, y^2]
  let s00 = 0,
    s01 = 0,
    s02 = 0,
    s11 = 0,
    s12 = 0,
    s22 = 0;
  let t0 = 0,
    t1 = 0,
    t2 = 0;
  for (let i = 0; i < n; i++) {
    const y = ys[i];
    const y2 = y * y;
    const f = fs[i];
    s00 += 1;
    s01 += y;
    s02 += y2;
    s11 += y * y;
    s12 += y * y2;
    s22 += y2 * y2;
    t0 += f;
    t1 += f * y;
    t2 += f * y2;
  }
  const m = [
    [s00, s01, s02],
    [s01, s11, s12],
    [s02, s12, s22],
  ];
  const t = [t0, t1, t2];
  const beta = solve3x3(m, t);
  if (!beta) return null;
  const [beta0, beta1, beta2] = beta;
  const sse = fs.reduce((acc, f, i) => acc + (f - (beta0 + beta1 * ys[i] + beta2 * ys[i] ** 2)) ** 2, 0);
  const sst = fs.reduce((acc, f) => acc + (f - mf) ** 2, 0);
  const r2 = sst > 1e-15 ? 1 - sse / sst : 0;
  return { beta0, beta1, beta2, r2 };
}

function solve3x3(m: number[][], b: number[]): number[] | null {
  const a = m.map((row, i) => [...row, b[i]]);
  const n = 3;
  for (let col = 0; col < n; col++) {
    let piv = col;
    for (let r = col + 1; r < n; r++) if (Math.abs(a[r][col]) > Math.abs(a[piv][col])) piv = r;
    if (Math.abs(a[piv][col]) < 1e-12) return null;
    [a[col], a[piv]] = [a[piv], a[col]];
    for (let r = col + 1; r < n; r++) {
      const f = a[r][col] / a[col][col];
      for (let c = col; c <= n; c++) a[r][c] -= f * a[col][c];
    }
  }
  const x = [0, 0, 0];
  for (let r = n - 1; r >= 0; r--) {
    let s = a[r][n];
    for (let c = r + 1; c < n; c++) s -= a[r][c] * x[c];
    x[r] = s / a[r][r];
  }
  return x.every(Number.isFinite) ? x : null;
}

/**
 * F1 = β0 + β1·Y → 산점도(F1 가로 · Y 세로) 추세선.
 * Y(요인) 데이터 구간으로 f1=β0+β1·y 를 그림.
 * (F1 구간으로 y=(F1−β0)/β₁ 를 쓰면 β₁이 작을 때 y가 도메인 밖으로 나가 Recharts가 잘라 수직선처럼 보임)
 */
export function linearTrendSegmentInF1YPlane(
  beta0: number,
  beta1: number,
  points: F1YPoint[],
  f1Domain: [number, number] = [0, 0.5]
): F1YPoint[] | null {
  if (!Number.isFinite(beta0) || !Number.isFinite(beta1) || points.length < 2) {
    return null;
  }
  const ys = points.map((p) => p.y);
  const yLo = Math.min(...ys);
  const yHi = Math.max(...ys);
  const span = yHi - yLo;
  const pad = Math.max(span * 0.06, span * 0.02 || Math.abs(yHi) * 0.02 || 1);
  const ya = yLo - pad;
  const yb = yHi + pad;
  const [fLo, fHi] = f1Domain;

  const candidates: F1YPoint[] = [];
  const add = (y: number) => {
    if (!Number.isFinite(y)) return;
    const f1 = beta0 + beta1 * y;
    if (Number.isFinite(f1)) candidates.push({ f1, y });
  };
  add(ya);
  add(yb);
  if (Math.abs(beta1) > 1e-14) {
    add((fLo - beta0) / beta1);
    add((fHi - beta0) / beta1);
  }

  const inView = candidates.filter(
    (p) => p.f1 >= fLo - 1e-9 && p.f1 <= fHi + 1e-9 && p.y >= ya - 1e-6 && p.y <= yb + 1e-6
  );
  if (inView.length < 2) {
    const byY = [...candidates].sort((a, b) => a.y - b.y);
    if (byY.length >= 2) return [byY[0], byY[byY.length - 1]];
    return null;
  }
  inView.sort((a, b) => a.y - b.y);
  return [inView[0], inView[inView.length - 1]];
}

/** F1이 한곳에 몰릴 때 산점도 가로축 — [0,0.5] 고정이면 점·축이 왼쪽에 겹침 */
export function f1XDomainFromPoints(
  points: F1YPoint[],
  fixed: [number, number] = [0, 0.5],
  narrowSpan = 0.12
): [number, number] {
  if (points.length < 2) return fixed;
  const fs = points.map((p) => p.f1);
  const lo = Math.min(...fs);
  const hi = Math.max(...fs);
  const span = hi - lo;
  if (span >= narrowSpan) return fixed;
  const pad = Math.max(span * 0.12, 0.02);
  return [Math.max(fixed[0], lo - pad), Math.min(fixed[1], hi + pad)];
}

/** @deprecated 구간 필터 버전 — segment 사용 권장 */
export function linearTrendInF1YPlane(
  beta0: number,
  beta1: number,
  yRange: [number, number],
  n = 40
): F1YPoint[] | null {
  if (!Number.isFinite(beta0) || !Number.isFinite(beta1) || Math.abs(beta1) < 1e-12) return null;
  const f1s = linspace(0, 0.5, n);
  const pts = f1s.map((f1) => ({ f1, y: (f1 - beta0) / beta1 }));
  const [yLo, yHi] = yRange;
  const pad = (yHi - yLo) * 0.08 || 1;
  const filtered = pts.filter((p) => p.y >= yLo - pad && p.y <= yHi + pad);
  return filtered.length >= 2 ? filtered.sort((a, b) => a.f1 - b.f1) : null;
}

/** F1~Y OLS 기울기 β₁ 표기 */
export function formatSlopeBeta1(beta1: number): string {
  if (!Number.isFinite(beta1)) return "β₁ = —";
  const abs = Math.abs(beta1);
  const body = abs >= 1e-3 ? abs.toFixed(4) : abs >= 1e-6 ? abs.toFixed(6) : abs.toExponential(2);
  return `β₁ = ${beta1 < 0 ? "−" : ""}${body}`;
}

/** 2차식: f1 = b0 + b1·y + b2·y² → f1 그리드마다 실근 y */
export function quadraticTrendInF1YPlane(
  beta0: number,
  beta1: number,
  beta2: number,
  yRange: [number, number],
  n = 40
): F1YPoint[] | null {
  const [yLo, yHi] = yRange;
  const pad = (yHi - yLo) * 0.08 || 1;
  const out: F1YPoint[] = [];
  for (const f1 of linspace(0, 0.5, n)) {
    const c = beta0 - f1;
    let y: number | null = null;
    if (Math.abs(beta2) < 1e-14) {
      if (Math.abs(beta1) > 1e-14) y = -c / beta1;
    } else {
      const disc = beta1 * beta1 - 4 * beta2 * c;
      if (disc < 0) continue;
      const sd = Math.sqrt(disc);
      const r1 = (-beta1 + sd) / (2 * beta2);
      const r2 = (-beta1 - sd) / (2 * beta2);
      const cands = [r1, r2].filter((v) => Number.isFinite(v) && v >= yLo - pad && v <= yHi + pad);
      if (!cands.length) continue;
      y = cands.reduce((best, v) => (Math.abs(v - mean([yLo, yHi])) < Math.abs(best - mean([yLo, yHi])) ? v : best));
    }
    if (y != null && Number.isFinite(y)) out.push({ f1, y });
  }
  return out.length >= 2 ? out.sort((a, b) => a.f1 - b.f1) : null;
}

/** LOESS: 가로 F1 → 세로 Y (비유의 요인 스무딩) */
export function loessF1ToY(points: F1YPoint[], span = 0.65, nGrid = 32): F1YPoint[] | null {
  const n = points.length;
  if (n < 5) return null;
  const sorted = [...points].sort((a, b) => a.f1 - b.f1);
  const fMin = sorted[0].f1;
  const fMax = sorted[n - 1].f1;
  if (fMax - fMin < 1e-9) return null;
  const bandwidth = Math.max(span * (fMax - fMin), (fMax - fMin) * 0.25);
  const grid = linspace(fMin, fMax, nGrid);
  const curve: F1YPoint[] = [];
  for (const x0 of grid) {
    const wts: number[] = [];
    let wSum = 0;
    for (const p of sorted) {
      const u = Math.abs(p.f1 - x0) / bandwidth;
      const w = u >= 1 ? 0 : (1 - u ** 3) ** 3;
      wts.push(w);
      wSum += w;
    }
    if (wSum < 1e-9) continue;
    let sw = 0,
      swx = 0,
      swy = 0,
      swxx = 0,
      swxy = 0;
    for (let i = 0; i < n; i++) {
      const w = wts[i];
      const x = sorted[i].f1;
      const y = sorted[i].y;
      sw += w;
      swx += w * x;
      swy += w * y;
      swxx += w * x * x;
      swxy += w * x * y;
    }
    const det = sw * swxx - swx * swx;
    if (Math.abs(det) < 1e-12) continue;
    const b1 = (sw * swxy - swx * swy) / det;
    const b0 = (swy - b1 * swx) / sw;
    curve.push({ f1: x0, y: b0 + b1 * x0 });
  }
  return curve.length >= 2 ? curve : null;
}

function linspace(a: number, b: number, n: number): number[] {
  if (n < 2) return [a];
  const out: number[] = [];
  for (let i = 0; i < n; i++) out.push(a + ((b - a) * i) / (n - 1));
  return out;
}

export function yRangeFromPoints(points: F1YPoint[]): [number, number] {
  const ys = points.map((p) => p.y);
  return [Math.min(...ys), Math.max(...ys)];
}

/** F1 = β0 + β1·Y (Y = 요인 라벨) */
export function formatLinearEquation(labelY: string, beta0: number, beta1: number): string {
  const sign = beta1 >= 0 ? "+" : "−";
  return `F1 = ${beta0.toFixed(4)} ${sign} (${Math.abs(beta1).toFixed(6)})·${labelY}`;
}

export function formatQuadraticEquation(labelY: string, beta0: number, beta1: number, beta2: number): string {
  const s1 = beta1 >= 0 ? "+" : "−";
  const s2 = beta2 >= 0 ? "+" : "−";
  return (
    `F1 = ${beta0.toFixed(4)} ${s1} (${Math.abs(beta1).toFixed(6)})·${labelY} ` +
    `${s2} (${Math.abs(beta2).toFixed(6)})·${labelY}²`
  );
}
