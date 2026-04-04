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
const EDGE_HUE = 118;
const EDGE_LIGHT = 52;

/** 승률은 선 굵기·불투명도와 무관하게 채도만 변화 (낮음=탁한 회녹, 높음=선명한 녹). */
function edgePathOptions(ratePct: number): L.PolylineOptions {
  const t = Math.max(0, Math.min(100, ratePct)) / 100;
  const saturation = 8 + t * 76;
  return {
    color: `hsl(${EDGE_HUE} ${saturation}% ${EDGE_LIGHT}%)`,
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

export default function MapPanel() {
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
          대여소는 점, 출발·도착 쌍은 선으로 연결됩니다. 선은{" "}
          <strong>비교 가능한 트립 중 따릉이가 더 빠른 비율</strong>이 높을수록
          선 색 채도만 진해지고, 굵기는 같습니다. 점·선에 마우스를 올리면 이름과
          승률을 볼 수 있습니다.
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
            const opts = edgePathOptions(e.rate_pct);
            return (
              <Polyline
                key={`${e.from_id}-${e.to_id}-${e.rate_pct}`}
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

      <div className="map-legend">
        <span className="map-legend-label">선 색 채도 (굵기 동일)</span>
        <div className="map-legend-bar" aria-hidden />
        <span className="map-legend-lo">낮음 (0%)</span>
        <span className="map-legend-hi">높음 (100%)</span>
      </div>
    </div>
  );
}
