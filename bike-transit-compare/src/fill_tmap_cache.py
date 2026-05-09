from __future__ import annotations

import argparse
import csv
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Set, Tuple

import pandas as pd

from .run import fetch_transit_time


def _clean_header(s: str) -> str:
    return (s or "").replace("\ufeff", "").strip()


def _pick_field(fieldnames: Iterable[str], want: str) -> Optional[str]:
    # handle BOM/whitespace
    m = {_clean_header(f): f for f in fieldnames}
    return m.get(_clean_header(want))


def _pair_key(a: str, b: str) -> Tuple[str, str]:
    return (a, b) if a < b else (b, a)

def _out_name_for_input_csv(csv_path: Path) -> str:
    # e.g. "강남구_시간_거리.csv" -> "강남구_tmap_pairs.csv"
    stem = csv_path.stem
    stem = stem.replace("_시간_거리", "")
    return f"{stem}_tmap_pairs.csv"


def _gu_from_od_csv_path(csv_path: Path) -> str:
    return csv_path.stem.replace("_시간_거리", "")


def _filling_marker_path(out_path: Path) -> Path:
    return out_path.with_name(out_path.name + ".filling")


def _filling_marker_stale(marker: Path, max_age_sec: float) -> bool:
    try:
        return (time.time() - marker.stat().st_mtime) >= max_age_sec
    except OSError:
        return True


@dataclass(frozen=True)
class Coord:
    lat: float
    lon: float


def _parse_float(x) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(str(x).strip())
    except (TypeError, ValueError):
        return None


def iter_unique_pairs_from_csv(
    path: Path,
) -> Tuple[Dict[str, Coord], Iterator[Tuple[str, str]]]:
    """
    관내이동_시간_거리 CSV 1개에서:
    - station_id -> (lat, lon) 맵 구성
    - (start_id, end_id) 유니크(대칭) pair iterator 반환
    """
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"missing header: {path}")

        sk = _pick_field(reader.fieldnames, "시작_대여소_ID")
        slatk = _pick_field(reader.fieldnames, "시작_대여소_위도")
        slonk = _pick_field(reader.fieldnames, "시작_대여소_경도")
        ek = _pick_field(reader.fieldnames, "종료_대여소_ID")
        elatk = _pick_field(reader.fieldnames, "종료_대여소_위도")
        elonk = _pick_field(reader.fieldnames, "종료_대여소_경도")
        need = [("시작_대여소_ID", sk), ("종료_대여소_ID", ek)]
        for nm, v in need:
            if v is None:
                raise ValueError(f"missing required column '{nm}' in {path}")

        coord: Dict[str, Coord] = {}
        pairs = set()
        for row in reader:
            a = (row.get(sk) or "").strip()
            b = (row.get(ek) or "").strip()
            if not a or not b or a == b:
                continue

            if slatk and slonk:
                la = _parse_float(row.get(slatk))
                lo = _parse_float(row.get(slonk))
                if la is not None and lo is not None and a not in coord:
                    coord[a] = Coord(lat=la, lon=lo)
            if elatk and elonk:
                la = _parse_float(row.get(elatk))
                lo = _parse_float(row.get(elonk))
                if la is not None and lo is not None and b not in coord:
                    coord[b] = Coord(lat=la, lon=lo)

            pairs.add(_pair_key(a, b))

    def _iter() -> Iterator[Tuple[str, str]]:
        for p in sorted(pairs):
            yield p

    return coord, _iter()


