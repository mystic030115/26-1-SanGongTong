import { Component, type ErrorInfo, type ReactNode, useState } from "react";
import DistrictSavingsPanel from "./DistrictSavingsPanel";
import SupplyDemandPanel from "./SupplyDemandPanel";
import "./App.css";

type TabKey = "hypo1" | "hypo2";

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

export default function App() {
  const [tab, setTab] = useState<TabKey>("hypo1");

  return (
    <div className="app">
      <h1>따릉이 vs 대중교통</h1>
      <p className="sub">서울 25개 자치구 관내이동 데이터로 본 따릉이 효용(가설 1)과 대여소 수급 균형(가설 2).</p>

      <nav className="tabs" aria-label="메인 메뉴">
        <button
          type="button"
          className={tab === "hypo1" ? "tab active" : "tab"}
          onClick={() => setTab("hypo1")}
        >
          가설 1 · 따릉이 효용 (Depth/Coverage/F1)
        </button>
        <button
          type="button"
          className={tab === "hypo2" ? "tab active" : "tab"}
          onClick={() => setTab("hypo2")}
        >
          가설 2 · 수급 균형 (Capa/유동량)
        </button>
      </nav>

      <PanelErrorBoundary label="가설 1 · Depth/Coverage">
        <div className="panel charts-panel-wrap" hidden={tab !== "hypo1"}>
          <DistrictSavingsPanel />
        </div>
      </PanelErrorBoundary>

      <PanelErrorBoundary label="가설 2 · 수급 균형">
        <div className="panel charts-panel-wrap" hidden={tab !== "hypo2"}>
          <SupplyDemandPanel />
        </div>
      </PanelErrorBoundary>
    </div>
  );
}
