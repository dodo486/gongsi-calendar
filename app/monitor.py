# -*- coding: utf-8 -*-
"""
공시캘린더 실시간 폴러 (백그라운드 상주)
- POLL_SECONDS 마다 어제~오늘 공시(감시대상+관심종류)를 확인 (자정 경계 누락 방지)
- 새 공시가 뜨면 OS 알림 (윈도우: 토스트·클릭 시 DART 원문 / 맥: 알림센터 / 리눅스: notify-send)
- 새 공시를 data/disclosures.json 에 자동 반영 → 웹 캘린더가 자동 갱신
사용:
  python -m app.monitor            # 상주 모드 (1분 폴링)
  python -m app.monitor --once     # 1회만 확인하고 종료 (테스트)
  python -m app.monitor --test-toast   # 알림만 테스트
"""
import json, os, sys, time, datetime, platform, subprocess, threading
from derive.disclosures import collect_events
from ingest.sources.dart import live_rows
from core.paths import load_watchlist, load_json, save_json, OUT_DIR, CACHE_DIR
from ingest.sources.net import TLS_MODE
from ingest import run as ingest
from derive import run as derive
from derive import earnings
from derive import closearb
from derive import industry
from core import market as md_feed

POLL_SECONDS = 20          # 공시/실적 폴링 주기 (DART·네이버는 무거워 20초)
LIMITS_POLL_SECONDS = 5    # 상하한가는 별도 스레드로 빠르게 — collect 0.1초라 장중 실시간(5초) 갱신
SELF_HEAL_SECONDS = 600    # 배당·증자·실적 전체 재계산 주기 — 받기 실패로 빠진 원문 재시도(목록은 저장본이라 가벼움)
RETENTION_DAYS = 365       # 공시 캘린더 이력 보관 기간 (오래된 이벤트 자동 정리)
SEEN_KEEP_DAYS = 30        # 알림 중복방지 기록 보관 기간 (폴링 창은 2일이라 충분)
DATA_PATH = os.path.join(OUT_DIR, "disclosures.json")
SEEN_PATH = os.path.join(CACHE_DIR, "seen.json")   # 알림 중복방지용 (공시/배당/상하한가 공통)
ALERT_MIN_STAGE = 1   # 상하한가 토스트 최소 단계 (1=전부 / 2=2·3단계 / 3=실질 상한하한만)

def load_seen():
    if os.path.exists(SEEN_PATH):
        return set(json.load(open(SEEN_PATH, encoding="utf-8")))
    seen = set()   # 최초 실행: 기존 데이터의 접수번호로 시드
    for f in ("disclosures.json", "dividends.json", "earnings.json", "earn_sched.json"):
        p = os.path.join(OUT_DIR, f)
        if os.path.exists(p):
            for e in json.load(open(p, encoding="utf-8")).get("events", []):
                if e.get("rcept_no"):
                    seen.add(e["rcept_no"])
    return seen

def save_seen(seen):
    # 오래된 접수번호(rcept_no 앞 8자리 = 접수일)는 정리 — 무한 성장 방지
    cutoff = (datetime.date.today() - datetime.timedelta(days=SEEN_KEEP_DAYS)).strftime("%Y%m%d")
    save_json(SEEN_PATH, sorted(r for r in seen if r[:8] >= cutoff))

_again = set()   # 갱신 중에 또 들어온 요청 — 끝난 뒤 한 번 더 돈다(요청 합침)

def _run_guarded(lock, work, name):
    """work() 를 데몬 스레드로 실행. 이미 도는 중이면 끝난 뒤 한 번 더 — 새 공시가 갱신 도중 와도 빠지지 않음.
    refresh_* 들의 공통 뼈대."""
    if not lock.acquire(blocking=False):
        _again.add(name)
        return
    def run():
        while True:
            _again.discard(name)
            try:
                work()
            except Exception as ex:
                print(f"  [{name}] 갱신 실패: {ex}")
            if name not in _again:
                break
        lock.release()
        if name in _again:   # 풀기 직전에 들어온 요청
            _run_guarded(lock, work, name)
    threading.Thread(target=run, daemon=True).start()

_pipe_lock = threading.Lock()
def refresh(full=False, also=()):
    """들여오기 한 바퀴 → 바뀐 종류(+also)를 읽는 계산만(full 이면 전부). 배당 자가 점검 오류는 알림"""
    def work():
        changed = ingest.run() | set(also)
        errors = derive.run(None if full else changed).get("dividend_check")
        if errors:
            notify({"category": "점검", "corp": "배당 데이터", "market": "",
                    "title": f"오류 {len(errors)}건 — {errors[0]}", "url": "http://127.0.0.1:8777/dividend/index.html"})
    _run_guarded(_pipe_lock, work, "들여오기·계산")