def iter_unique_pairs_from_csv_into(
    path: Path,
    *,
    coord: Dict[str, Coord],
    pairs: set[Tuple[str, str]],
) -> None:
    """CSV 1개를 읽어서 coord/pairs에 누적(대칭 유니크)"""
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return
        sk = _pick_field(reader.fieldnames, "시작_대여소_ID")
        slatk = _pick_field(reader.fieldnames, "시작_대여소_위도")
        slonk = _pick_field(reader.fieldnames, "시작_대여소_경도")
        ek = _pick_field(reader.fieldnames, "종료_대여소_ID")
        elatk = _pick_field(reader.fieldnames, "종료_대여소_위도")
        elonk = _pick_field(reader.fieldnames, "종료_대여소_경도")
        if sk is None or ek is None:
            raise ValueError(f"missing required columns in {path.name}")

        for row in reader:
            a = (row.get(sk) or "").strip()
            b = (row.get(ek) or "").strip()
            if not a or not b or a == b:
                continue

            if slatk and slonk and a not in coord:
                la = _parse_float(row.get(slatk))
                lo = _parse_float(row.get(slonk))
                if la is not None and lo is not None:
                    coord[a] = Coord(lat=la, lon=lo)
            if elatk and elonk and b not in coord:
                la = _parse_float(row.get(elatk))
                lo = _parse_float(row.get(elonk))
                if la is not None and lo is not None:
                    coord[b] = Coord(lat=la, lon=lo)

            pairs.add(_pair_key(a, b))


def build_global_unique_pairs_from_dir(
    input_dir: Path,
) -> Tuple[Dict[str, Coord], Iterator[Tuple[str, str]]]:
    """
    관내이동_시간_거리 폴더 내 *.csv를 전부 읽어서
    - station_id -> (lat, lon) 맵
    - (start_id, end_id) 유니크(대칭) pair iterator
    """
    files = sorted(p for p in input_dir.glob("*.csv") if p.is_file())
    if not files:
        raise ValueError(f"no csv files in dir: {input_dir}")

    coord: Dict[str, Coord] = {}
    pairs = set()

    for path in files:
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                continue

            sk = _pick_field(reader.fieldnames, "시작_대여소_ID")
            slatk = _pick_field(reader.fieldnames, "시작_대여소_위도")
            slonk = _pick_field(reader.fieldnames, "시작_대여소_경도")
            ek = _pick_field(reader.fieldnames, "종료_대여소_ID")
            elatk = _pick_field(reader.fieldnames, "종료_대여소_위도")
            elonk = _pick_field(reader.fieldnames, "종료_대여소_경도")
            if sk is None or ek is None:
                raise ValueError(f"missing required columns in {path.name}")

            for row in reader:
                a = (row.get(sk) or "").strip()
                b = (row.get(ek) or "").strip()
                if not a or not b or a == b:
                    continue

                if slatk and slonk and a not in coord:
                    la = _parse_float(row.get(slatk))
                    lo = _parse_float(row.get(slonk))
                    if la is not None and lo is not None:
                        coord[a] = Coord(lat=la, lon=lo)
                if elatk and elonk and b not in coord:
                    la = _parse_float(row.get(elatk))
                    lo = _parse_float(row.get(elonk))
                    if la is not None and lo is not None:
                        coord[b] = Coord(lat=la, lon=lo)

                pairs.add(_pair_key(a, b))

    def _iter() -> Iterator[Tuple[str, str]]:
        for p in sorted(pairs):
            yield p

    return coord, _iter()


def _load_done_keys(path: Path) -> set[Tuple[str, str]]:
    """(레거시) 캐시에 한 번이라도 나온 쌍 — 단일 파일 모드 등에서만 사용."""
    if not path.exists():
        return set()
    try:
        df = pd.read_csv(path)
    except Exception:
        return set()
    if df.empty:
        return set()
    if "a_id" in df.columns and "b_id" in df.columns:
        out = set()
        for a, b in zip(df["a_id"], df["b_id"]):
            if pd.isna(a) or pd.isna(b):
                continue
            out.add(_pair_key(str(a), str(b)))
        return out
    return set()


_STABLE_SKIP_STATUSES = frozenset({"OK", "NO_PATH_OR_TOO_CLOSE"})


