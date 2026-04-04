import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CircleMarker,
  MapContainer,
  Polyline,
  TileLayer,
  Tooltip,
  useMap,
} from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { fetchMapGraph, type MapGraphEdge, type MapGraphNode } from "./api";
import "./MapPanel.css";

const SEOUL: [number, number] = [37.565, 126.985];

function applyJitter(nodes: MapGraphNode[]): MapGraphNode[] {
  const groups = new Map<string, MapGraphNode[]>();
  for (const n of nodes) {
    const key = `${n.lat.toFixed(5)}_${n.lon.toFixed(5)}`;
    let g = groups.get(key);
    if (!g) {
      g = [];
      groups.set(key, g);
    }
    g.push(n);
  }
  const out: MapGraphNode[] = [];
  for (const g of groups.values()) {
    if (g.length === 1) {
      out.push(g[0]);
      continue;
    }
    g.forEach((n, i) => {
      const ang = (2 * Math.PI * i) / g.length;
      const r = 0.0002;
      out.push({
        ...n,
        lat: n.lat + r * Math.cos(ang),
        lon: n.lon + r * Math.sin(ang),
      });
    });
  }
  return out;
}

function curvePositions(
  a: [number, number],
  b: [number, number],
  bendSign: number
): [number, number][] {
  const dLat = b[0] - a[0];
  const dLon = b[1] - a[1];
  const len = Math.hypot(dLat, dLon) || 1e-9;
  const perpLat = -dLon / len;
  const perpLon = dLat / len;
  const midLat = (a[0] + b[0]) / 2;
  const midLon = (a[1] + b[1]) / 2;
  const scale = Math.min(1, len / 0.025) * 0.00014 * bendSign;
  return [
    a,
    [midLat + perpLat * scale, midLon + perpLon * scale],
    b,
  ];
}

const EDGE_WEIGHT = 2;
const EDGE_OPACITY = 0.92;

/**
 * 임계 승률 탭과 동일 기준: 승률 > threshold → 따릉이 유리(회색, 높을수록 진함),
 * 이하 → 대중교통 유리 쪽(초록, 승률이 낮을수록 진함).
 */
function edgePathOptions(ratePct: number, thresholdPct: number): L.PolylineOptions {
  const r = Math.max(0, Math.min(100, ratePct));
  const thr = Math.max(0, Math.min(100, thresholdPct));
  let color: string;

  if (r > thr) {
    const span = Math.max(100 - thr, 0.001);
    const u = Math.max(0, Math.min(1, (r - thr) / span));
    const L = 58 - u * 24;
    const S = 6 + u * 16;
    color = `hsl(218 ${S}% ${L}%)`;
  } else {
    const span = Math.max(thr, 0.001);
    const u = Math.max(0, Math.min(1, (thr - r) / span));
    const L = 52 - u * 22;
    const S = 28 + u * 48;
    color = `hsl(134 ${S}% ${L}%)`;
  }

  return {
    color,
    weight: EDGE_WEIGHT,
    opacity: EDGE_OPACITY,
    lineCap: "round",
    lineJoin: "round",
  };
}

function FitBounds({ points }: { points: [number, number][] }) {
  const map = useMap();
  useEffect(() => {
    if (points.length === 0) return;
    const b = L.latLngBounds(points);
    map.fitBounds(b, { padding: [52, 52], maxZoom: 15, animate: true });
  }, [map, points]);
  return null;
}

function MapResizeSync() {
  const map = useMap();
  useEffect(() => {
    const id = window.setTimeout(() => map.invalidateSize(), 120);
    return () => window.clearTimeout(id);
  }, [map]);
  return null;
}

type MapPanelProps = {
  /** 임계 승률 탭에서 「적용」한 값과 맞춤(기본 50). */
  thresholdPct?: number;
};

