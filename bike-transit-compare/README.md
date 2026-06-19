# bike-transit-compare

서울 관내이동 데이터로 **따릉이 vs 대중교통**을 비교하고, **가설 1(효용)** · **가설 2(수급 균형)** 결과를 웹 대시보드로 보는 프로젝트입니다.

---

## 빠른 실행 (가설 1·2 대시보드)

**필요한 것:** Python 3.9+, Node.js 18+

```bash
git clone https://github.com/mystic030115/26-1-SanGongTong.git
cd 26-1-SanGongTong/bike-transit-compare

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cd frontend && npm install && cd ..
```

**터미널 1 — API**

```bash
cd bike-transit-compare
source .venv/bin/activate
python -m uvicorn src.web_api:app --host 127.0.0.1 --port 8000
```

**터미널 2 — 프론트**

```bash
cd bike-transit-compare/frontend
npm run dev
```

브라우저에서 **http://localhost:5173** 을 엽니다.

| 탭 | 내용 |
|---|---|
| **가설 1** | Depth / Coverage / F1, 구별 히트맵, 요인 상관, 대여 소요시간 보정(기본 +0.5분) |
| **가설 2** | Capa·유동량 4단계 분석 (상관 → 순유입 검정 → Capa 부족 → 6집단) |

> TMAP API 키 없이도 **레포에 포함된 캐시·데이터**로 대시보드를 볼 수 있습니다.  
> 가설 1 **대여 소요시간 슬라이더(+0.5분)** 도 `관내이동_시간_거리/` CSV(25개 구)가 레포에 포함되어 있습니다.

### 관내이동 CSV (레포 포함)

`bike-transit-compare/관내이동_시간_거리/`에 구별 `*_시간_거리.csv` 25개가 들어 있습니다. clone만 하면 가설 1 borrow 보정·재계산이 동작합니다.

```bash
bike-transit-compare/관내이동_시간_거리/강남구_시간_거리.csv
# … 25개 구

# 다른 경로에 두었을 때만 API 실행 전에 지정
export OD_DISTRICT_DIR="/경로/관내이동_시간_거리"
```

처음 API 기동 후 가설 1(+0.5분) 재계산은 **1~2분** 걸릴 수 있고, 이후에는 캐시로 빨라집니다.

---

## 상세 설정

### Python 가상환경

```bash
cd bike-transit-compare
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### API 키 (TMAP 신규 조회 시만)

출발·도착 조회나 TMAP 캐시 **추가 수집**을 할 때만 필요합니다. 대시보드 열람만이면 생략 가능합니다.

```env
# bike-transit-compare/.env
TMAP_APP_KEY=여기에_키
```

### 원시 데이터 (배치 파이프라인용)

`python -m src.run` 배치를 돌릴 때만 필요합니다.

- `data/raw/trips.csv`
- `data/raw/stations.xlsx`

---

## Python 배치

```bash
source .venv/bin/activate
python -m src.run
```

또는 `./scripts/run_batch.sh`

- 결과: `data/cache/transit_pairs.csv`, `data/output/trips_with_transit.xlsx`
- 요약: `data/output/last_run_summary.json`

---

## 포트 충돌 시 (8000)

```bash
lsof -iTCP:8000 -sTCP:LISTEN   # PID 확인
kill PID                        # 안 되면 kill -9 PID
```

---

## 디렉터리 구조

| 경로 | 설명 |
|---|---|
| `frontend/` | React 대시보드 (가설 1·2) |
| `src/web_api.py` | FastAPI 백엔드 |
| `data/cache/tmap_by_district/` | 구별 TMAP 캐시 (대시보드용) |
| `data/supply/` | 가설 2 수급 분석 입력 CSV |
| `data/factors/` | 가설 1 외부 요인 데이터 |
| `관내이동_시간_거리/` | 구별 OD CSV 25개 (가설 1 borrow 재계산) |
| `frontend/public/district_savings.json` | 가설 1 구별 Depth/Coverage/F1 |
| `scripts/` | `run_web_api.sh`, `run_frontend.sh`, `run_batch.sh` |

---

## 로그·캐시 (참고)

| 위치 | 내용 |
|---|---|
| `data/logs/journal_*.jsonl` | API·배치 이벤트 로그 |
| `data/cache/transit_pairs.csv` | 출발·도착 쌍별 대중교통 캐시 |
| `data/output/trips_with_transit.xlsx` | 배치 병합 결과 |
