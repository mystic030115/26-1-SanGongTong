# bike-transit-compare

자전거(따릉이) 이동 데이터와 대중교통(TMAP) 경로·시간을 비교하는 프로젝트입니다.

**두 가지 사용 방식이 있습니다.**

| 방식 | 용도 |
|------|------|
| **Python 배치** | `trips.csv` 전체를 돌려 `transit_pairs.csv` 캐시와 `trips_with_transit.xlsx`를 한 번에 생성 |
| **웹 대시보드** | 브라우저에서 출발·도착 대여소를 골라 조회하고, 전역 통계를 봄 (API + React) |

---

## 처음부터 다시 세팅하기

아래는 저장소를 새로 받았거나, 가상환경·의존성을 처음부터 맞출 때의 순서입니다.

### 1. 저장소·Python 가상환경

```bash
cd bike-transit-compare
python3 -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt`에는 `#`로 시작하는 주석 줄을 넣지 마세요. `pip install -r`은 한 줄에 패키지 이름만 있어야 합니다.

### 2. API 키

프로젝트 루트에 `.env` 파일을 만들고 TMAP 앱 키를 넣습니다.

```env
TMAP_APP_KEY=여기에_키
```

### 3. 원시 데이터

다음 파일이 있어야 배치·웹이 동작합니다.

- `data/raw/trips.csv`
- `data/raw/stations.xlsx`

없으면 배치나 API의 `load_data()` 단계에서 실패합니다.

### 4. 프런트엔드 (Node.js)

[Node.js](https://nodejs.org/) 설치 후:

```bash
cd frontend
npm install
```

**주의:** 셸에서 디렉터리 이동과 `npm` 명령 사이에 **줄바꿈(또는 `;`)** 을 넣으세요.  
`cd .../frontendnpm install` 처럼 붙이면 잘못된 경로로 이동합니다.

### 5. 동작 확인

- **배치 한 번** (선택): 아래 「Python 배치」 절 참고.
- **웹**: 터미널 두 개에서 API(8000)와 Vite(5173)를 띄운 뒤 브라우저에서 http://localhost:5173 을 엽니다.

---

## 공통 준비 (요약)

1. `python3 -m venv .venv && source .venv/bin/activate`
2. `pip install -r requirements.txt`
3. `.env`에 `TMAP_APP_KEY=...`
4. `data/raw/`에 `trips.csv`, `stations.xlsx`
5. 웹 UI 사용 시: `cd frontend && npm install`

---

## 1) Python 배치 (원래 방식)

프로젝트 루트에서:

```bash
source .venv/bin/activate
python -m src.run
```

또는:

```bash
chmod +x scripts/run_batch.sh
./scripts/run_batch.sh
```

- 결과: `data/cache/transit_pairs.csv`, `data/output/trips_with_transit.xlsx`
- 배치가 끝나면 **`data/output/last_run_summary.json`** 에 마지막 실행 요약(트립 행 수, 캐시 행 수, `build_pair_cache` 요약 등)이 덮어쓰기 저장됩니다.
- 자주 쓰는 환경 변수: `TOP_OD_PAIRS`, `MAX_OD_PAIRS`, `TRIPS_MAX_ROWS`
- `trips.csv`를 구간만 남기고 덮어쓰기: `python -m src.run --rewrite-trips`

---

## 2) 웹 대시보드 (React + FastAPI)

### 터미널 A — API (포트 8000)

```bash
cd bike-transit-compare    # 클론한 프로젝트 루트
source .venv/bin/activate
python -m uvicorn src.web_api:app --reload --host 127.0.0.1 --port 8000
```

또는 `./scripts/run_web_api.sh`  
(`run_frontend.sh`는 실행 전에 `npm install`을 한 번 돌립니다.)

### 터미널 B — 프런트 (포트 5173)

```bash
cd frontend
npm install
npm run dev
```

또는 `./scripts/run_frontend.sh`

- 브라우저: http://localhost:5173  
- UI 탭 **「통계 차트」**: `GET /api/charts/summary`
- UI 탭 **「임계 승률」**: `GET /api/od-threshold/summary?threshold_pct=50`
- UI 탭 **「Map」**: 대여소(점)와 출발·도착 쌍 연결선(승률이 높을수록 색 채도만 진해지고 굵기는 동일), `GET /api/map/graph?min_comparable=3&max_edges=700`
- API 직접: http://127.0.0.1:8000/docs , http://127.0.0.1:8000/api/health

### 포트 8000을 쓰는 프로세스 끄고 다시 켜기

다른 터미널에서 API를 이미 띄워 두었거나, 예전 `uvicorn`이 남아 있으면 포트가 막힙니다.

**macOS / Linux — 무엇이 8000을 쓰는지 확인**

```bash
lsof -iTCP:8000 -sTCP:LISTEN
```

`PID` 열에 나온 숫자가 프로세스 ID입니다.

**종료**

```bash
kill PID
```

응답이 없으면:

```bash
kill -9 PID
```

**Windows (PowerShell)**

```powershell
netstat -ano | findstr :8000
```

마지막 열이 PID입니다.

```powershell
taskkill /PID 번호 /F
```

종료 후 다시 터미널 A에서 `uvicorn` 또는 `./scripts/run_web_api.sh` 로 띄우면 됩니다.  
`--reload` 옵션을 켜 두면 `src/web_api.py` 등을 저장할 때 자동으로 다시 로드되지만, **가끔은 프로세스를 완전히 끄고 다시 켜는 것**이 안전합니다.

---

## 로그·파일로 남는 것

프론트를 켜지 않아도 백엔드·배치만으로 아래에 기록이 쌓입니다.

| 위치 | 내용 |
|------|------|
| `data/logs/journal_YYYY-MM-DD.jsonl` | 하루 한 파일, **JSON 한 줄당 이벤트** (TMAP HTTP 결과·요청/HTTP 오류, `build_pair_cache` 완료, 웹에서 출발·도착 조회 시 TMAP 호출, 상위 N 배치 갱신 등) |
| `data/output/last_run_summary.json` | `python -m src.run` 이 **정상 종료될 때마다** 마지막 실행 요약 |
| `data/cache/tmap_usage.json` | KST 기준 당일 TMAP HTTP 호출 누적(자정 리셋) |
| `data/cache/transit_pairs.csv` | 출발·도착 쌍별 대중교통 캐시 |
| `data/output/trips_with_transit.xlsx` | 배치 병합 결과 |

`.gitignore`에 `data/logs/` 와 `tmap_usage.json` 을 넣어 두었습니다. 로그를 저장소에 올리고 싶으면 `.gitignore`에서 해당 줄을 지우면 됩니다.

---

## 디렉터리 구조

- `data/raw/` — `trips.csv`, `stations.xlsx`
- `data/cache/` — `transit_pairs.csv`, `tmap_usage.json`
- `data/logs/` — `journal_*.jsonl`
- `data/output/` — `trips_with_transit.xlsx`, `last_run_summary.json`
- `src/run.py` — 배치 파이프라인
- `src/web_api.py` — 로컬 HTTP API
- `src/app_journal.py` — 일지·요약 파일 기록
- `src/tmap_usage.py` — 일별 TMAP 호출 카운트
- `frontend/` — Vite + React + TypeScript UI (`MapPanel.tsx` — Leaflet 지도 탭)
- `scripts/` — `run_batch.sh`, `run_web_api.sh`, `run_frontend.sh`