_earn_lock = threading.Lock()
def refresh_quotes():
    """실적 등락률만 갱신(가벼움) — 실적 목록 자체는 refresh() 의 계산이 만든다"""
    _run_guarded(_earn_lock, earnings.refresh_quotes, "실적 등락률")

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

def _rate_str(code):
    """종목코드 → ' (+3.3%)' 등락률 문자열(부호 포함). 미상/실패면 ''."""
    if not code:
        return ""
    try:
        r = md_feed.stock_rate(code).get("rate")
        if isinstance(r, (int, float)):
            return f" ({'+' if r > 0 else ''}{r}%)"
    except Exception:
        pass
    return ""

def _code_of(name):
    """종목명 → 종목코드 (watchlist 역인덱스). 없으면 ''. (시장조치는 코드 없이 회사명만 있음)"""
    if not name:
        return ""
    key = name.strip()
    for code, info in (load_watchlist() or {}).items():
        if (info.get("name") or "").strip() == key:
            return code
    return ""

def _chart_url(code):
    """토스트 클릭 → 로컬 서버(/chart)가 대시보드에 'alphachart' 탭으로 알파스퀘어 차트 열기 지시.
    대시보드가 열려 있으면 항상 같은 탭 재사용(종목만 교체), 없으면 새 탭 폴백."""
    return f"http://127.0.0.1:8777/chart?code={code}" if code else ""

def _toast(title, msg, launch, buttons):
    """공용 OS 알림 — 윈도우(winotify, reminder 지속: 직접 닫기 전까지 화면·알림센터에 유지) / 맥 / 리눅스.
    launch: 토스트 본문 클릭 시 열 URL. buttons: [(label, url), ...] (reminder 시나리오는 액션 1개+ 필요)."""
    try:
        system = platform.system()
        if system == "Windows":
            from winotify import Notification, audio
            t = Notification(app_id="공시캘린더", title=title, msg=msg, launch=launch)
            t.set_audio(audio.Default, loop=False)
            for label, url in buttons:
                if url:
                    t.add_actions(label=label, launch=url)
            t.duration = 'long" scenario="reminder'   # 직접 닫기 전까지 화면에 유지
            t.show()
        elif system == "Darwin":
            script = (f'display notification {json.dumps(msg, ensure_ascii=False)} '
                      f'with title {json.dumps(title, ensure_ascii=False)} sound name "Glass"')
            subprocess.run(["osascript", "-e", script], check=False, timeout=10)
        else:
            subprocess.run(["notify-send", title, msg], check=False, timeout=10)
    except Exception as ex:
        print(f"  [알림실패] {ex}")

def notify(e):
    """공시 알림 — 클릭 시 토스 종목 차트(종목 알면) / 버튼: DART 원문"""
    title = f"[{e['category']}] {e['corp']}{_rate_str(e.get('stock'))} · {e['market']}"
    msg = f"⏰{datetime.datetime.now().strftime('%H:%M')} · {e['title']}"
    code = e.get("stock")
    chart = _chart_url(code)
    buttons = [("🔗 DART 원문", e["url"])]
    _toast(title, msg, chart or e["url"], buttons)

def notify_limit(e):
    """선물 상하한가 알림 — 클릭 시 기초주식 토스 차트(주식선물) / 버튼: KRX 원문"""
    arrow = "▲상한" if e["direction"] == "상승" else "▼하한"
    r = e.get("rate")
    rate_str = f"({'+' if r > 0 else ''}{r}%)" if isinstance(r, (int, float)) else ""
    title = f"[선물 {arrow}] {e['name']}{rate_str} · {e['market']}"
    tm = f"⏰{e['time']} · " if e.get("time") else ""
    msg = f"{tm}{e['kind']} {e['stage']}단계 가격제한폭 도달"
    code = e.get("code")   # 주식선물이면 기초주식 코드, 지수선물이면 빈값
    chart = _chart_url(code)
    buttons = [("🔗 KRX 공시", e["url"])]
    _toast(title, msg, chart or e["url"], buttons)

def notify_action(e):
    """KRX 시장조치 알림 — 클릭 시 해당 종목 토스 차트(종목조치) / 버튼: KRX 원문"""
    dirtag = " ▲매수" if e["direction"] == "매수" else (" ▼매도" if e["direction"] == "매도" else "")
    corp = e.get("corp", "")
    code = _code_of(corp)
    who = f"{corp}{_rate_str(code)} · " if corp else ""   # 종목-특정 조치면 회사명+등락률
    title = f"[{e['kind']}{(' ' + e['action']) if e['action'] else ''}] {who}{e['market']}{dirtag}"
    tm = f"⏰{e['time']} · " if e.get("time") else ""
    msg = f"{tm}{e['title']}"
    chart = _chart_url(code)
    buttons = [("🔗 KRX", e["url"])]
    _toast(title, msg, chart or e["url"], buttons)

