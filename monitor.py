# -*- coding: utf-8 -*-
"""
공시캘린더 실시간 폴러 (백그라운드 상주)
- POLL_SECONDS 마다 어제~오늘 공시(감시대상+관심종류)를 확인 (자정 경계 누락 방지)
- 새 공시가 뜨면 OS 알림 (윈도우: 토스트·클릭 시 DART 원문 / 맥: 알림센터 / 리눅스: notify-send)
- 새 공시를 data/disclosures.json 에 자동 반영 → 웹 캘린더가 자동 갱신
사용:
  python monitor.py            # 상주 모드 (1분 폴링)
  python monitor.py --once     # 1회만 확인하고 종료 (테스트)
  python monitor.py --test-toast   # 알림만 테스트
"""
import json, os, sys, time, datetime, platform, subprocess, threading
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 콘솔 인코딩 크래시 방지
except Exception:
    pass
from fetch import collect_events, DATA_DIR, TLS_MODE, load_watchlist, CAL_EXCLUDE, save_json
import dividends
import expiries
import research

POLL_SECONDS = 60          # 폴링 주기 (트레이더용으로 장중 20~30초로 낮춰도 됨)
RETENTION_DAYS = 365       # 공시 캘린더 이력 보관 기간 (오래된 이벤트 자동 정리)
SEEN_KEEP_DAYS = 30        # 알림 중복방지 기록 보관 기간 (폴링 창은 2일이라 충분)
DATA_PATH = os.path.join(DATA_DIR, "disclosures.json")
SEEN_PATH = os.path.join(DATA_DIR, "seen.json")   # 알림 중복방지용 (공시/배당 공통)

def load_seen():
    if os.path.exists(SEEN_PATH):
        return set(json.load(open(SEEN_PATH, encoding="utf-8")))
    seen = set()   # 최초 실행: 기존 데이터의 접수번호로 시드
    for f in ("disclosures.json", "dividends.json"):
        p = os.path.join(DATA_DIR, f)
        if os.path.exists(p):
            for e in json.load(open(p, encoding="utf-8")).get("events", []):
                if e.get("rcept_no"):
                    seen.add(e["rcept_no"])
    return seen

def save_seen(seen):
    # 오래된 접수번호(rcept_no 앞 8자리 = 접수일)는 정리 — 무한 성장 방지
    cutoff = (datetime.date.today() - datetime.timedelta(days=SEEN_KEEP_DAYS)).strftime("%Y%m%d")
    save_json(SEEN_PATH, sorted(r for r in seen if r[:8] >= cutoff))

_div_lock = threading.Lock()      # 배당 전체 재생성 중복 방지
_pending_lock = threading.Lock()  # 대기 중 배당 upsert 큐 보호
_pending_div = []                 # 전체 재생성 도중 도착한 배당 공시 (끝난 뒤 반영)

def refresh_dividends():
    """배당 데이터(dividends.json) 재생성 — 무겁고 느려서 별도 스레드/중복방지"""
    if not _div_lock.acquire(blocking=False):
        return  # 이미 갱신 중
    def run():
        try:
            print("  [배당] 데이터 갱신 시작...")
            dividends.main(90)
            print("  [배당] 갱신 완료")
        except Exception as ex:
            print(f"  [배당] 갱신 실패: {ex}")
        finally:
            _div_lock.release()
        flush_pending_div()   # 재생성 도중 대기시킨 신규 배당 반영
    threading.Thread(target=run, daemon=True).start()

def flush_pending_div():
    """대기 중인 배당 upsert 처리 — 전체 재생성 중이면 미룸 (덮어쓰기로 유실되는 것 방지)"""
    with _pending_lock:
        if not _pending_div or _div_lock.locked():
            return
        batch, _pending_div[:] = list(_pending_div), []
    try:
        if dividends.upsert(batch):
            print(f"  [배당] 신규 {len(batch)}건 반영")
            refresh_research()   # 새 배당 → 이력·선진화·예상배당 갱신 (캐시라 신규분만 파싱)
    except Exception as ex:
        print(f"  [배당] 증분 반영 실패({ex}) → 전체 갱신")
        refresh_dividends()

_res_lock = threading.Lock()
def refresh_research():
    """리서치(선진화 판별 + 예상배당) 갱신 — 이력은 rcept_no 캐시라 신규 문서만 파싱"""
    if not _res_lock.acquire(blocking=False):
        return
    def run():
        try:
            print("  [리서치] 선진화·예상배당 갱신 시작...")
            research.build()
            print("  [리서치] 갱신 완료")
        except Exception as ex:
            print(f"  [리서치] 갱신 실패: {ex}")
        finally:
            _res_lock.release()
    threading.Thread(target=run, daemon=True).start()