export default function MapPanel({ thresholdPct = 50 }: MapPanelProps) {
  const [minComp, setMinComp] = useState(3);
  const [maxEdges, setMaxEdges] = useState(700);
  const [data, setData] = useState<Awaited<
    ReturnType<typeof fetchMapGraph>
  > | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    setErr(null);
    fetchMapGraph(minComp, maxEdges)
      .then(setData)
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }, [minComp, maxEdges]);

  useEffect(() => {
    load();
  }, [load]);

  const jittered = useMemo(
    () => (data?.nodes ? applyJitter(data.nodes) : []),
    [data?.nodes]
  );

  const posById = useMemo(() => {
    const m = new Map<string, [number, number]>();
    for (const n of jittered) {
      m.set(n.id, [n.lat, n.lon]);
    }
    return m;
  }, [jittered]);

  const boundsPoints = useMemo(
    () => jittered.map((n) => [n.lat, n.lon] as [number, number]),
    [jittered]
  );

  const edges = data?.edges ?? [];

  return (
    <div className="map-panel-root">
      <div className="map-toolbar">
        <p className="map-intro">
          대여소는 점, 출발·도착 쌍은 선으로 연결됩니다. 임계 승률 탭에서 적용한{" "}
          <strong>{thresholdPct}%</strong>를 기준으로, 승률이 그보다{" "}
          <strong>크면 회색</strong>(높을수록 진함 · 따릉이 유리),{" "}
          <strong>이하이면 초록</strong>(낮을수록 진함 · 대중교통 유리 쪽)입니다.
          굵기는 같습니다. 점·선에 마우스를 올리면 이름과 승률을 볼 수 있습니다.
        </p>
        <div className="map-controls">
          <label className="map-field">
            <span>최소 비교 트립 수</span>
            <input
              type="number"
              min={1}
              max={500}
              value={minComp}
              onChange={(e) => setMinComp(Number(e.target.value) || 1)}
            />
          </label>
          <label className="map-field">
            <span>최대 선 개수</span>
            <input
              type="number"
              min={50}
              max={3000}
              step={50}
              value={maxEdges}
              onChange={(e) => setMaxEdges(Number(e.target.value) || 50)}
            />
          </label>
          <button type="button" className="btn map-reload" onClick={load}>
            {loading ? "불러오는 중…" : "다시 불러오기"}
          </button>
        </div>
        {data?.meta && (
          <p className="map-meta mono">
            표시 중인 연결(선): {data.meta.edge_count}개 (비교 가능 ≥{" "}
            {data.meta.min_comparable}건, 상위 {data.meta.max_edges}개까지)
          </p>
        )}
      </div>

      {err && <p className="err">{err}</p>}

      <div className="map-leaflet-host">
        <MapContainer
          center={SEOUL}
          zoom={12}
          className="map-leaflet-map"
          scrollWheelZoom
          attributionControl
          zoomSnap={0.5}
          zoomDelta={0.5}
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>'
            url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
            subdomains="abcd"
            maxZoom={20}
          />
          <MapResizeSync />
          {boundsPoints.length > 0 ? (
            <FitBounds points={boundsPoints} />
          ) : null}

          {edges.map((e: MapGraphEdge) => {
            const a = posById.get(e.from_id);
            const b = posById.get(e.to_id);
            if (!a || !b) return null;
            const bend = e.from_id < e.to_id ? 1 : -1;
            const positions = curvePositions(a, b, bend);
            const opts = edgePathOptions(e.rate_pct, thresholdPct);
            return (
              <Polyline
                key={`${e.from_id}-${e.to_id}-${e.rate_pct}-${thresholdPct}`}
                positions={positions}
                pathOptions={opts}
              >
                <Tooltip
                  sticky
                  direction="top"
                  className="map-leaflet-tooltip map-edge-tooltip"
                >
                  <div className="map-tip-block">
                    <div className="map-tip-line mono">
                      {e.from_id} → {e.to_id}
                    </div>
                    <div className="map-tip-sub">
                      {e.from_name} → {e.to_name}
                    </div>
                    <div className="map-tip-rate">
                      따릉이 더 빠른 비율{" "}
                      <strong>{e.rate_pct.toFixed(1)}%</strong>
                      <span className="map-tip-sub">
                        {" "}
                        (임계 {thresholdPct}%{" "}
                        {e.rate_pct > thresholdPct ? "초과 · 회색" : "이하 · 초록"})
                      </span>
                    </div>
                    <div className="map-tip-sub mono">
                      비교 가능 {e.comparable}건 / 이 쌍 전체 트립{" "}
                      {e.total_trips}건
                    </div>
                  </div>
                </Tooltip>
              </Polyline>
            );
          })}

          {jittered.map((n: MapGraphNode) => (
            <CircleMarker
              key={n.id}
              center={[n.lat, n.lon]}
              radius={4}
              pathOptions={{
                color: "#7dd3fc",
                weight: 1,
                fillColor: "#38bdf8",
                fillOpacity: 0.9,
              }}
            >
              <Tooltip
                sticky
                direction="top"
                className="map-leaflet-tooltip map-node-tooltip"
              >
                <div className="map-tip-block">
                  <div className="map-tip-line mono">{n.id}</div>
                  <div className="map-tip-name">{n.name}</div>
                </div>
              </Tooltip>
            </CircleMarker>
          ))}
        </MapContainer>
      </div>

      <div className="map-legend map-legend-split">
        <span className="map-legend-label">
          선 색 · 임계 {thresholdPct}% (임계 승률 탭 「적용」과 동일)
        </span>
        <div className="map-legend-bars" aria-hidden>
          <div
            className="map-legend-swatch map-legend-swatch-green"
            title="임계 이하: 승률 낮을수록 진한 초록"
          />
          <div
            className="map-legend-swatch map-legend-swatch-grey"
            title="임계 초과: 승률 높을수록 진한 회색"
          />
        </div>
        <span className="map-legend-cap map-legend-cap-l">
          초록 = 임 ≤ {thresholdPct}% · 낮은 승률일수록 진함
        </span>
        <span className="map-legend-cap map-legend-cap-r">
          회색 = 임 &gt; {thresholdPct}% · 높은 승률일수록 진함
        </span>
      </div>
    </div>
  );
}