def notify_halt(e):
    """매매거래정지 알림 — 클릭 시 해당 종목 차트 / 버튼: KIND 원문"""
    code = e.get("code")
    who = f"{e.get('corp','')}{_rate_str(code)} · " if e.get("corp") else ""
    title = f"[🚫거래정지] {who}{e.get('market','')}"
    rr = e.get("resume_date") or "미정"
    tag = "" if e.get("resume_confirmed") else "(예정)"
    msg = f"{e.get('reason','')} · 정지 {e.get('halt_date','')} → 재개 {rr}{tag}"
    chart = _chart_url(code)
    buttons = [("🔗 KIND 공시", e["url"])]
    _toast(title, msg, chart or e["url"], buttons)

def _active(e):
    """사이드카·서킷 자동해제 전, 선물 가격제한폭 확대 전 — 생산자가 정한 구간(active_from ~ active_to) 안인지"""
    return bool(e.get("active_to")) and e["active_from"] <= datetime.datetime.now().strftime("%H:%M") < e["active_to"]

def alert_actions(evs, ac_seen, alert=True):
    """새 시장조치 토스트. ac_seen: 전용 in-memory 중복셋.
    사이드카·서킷브레이커는 재시작 baseline 이라도 '아직 안 풀렸으면' 무조건 토스트(놓침 방지)."""
    for e in evs:
        if e["rcept_no"] in ac_seen:
            continue
        ac_seen.add(e["rcept_no"])
        if alert or (e["critical"] and _active(e)):
            print(f"  🚨 시장조치 [{e['kind']}] {e['market']} {e['action']} {e['title'][:40]}")
            notify_action(e)

def alert_limits(evs, lim_seen, alert=True):
    """새 선물 가격제한폭 도달 토스트 — 실시간 신규 + 재시작 baseline 이라도 아직 제한폭 확대 전이면(사이드카와 통일)"""
    for e in evs:
        if e["rcept_no"] in lim_seen:
            continue
        lim_seen.add(e["rcept_no"])
        if (alert or _active(e)) and (e.get("stage") or 0) >= ALERT_MIN_STAGE:
            print(f"  🚨 선물 {'상한' if e['direction']=='상승' else '하한'}: {e['name']} {e['stage']}단계 ({e['kind']})")
            notify_limit(e)

def live_pass(lim_seen, ac_seen, alert=True):
    """장중 고속 바퀴 — KIND 오늘 목록만 들여오기 → 시장조치·상하한가 계산(등락률은 매번) → 새 건 토스트"""
    out = derive.run(ingest.run(fast=True) | {"live"}, log=False)
    alert_limits(out.get("limits") or [], lim_seen, alert)
    alert_actions(out.get("actions") or [], ac_seen, alert)

INDUSTRY_POLL_SECONDS = 60  # 인더스트리 액션 — 전 종목 스냅샷 1회(수 초), 묶음 재구성은 30일마다

def industry_loop():
    """인더스트리 액션 전용 루프(별도 스레드) → data/industry_action.json."""
    while True:
        try:
            industry.daily_upkeep()
            industry.build_action()
        except Exception as ex:
            print(f"  [인더스트리] 갱신 실패: {ex}")
        time.sleep(INDUSTRY_POLL_SECONDS)

HALT_POLL_SECONDS = 600   # 거래정지/재개 알림 확인 — halts.json 은 들여오기·계산이 갱신

def halt_loop():
    """거래정지 알림 전용 루프 — 계산이 만든 data/halts.json 에서 새 정지 종목 토스트(첫 주기는 시딩만, 폭주 방지)."""
    halt_seen, first = set(), True
    while True:
        try:
            for e in load_json(os.path.join(OUT_DIR, "halts.json"), {}).get("events", []):
                rc = e.get("rcept_no")
                if rc and rc not in halt_seen:
                    halt_seen.add(rc)
                    if not first:
                        notify_halt(e)
            first = False
        except Exception as ex:
            print(f"  [거래정지] 알림 확인 실패: {ex}")
        time.sleep(HALT_POLL_SECONDS)

def limits_loop(lim_seen, ac_seen):
    """상하한가·시장조치 전용 고속 루프(별도 스레드) — LIMITS_POLL_SECONDS 마다"""
    while True:
        try:
            live_pass(lim_seen, ac_seen)
        except Exception as ex:
            print(f"  [상하한가·시장조치 루프 오류] {ex}")
        time.sleep(LIMITS_POLL_SECONDS)