def _last_status_by_pair_from_cache(path: Path) -> Dict[Tuple[str, str], str]:
    """
    캐시 CSV를 읽어 (a,b) → 마지막 행의 transit_status.
    같은 쌍이 여러 행이면 뒤에 있는 행이 우선(API_ERROR 재시도 후 갱신).
    """
    out: Dict[Tuple[str, str], str] = {}
    if not path.exists():
        return out
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                a = (row.get("a_id") or "").strip()
                b = (row.get("b_id") or "").strip()
                if not a or not b:
                    continue
                k = _pair_key(a, b)
                st = (row.get("transit_status") or "").strip() or "EMPTY"
                out[k] = st
    except OSError:
        return out
    return out


def _interleave_district_files_from_ends(files: List[Path]) -> List[Path]:
    """
    가나다순 정렬된 구 CSV 목록에서 앞·뒤를 번갈아 뽑은 순서.
    병렬 워커가 동시에 돌 때 강남 쪽과 중랑 쪽처럼 양 끝이 같이 채워지도록 한다.
    """
    n = len(files)
    if n <= 1:
        return list(files)
    out: List[Path] = []
    lo, hi = 0, n - 1
    while lo <= hi:
        out.append(files[lo])
        lo += 1
        if lo <= hi:
            out.append(files[hi])
            hi -= 1
    return out


