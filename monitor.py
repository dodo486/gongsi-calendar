# -*- coding: utf-8 -*-
"""
공시캘린더 실시간 폴러 (백그라운드 상주)
- POLL_SECONDS 마다 오늘 공시(감시대상+관심종류)를 확인
- 새 공시가 뜨면 윈도우 토스트 알림 (클릭 시 DART 원문 열림)
- 새 공시를 data/disclosures.json 에 자동 반영 → 웹 캘린더가 자동 갱신
사용:
  python monitor.py            # 상주 모드 (1분 폴링)
  python monitor.py --once     # 1회만 확인하고 종료 (테스트)
  python monitor.py --test-toast   # 알림만 테스트
"""
import json, os, sys, time, datetime, threading
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 콘솔 인코딩 크래시 방지
except Exception:
    pass
from fetch import collect_events, DATA_DIR, TLS_MODE, load_watchlist, CAL_EXCLUDE
import dividends as divmod
import expiries

POLL_SECONDS = 60          # 폴링 주기 (트레이더용으로 장중 20~30초로 낮춰도 됨)
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
    json.dump(sorted(seen), open(SEEN_PATH, "w", encoding="utf-8"))

_div_lock = threading.Lock()
def refresh_dividends():
    """배당 데이터(dividends.json) 재생성 — 무겁고 느려서 별도 스레드/중복방지"""
    if not _div_lock.acquire(blocking=False):
        return  # 이미 갱신 중
    def run():
        try:
            print("  [배당] 데이터 갱신 시작...")
            divmod.main(90)
            print("  [배당] 갱신 완료")
        except Exception as ex:
            print(f"  [배당] 갱신 실패: {ex}")
        finally:
            _div_lock.release()
    threading.Thread(target=run, daemon=True).start()

def load_payload():
    if os.path.exists(DATA_PATH):
        return json.load(open(DATA_PATH, encoding="utf-8"))
    return {"range": {}, "count": 0, "events": []}

def save_payload(p):
    p["count"] = len(p["events"])
    p["generated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    json.dump(p, open(DATA_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

def notify(e):
    """윈도우 토스트 — 클릭하면 DART 원문 페이지가 열림"""
    try:
        from winotify import Notification, audio
        t = Notification(
            app_id="공시캘린더",
            title=f"[{e['category']}] {e['corp']} · {e['market']}",
            msg=e["title"],
            launch=e["url"],
        )
        t.set_audio(audio.Default, loop=False)
        t.add_actions(label="DART 원문 보기", launch=e["url"])
        t.show()
    except Exception as ex:
        print(f"  [알림실패] {ex}")

def poll_once(payload, seen, alert=True):
    today = datetime.date.today().strftime("%Y%m%d")
    watch = load_watchlist()
    events = collect_events(today, today, watch=watch, verbose=False)
    new = [e for e in events if e["rcept_no"] not in seen]
    added = False       # 공시 캘린더(disclosures.json)에 추가됐는지
    new_div = []        # 새 배당 공시들
    for e in new:
        if alert:
            print(f"  🔔 신규: [{e['category']}] {e['corp']} - {e['title']}")
            notify(e)
        seen.add(e["rcept_no"])
        if e["category"] in CAL_EXCLUDE:      # 배당 → 공시 캘린더엔 넣지 않음
            new_div.append(e)
        else:
            payload["events"].insert(0, e)    # 공시 캘린더 (최신이 앞으로)
            added = True
    if new:
        save_seen(seen)
    if added:
        save_payload(payload)
    if new_div:
        # 새 배당 공시 1건씩 즉시 증분 반영 (~1초, 전체 재생성 안 함)
        try:
            if divmod.upsert(new_div):
                print(f"  [배당] 신규 {len(new_div)}건 즉시 반영")
        except Exception as ex:
            print(f"  [배당] 증분 반영 실패({ex}) → 전체 갱신")
            refresh_dividends()
    return len(new)

def main():
    payload = load_payload()
    first_run = not os.path.exists(SEEN_PATH)
    seen = load_seen()
    print(f"공시캘린더 폴러 시작 | TLS={TLS_MODE} | seen {len(seen)}건 | 주기 {POLL_SECONDS}s")

    if "--test-toast" in sys.argv:
        notify({"category": "테스트", "corp": "테스트기업", "market": "KOSPI",
                "title": "이것은 알림 테스트입니다", "url": "https://dart.fss.or.kr"})
        print("테스트 알림 전송됨"); return

    if "--once" in sys.argv:
        n = poll_once(payload, seen)
        print(f"1회 확인 완료 - 신규 {n}건"); return

    try:
        expiries.build()   # 선물·옵션 만기일 생성/갱신 (규칙 계산, 즉시)
        print("  [만기일] 생성 완료")
    except Exception as ex:
        print(f"  [만기일] 생성 실패: {ex}")
    refresh_dividends()   # 상주 시작 시 배당 데이터 1회 갱신
    if first_run:
        poll_once(payload, seen, alert=False)   # 최초 baseline: 현재 공시를 seen에 담고 알림 억제
        save_seen(seen)
        print("  최초 baseline 완료 (알림 억제)")
    while True:
        try:
            n = poll_once(payload, seen)
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] 확인 완료 (신규 {n}건, 누적 {len(seen)}건)")
        except Exception as ex:
            print(f"[오류] {ex}")
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