def poll_once(seen, alert=True):
    # 어제~오늘 조회: 자정 직전 접수분을 다음 폴링이 놓치지 않게 (중복은 seen이 걸러줌)
    today = datetime.date.today()
    bgn = (today - datetime.timedelta(days=1)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    watch = load_watchlist()
    events = collect_events(live_rows(bgn, end), watch=watch)
    new = [e for e in events if e["rcept_no"] not in seen]
    new_cal, new_div, new_earn, new_sched, new_cap = [], [], [], [], []
    for e in new:
        if alert:
            print(f"  🔔 신규: [{e['category']}] {e['corp']} - {e['title']}")
            notify(e)
        seen.add(e["rcept_no"])
        if e["category"] == "배당":           # 배당 → 배당 캘린더(dividends)
            new_div.append(e)
        elif e["category"] == "실적":          # 실적 → 실적 그리드 왼쪽(earnings)
            new_earn.append(e)
        elif e["category"] == "실적예고":       # 실적발표 예고 → 실적 그리드 오른쪽(earn_sched)
            new_sched.append(e)
        else:
            new_cal.append(e)                 # 그 외 → 공시 캘린더
        if e["category"] in ("유상증자", "무상증자"):   # 증자는 공시 캘린더 + 배당 캘린더 T-1/T-2 마커 겸용
            new_cap.append(e)
    if new:
        save_seen(seen)
    if new_cal:
        # 저장 직전 디스크에서 다시 읽어 병합 — fetch.py 백필과 동시 실행돼도 유실 없음
        payload = load_payload()
        have = {e.get("rcept_no") for e in payload["events"]}
        payload["events"] = [e for e in new_cal if e["rcept_no"] not in have] + payload["events"]
        save_payload(payload)
    if new_div or new_sched or new_cap or new_earn:
        refresh()                             # 새 배당·실적·실적예고·증자 공시 → 들여오기 → 바뀐 것만 계산
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
        live_pass(set(), set(), alert=False)
        print(f"1회 확인 완료 - 공시 신규 {n}건 (limits/krx_actions 갱신됨)"); return

    refresh(full=True)            # 들여오기 + 전 계산 1회 (만기일·배당·실적·실적예고·증자·공시 탭·정지)
    lim_seen, ac_seen = set(), set()   # 상하한가·시장조치 전용 중복셋(공시 seen 과 분리)
    try:
        live_pass(lim_seen, ac_seen, alert=False)   # baseline: 현재분을 담고 알림 억제(재시작 스팸 방지, 아직 걸린 건 알림)
        print("  [상하한가·시장조치] baseline 완료")
    except Exception as ex:
        print(f"  [상하한가·시장조치] baseline 실패: {ex}")
    threading.Thread(target=limits_loop, args=(lim_seen, ac_seen), daemon=True).start()   # 5초 고속 갱신 스레드
    print(f"  [상하한가·시장조치] 전용 루프 시작 ({LIMITS_POLL_SECONDS}초 주기)")
    threading.Thread(target=halt_loop, daemon=True).start()   # 거래정지/재개 집계 스레드
    print(f"  [거래정지] 전용 루프 시작 ({HALT_POLL_SECONDS}초 주기)")
    threading.Thread(target=industry_loop, daemon=True).start()
    print(f"  [인더스트리] 전용 루프 시작 ({INDUSTRY_POLL_SECONDS}초 주기)")
    threading.Thread(target=closearb.loop, daemon=True).start()
    print("  [종가 차익] 전용 루프 시작 (거래일 08:00~20:00)")
    if first_run:
        poll_once(seen, alert=False)   # 최초 baseline: 현재 공시를 seen에 담고 알림 억제
        save_seen(seen)
        print("  최초 baseline 완료 (알림 억제)")
    last_heal = time.time()
    while True:
        try:
            n = poll_once(seen)   # 상하한가는 별도 스레드(limits_loop)가 5초마다 전담
            refresh_quotes()      # 실적 등락률만 매 주기 갱신(가벼움; 신규 실적은 poll_once 가 refresh 트리거)
            # 주기적 전체 재계산 — 받기 실패로 빠진 원문을 다시 시도
            if time.time() - last_heal >= SELF_HEAL_SECONDS:
                last_heal = time.time()
                refresh(also={"quotes"})   # 들여오기 + 시세 쓰는 계산(증자) — 날이 바뀌었으면 오늘 기준 계산도
                print("  [주기 재계산] 들여오기·시세 계산")
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] 확인 완료 (공시 신규 {n} · seen {len(seen)})")
        except Exception as ex:
            print(f"[오류] {ex}")
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