def _append_rows_to_cache(
    out_path: Path,
    rows: Iterable[dict],
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not out_path.exists()
    with out_path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "a_id",
                "b_id",
                "a_lat",
                "a_lon",
                "b_lat",
                "b_lon",
                "transit_status",
                "transit_total_min_1dp",
                "transit_riding_min_1dp",
                "transit_total_dist_m",
                "api_detail",
                "written_at_utc",
            ],
        )
        if new_file:
            w.writeheader()
        for r in rows:
            w.writerow(r)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="관내이동_시간_거리 CSV 경로 (예: 강남구_시간_거리.csv)")
    ap.add_argument("--input-dir", help="관내이동_시간_거리 폴더 경로 (전체 통합)")
    ap.add_argument(
        "--output",
        help="저장할 캐시 CSV 경로(단일 모드). --input-dir --per-district 이면 생략 가능.",
    )
    ap.add_argument("--output-dir", help="(per-district) 출력 폴더 경로")
    ap.add_argument("--per-district", action="store_true", help="input-dir 사용 시 구별 파일로 나눠 저장")
    ap.add_argument(
        "--reverse-district-files",
        action="store_true",
        help="(--per-district) 구 CSV 처리 순서를 가나다 역순(예: 중랑구→…→강남구).",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=4,
        help=(
            "(--per-district 전용) 서로 다른 자치구 CSV를 동시에 처리하는 워커 수. 기본 4. "
            "가나다 앞·뒤를 섞어(--workers>1) 한꺼번에 쏩니다. 순차만 쓰려면 --workers 1."
        ),
    )
    ap.add_argument(
        "--pair-workers",
        type=int,
        default=3,
        help=(
            "(--per-district 전용) 한 구 안에서 동시에 날리는 OD 쌍(TMap 호출) 수. 기본 3. "
            "과호출이면 1~2로 낮추거나 --sleep-sec을 올리세요."
        ),
    )
    ap.add_argument(
        "--exclude-gu",
        action="append",
        default=[],
        metavar="NAME",
        help="건너뛸 자치구(예: 강남구). 옵션을 여러 번 주면 누적됩니다.",
    )
    ap.add_argument(
        "--only-incomplete",
        action="store_true",
        help=(
            "(--per-district) 의미: 각 구 처리 시 남은 쌍이 없으면 즉시 스킵(기본 동작과 동일). "
            "전역 사전 필터는 하지 않습니다(대용량 OD CSV를 시작 시 두 번 읽지 않기 위함)."
        ),
    )
    ap.add_argument(
        "--skip-active-marker",
        action="store_true",
        help=(
            "(--per-district) 같은 출력 CSV에 대해 *.filling 마커가 있고 "
            "수정 시각이 --filling-max-age-sec 이내면 그 구는 스킵(다른 프로세스가 돌리는 중으로 간주)."
        ),
    )
    ap.add_argument(
        "--filling-max-age-sec",
        type=float,
        default=7200.0,
        help="(--skip-active-marker) 이보다 오래된 .filling은 무시하고 진행(비정상 종료 잔여물 정리).",
    )
    ap.add_argument(
        "--background",
        action="store_true",
        help="POSIX(mac/linux)에서만: 즉시 부모는 종료하고 자식 프로세스가 나머지를 실행(터미널 끊김에도 계속).",
    )
    ap.add_argument("--max-calls", type=int, default=0, help="이번 실행에서 최대 호출 수(0이면 제한 없음)")
    ap.add_argument("--sleep-sec", type=float, default=0.12, help="요청 간 sleep(과호출 완화, pair-workers마다 적용)")
    ap.add_argument(
        "--single-pass",
        action="store_true",
        help="각 구(또는 단일 파일)에서 라운드 1회만 호출 후 종료. 기본은 API_ERROR 등이 OK/NO_PATH로 끝날 때까지 반복.",
    )
    ap.add_argument(
        "--complete-max-rounds",
        type=int,
        default=2000,
        help="재시도 라운드 상한(구·단일 파일 각각). 무한 재시도 방지.",
    )
    args = ap.parse_args(argv)

    if args.input_dir and args.per_district:
        if not args.output_dir:
            ap.error("--output-dir is required when --per-district is set")
    elif not args.output:
        ap.error("--output is required unless using --input-dir with --per-district and --output-dir")

    if getattr(args, "background", False):
        if os.name != "posix":
            print("[ERR] --background 는 macOS/Linux 에서만 지원됩니다.", file=sys.stderr)
            return 2
        pid = os.fork()
        if pid != 0:
            print(f"[BG] child pid={pid} (parent exit 0)")
            return 0
        try:
            os.setsid()
        except OSError:
            pass

    max_calls = int(args.max_calls) if args.max_calls is not None else 0
    sleep_sec = float(args.sleep_sec)
    single_pass = bool(getattr(args, "single_pass", False))
    complete_max_rounds = max(1, int(getattr(args, "complete_max_rounds", 2000)))

    def sec_to_min_1dp(v):
        try:
            return round(float(v) / 60.0, 1)
        except (TypeError, ValueError):
            return ""

    # shared result memo to minimize API across districts
    memo: Dict[Tuple[str, str], dict] = {}

    def fetch_one(a_id: str, b_id: str, ca: Coord, cb: Coord) -> Tuple[bool, dict]:
        out = fetch_transit_time(ca.lon, ca.lat, cb.lon, cb.lat)
        st = str(out.get("transit_status", ""))
        row = {
            "a_id": a_id,
            "b_id": b_id,
            "a_lat": ca.lat,
            "a_lon": ca.lon,
            "b_lat": cb.lat,
            "b_lon": cb.lon,
            "transit_status": st,
            "transit_total_min_1dp": sec_to_min_1dp(out.get("transit_total_min")),
            "transit_riding_min_1dp": sec_to_min_1dp(out.get("transit_riding_min")),
            "transit_total_dist_m": ""
            if out.get("transit_total_dist_m") is None or pd.isna(out.get("transit_total_dist_m"))
            else float(out.get("transit_total_dist_m")),
            "api_detail": str(out.get("api_detail") or ""),
            "written_at_utc": pd.Timestamp.utcnow().isoformat(),
        }
        return (st == "OK"), row

    if args.input_dir and args.per_district:
        input_dir = Path(args.input_dir)
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        files_sorted = sorted(p for p in input_dir.glob("*.csv") if p.is_file())
        if not files_sorted:
            raise ValueError(f"no csv files in dir: {input_dir}")
        files = list(reversed(files_sorted)) if args.reverse_district_files else files_sorted

        exclude: Set[str] = set()
        for chunk in args.exclude_gu or []:
            for part in str(chunk).split(","):
                p = part.strip()
                if p:
                    exclude.add(p)

        if exclude:
            before = len(files)
            files = [fp for fp in files if _gu_from_od_csv_path(fp) not in exclude]
            print(f"[FILTER] exclude-gu {sorted(exclude)}: {before} -> {len(files)} files")

        if args.only_incomplete:
            print("[NOTE] --only-incomplete: 구별로 남은 쌍이 없으면 워커가 즉시 스킵합니다(전역 사전 스캔 없음).")

        if not files:
            print("[DONE] no district files to process (after filters)")
            return 0

        workers = max(1, int(args.workers))
        pair_workers = max(1, int(args.pair_workers))
        filling_max_age = float(args.filling_max_age_sec)
        memo_lock = threading.Lock()
        calls_lock = threading.Lock()
        total_calls_shared = [0]
        stop_event = threading.Event()
        write_locks: Dict[str, threading.Lock] = {}
        write_locks_guard = threading.Lock()

        def _write_lock_for(out_path: Path) -> threading.Lock:
            k = str(out_path.resolve())
            with write_locks_guard:
                if k not in write_locks:
                    write_locks[k] = threading.Lock()
                return write_locks[k]

        def process_one_district_file(fp: Path) -> None:
            if stop_event.is_set():
                return

            gu = _gu_from_od_csv_path(fp)
            if gu in exclude:
                return

            out_path = out_dir / _out_name_for_input_csv(fp)
            marker = _filling_marker_path(out_path)

            if args.skip_active_marker and marker.exists():
                if _filling_marker_stale(marker, filling_max_age):
                    try:
                        marker.unlink()
                    except OSError:
                        pass
                else:
                    print(f"[SKIP] active .filling (another run?) — {gu} ({marker.name})")
                    return

            coord: Dict[str, Coord] = {}
            pairs: set[Tuple[str, str]] = set()
            last_status0 = _last_status_by_pair_from_cache(out_path)
            print(f"[FILE] {fp.name} -> {out_path.name} (cached_pairs={len(last_status0)})")
            iter_unique_pairs_from_csv_into(fp, coord=coord, pairs=pairs)
            print(f"[FILE] {fp.name} unique_pairs={len(pairs)} coords={len(coord)}")

            fetchable_pairs: set[Tuple[str, str]] = set()
            for a_id, b_id in sorted(pairs):
                ca = coord.get(a_id)
                cb = coord.get(b_id)
                if ca is None or cb is None:
                    continue
                fetchable_pairs.add((a_id, b_id))

            pair_target = len(fetchable_pairs)
            if pair_target == 0:
                print(f"[SKIP] {gu} no fetchable pairs (coords missing)")
                return

            wl = _write_lock_for(out_path)
            round_num = 0
            while True:
                round_num += 1
                last_status = _last_status_by_pair_from_cache(out_path)
                initial_stable = {k for k in fetchable_pairs if last_status.get(k, "") in _STABLE_SKIP_STATUSES}
                if fetchable_pairs.issubset(initial_stable):
                    if round_num == 1:
                        print(f"[SKIP] {gu} all fetchable pairs terminal (OK/NO_PATH) — {pair_target} pairs")
                    else:
                        print(
                            f"[OK] {gu} all fetchable pairs terminal after {round_num - 1} refill round(s) "
                            f"— {pair_target} pairs"
                        )
                    return

                work_list: List[Tuple[str, str, Coord, Coord]] = []
                for a_id, b_id in sorted(fetchable_pairs):
                    ca = coord.get(a_id)
                    cb = coord.get(b_id)
                    if ca is None or cb is None:
                        continue
                    key = (a_id, b_id)
                    if last_status.get(key, "") in _STABLE_SKIP_STATUSES:
                        continue
                    work_list.append((a_id, b_id, ca, cb))

                if not work_list:
                    print(f"[SKIP] {gu} no pending pairs (unexpected)")
                    return

                if round_num > complete_max_rounds:
                    pend = sum(
                        1 for k in fetchable_pairs if last_status.get(k, "") not in _STABLE_SKIP_STATUSES
                    )
                    print(f"[WARN] {gu} --complete-max-rounds={complete_max_rounds} exceeded; pending≈{pend}")
                    return

                if round_num > 1:
                    print(f"[ROUND] {gu} round={round_num} pending_pairs={len(work_list)}")

                wrote_new = [0]
                wrote_lock = threading.Lock()
                district_stable: set[Tuple[str, str]] = set(initial_stable)
                dd_lock = threading.Lock()
                district_complete_event = threading.Event()

                def run_pair(job: Tuple[str, str, Coord, Coord]) -> None:
                    if stop_event.is_set() or district_complete_event.is_set():
                        return
                    a_id, b_id, ca, cb = job
                    key = (a_id, b_id)

                    with dd_lock:
                        if fetchable_pairs.issubset(district_stable):
                            district_complete_event.set()
                            return
                        if key in district_stable:
                            return

                    with memo_lock:
                        cached = memo.get(key)
                    if cached is not None:
                        row = cached
                        did_fetch = False
                        ok = str(row.get("transit_status", "")) == "OK"
                    else:
                        ok, row = fetch_one(a_id, b_id, ca, cb)
                        did_fetch = True
                        row_st_tmp = str(row.get("transit_status", "") or "")
                        with memo_lock:
                            if row_st_tmp in _STABLE_SKIP_STATUSES:
                                memo[key] = row
                            else:
                                memo.pop(key, None)

                    row_st = str(row.get("transit_status", "") or "")
                    just_finished_all = False
                    with wl:
                        with dd_lock:
                            if district_complete_event.is_set():
                                return
                            if key in district_stable:
                                return
                        _append_rows_to_cache(out_path, [row])
                        with dd_lock:
                            if row_st in _STABLE_SKIP_STATUSES:
                                district_stable.add(key)
                            if fetchable_pairs.issubset(district_stable):
                                district_complete_event.set()
                                just_finished_all = True

                    if just_finished_all:
                        print(f"[OK] {gu} all fetchable pairs terminal OK/NO_PATH ({len(district_stable)}/{pair_target})")

                    with wrote_lock:
                        wrote_new[0] += 1

                    if did_fetch:
                        with calls_lock:
                            total_calls_shared[0] += 1
                            tc = total_calls_shared[0]
                            snap = tc
                        if not ok and snap % 25 == 0:
                            print(
                                f"[WARN] file={fp.name} status={row['transit_status']} a={a_id} b={b_id} detail={row.get('api_detail')}"
                            )
                        if snap % 25 == 0:
                            print(f"[OK] total_calls={snap} (latest file={fp.name}, wrote_new≈{wrote_new[0]})")
                        if max_calls and snap >= max_calls:
                            stop_event.set()
                            print(f"[DONE] reached max_calls={max_calls}")
                        if sleep_sec > 0:
                            time.sleep(sleep_sec)

                try:
                    marker.write_text(
                        f"pid={os.getpid()}\nstarted_utc={pd.Timestamp.utcnow().isoformat()}\nround={round_num}\n",
                        encoding="utf-8",
                    )
                except OSError as e:
                    print(f"[WARN] could not write marker {marker}: {e}")

                try:
                    if pair_workers <= 1:
                        for job in work_list:
                            if stop_event.is_set() or district_complete_event.is_set():
                                break
                            run_pair(job)
                    else:
                        with ThreadPoolExecutor(max_workers=pair_workers) as pex:
                            futures = [pex.submit(run_pair, job) for job in work_list]
                            for fut in as_completed(futures):
                                if stop_event.is_set():
                                    break
                                try:
                                    fut.result()
                                except Exception as e:
                                    print(f"[ERR] {fp.name}: {e}")
                finally:
                    try:
                        marker.unlink(missing_ok=True)
                    except OSError:
                        pass

                print(f"[FILE] {fp.name} round={round_num} new_rows≈{wrote_new[0]}")

                if single_pass or stop_event.is_set():
                    return

        file_plan: List[Path] = (
            _interleave_district_files_from_ends(files) if workers > 1 else list(files)
        )
        order_desc = (
            "reverse-sorted (가나다 끝→앞)"
            if args.reverse_district_files
            else ("ends-interleaved" if workers > 1 else "sorted (가나다 앞→끝)")
        )
        print(
            f"[PLAN] district_workers={workers} pair_workers={pair_workers} "
            f"files={len(file_plan)} order={order_desc} sleep={sleep_sec}s "
            f"single_pass={single_pass} complete_max_rounds={complete_max_rounds}"
        )

        if workers == 1:
            for fp in file_plan:
                process_one_district_file(fp)
        else:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(process_one_district_file, file_plan))

        print("[DONE] all district files completed")
        return 0

    # default: single output mode
    if not args.output:
        raise ValueError("--output is required in single-file mode")
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.input_dir:
        coord, pairs_it = build_global_unique_pairs_from_dir(Path(args.input_dir))
    elif args.input:
        coord, pairs_it = iter_unique_pairs_from_csv(Path(args.input))
    else:
        raise ValueError("either --input or --input-dir is required")
    pair_list = sorted(list(pairs_it))

    calls = 0
    round_num = 0
    while True:
        round_num += 1
        last_st = _last_status_by_pair_from_cache(out_path)
        pending: List[Tuple[str, str, Coord, Coord]] = []
        for a_id, b_id in pair_list:
            key = (a_id, b_id)
            if last_st.get(key, "") in _STABLE_SKIP_STATUSES:
                continue
            ca = coord.get(a_id)
            cb = coord.get(b_id)
            if ca is None or cb is None:
                continue
            pending.append((a_id, b_id, ca, cb))

        if not pending:
            if round_num == 1:
                print("[DONE] all pairs already stable (single-file mode)")
            else:
                print(f"[DONE] all pairs stable after {round_num - 1} refill round(s) (single-file)")
            return 0

        if round_num > complete_max_rounds:
            print(f"[WARN] single-file --complete-max-rounds={complete_max_rounds} exceeded; pending={len(pending)}")
            return 1

        if round_num > 1:
            print(f"[ROUND] single-file round={round_num} pending={len(pending)}")

        for a_id, b_id, ca, cb in pending:
            if max_calls and calls >= max_calls:
                print(f"[DONE] reached max_calls={max_calls}")
                return 0
            key = (a_id, b_id)

            ok, row = fetch_one(a_id, b_id, ca, cb)
            _append_rows_to_cache(out_path, [row])
            st = str(row.get("transit_status", "") or "")
            last_st[key] = st

            if not ok:
                if calls % 25 == 0:
                    print(
                        f"[WARN] status={row['transit_status']} a={a_id} b={b_id} detail={row.get('api_detail')}"
                    )

            calls += 1
            if calls % 25 == 0:
                print(f"[OK] calls={calls}")
            if sleep_sec > 0:
                time.sleep(sleep_sec)

        print(f"[FILE] single-file round={round_num} calls_in_round={len(pending)} total_calls={calls}")

        if single_pass:
            tail = _last_status_by_pair_from_cache(out_path)
            left = sum(
                1
                for a_id, b_id in pair_list
                if tail.get((a_id, b_id), "") not in _STABLE_SKIP_STATUSES
                and coord.get(a_id) is not None
                and coord.get(b_id) is not None
            )
            if left:
                print(f"[NOTE] single-file stopped with ≈{left} pairs still non-terminal; rerun without --single-pass")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())