def load_payload():
    if os.path.exists(DATA_PATH):
        return json.load(open(DATA_PATH, encoding="utf-8"))
    return {"range": {}, "count": 0, "events": []}

def save_payload(p):
    cutoff = (datetime.date.today() - datetime.timedelta(days=RETENTION_DAYS)).strftime("%Y%m%d")
    p["events"] = [e for e in p["events"] if (e.get("date") or "") >= cutoff]
    dates = [e["date"] for e in p["events"] if e.get("date")]
    p["range"] = {"bgn": min(dates), "end": max(dates)} if dates else {}
    p["count"] = len(p["events"])
    p["generated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_json(DATA_PATH, p)

def notify(e):
    """OS 알림 — 윈도우: 토스트(클릭 시 DART 원문) / 맥: 알림센터 / 리눅스: notify-send"""
    title = f"[{e['category']}] {e['corp']} · {e['market']}"
    try:
        system = platform.system()
        if system == "Windows":
            from winotify import Notification, audio
            t = Notification(app_id="공시캘린더", title=title, msg=e["title"], launch=e["url"])
            t.set_audio(audio.Default, loop=False)
            t.add_actions(label="DART 원문 보기", launch=e["url"])
            t.show()
        elif system == "Darwin":
            script = (f'display notification {json.dumps(e["title"], ensure_ascii=False)} '
                      f'with title {json.dumps(title, ensure_ascii=False)} sound name "Glass"')
            subprocess.run(["osascript", "-e", script], check=False, timeout=10)
        else:
            subprocess.run(["notify-send", title, e["title"]], check=False, timeout=10)
    except Exception as ex:
        print(f"  [알림실패] {ex}")

def poll_once(seen, alert=True):
    # 어제~오늘 조회: 자정 직전 접수분을 다음 폴링이 놓치지 않게 (중복은 seen이 걸러줌)
    today = datetime.date.today()
    bgn = (today - datetime.timedelta(days=1)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    watch = load_watchlist()
    events = collect_events(bgn, end, watch=watch, verbose=False)
    new = [e for e in events if e["rcept_no"] not in seen]
    new_cal, new_div = [], []
    for e in new:
        if alert:
            print(f"  🔔 신규: [{e['category']}] {e['corp']} - {e['title']}")
            notify(e)
        seen.add(e["rcept_no"])
        if e["category"] in CAL_EXCLUDE:      # 배당 → 공시 캘린더엔 넣지 않음
            new_div.append(e)
        else:
            new_cal.append(e)
    if new:
        save_seen(seen)
    if new_cal:
        # 저장 직전 디스크에서 다시 읽어 병합 — fetch.py 백필과 동시 실행돼도 유실 없음
        payload = load_payload()
        have = {e.get("rcept_no") for e in payload["events"]}
        payload["events"] = [e for e in new_cal if e["rcept_no"] not in have] + payload["events"]
        save_payload(payload)
    if new_div:
        with _pending_lock:
            _pending_div.extend(new_div)
    flush_pending_div()
    return len(new)

def main():
    first_run = not os.path.exists(SEEN_PATH)
    seen = load_seen()
    print(f"공시캘린더 폴러 시작 | TLS={TLS_MODE} | seen {len(seen)}건 | 주기 {POLL_SECONDS}s")

    if "--test-toast" in sys.argv:
        notify({"category": "테스트", "corp": "테스트기업", "market": "KOSPI",
                "title": "이것은 알림 테스트입니다", "url": "https://dart.fss.or.kr"})
        print("테스트 알림 전송됨"); return

    if "--once" in sys.argv:
        n = poll_once(seen)
        print(f"1회 확인 완료 - 신규 {n}건"); return

    try:
        expiries.build()   # 선물·옵션 만기일 생성/갱신 (규칙 계산, 즉시)
        print("  [만기일] 생성 완료")
    except Exception as ex:
        print(f"  [만기일] 생성 실패: {ex}")
    refresh_dividends()   # 상주 시작 시 배당 데이터 1회 갱신
    refresh_research()    # 선진화 판별 + 예상배당 갱신 (캐시 기반이라 신규 문서만 파싱)
    if first_run:
        poll_once(seen, alert=False)   # 최초 baseline: 현재 공시를 seen에 담고 알림 억제
        save_seen(seen)
        print("  최초 baseline 완료 (알림 억제)")
    while True:
        try:
            n = poll_once(seen)
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] 확인 완료 (신규 {n}건, 누적 {len(seen)}건)")
        except Exception as ex:
            print(f"[오류] {ex}")
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
