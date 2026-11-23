# -*- coding: utf-8 -*-
"""
공시캘린더 실시간 폴러 (백그라운드 상주)
- POLL_SECONDS 마다 어제~오늘 공시(감시대상+관심종류)를 확인 (자정 경계 누락 방지)
- 새 공시가 뜨면 OS 알림 (윈도우: 토스트·클릭 시 DART 원문 / 맥: 알림센터 / 리눅스: notify-send)
- 새 공시를 data/out/disclosures.json 에 자동 반영 → 웹 캘린더가 자동 갱신
사용:
  python -m app.monitor            # 상주 모드 (1분 폴링)
  python -m app.monitor --once     # 1회만 확인하고 종료 (테스트)
  python -m app.monitor --test-toast   # 알림만 테스트
"""
import json, os, sys, time, datetime, platform, subprocess, threading
from derive.disclosures import collect_events
from ingest.sources.dart import live_rows
from core.paths import (load_json, save_json, stamp, CACHE_DIR, URL,
                        DISCLOSURES_PATH, DIVIDENDS_PATH, EARNINGS_PATH, EARN_SCHED_PATH, HALTS_PATH)
from ingest.sources.net import TLS_MODE
from ingest import run as ingest
from derive import run as derive
from derive import earnings
from derive import closearb
from derive import industry
from core import market as md_feed
from core import ops, universe
from core.krx_cal import iso, ymd, days_ago
from core.fmt import signed_pct

POLL_SECONDS = 20          # 공시/실적 폴링 주기 (DART·네이버는 무거워 20초)
LIMITS_POLL_SECONDS = 5    # 상하한가는 별도 스레드로 빠르게 — collect 0.1초라 장중 실시간(5초) 갱신
SELF_HEAL_SECONDS = 600    # 배당·증자·실적 전체 재계산 주기 — 받기 실패로 빠진 원문 재시도(목록은 저장본이라 가벼움)
RETENTION_DAYS = 365       # 공시 캘린더 이력 보관 기간 (오래된 이벤트 자동 정리)
SEEN_KEEP_DAYS = 30        # 알림 중복방지 기록 보관 기간 (폴링 창은 2일이라 충분)
SEEN_PATH = os.path.join(CACHE_DIR, "seen.json")   # 알림 중복방지용 (공시/배당/상하한가 공통)
ALERT_MIN_STAGE = 1   # 상하한가 토스트 최소 단계 (1=전부 / 2=2·3단계 / 3=실질 상한하한만)

def load_seen():
    if os.path.exists(SEEN_PATH):
        return set(load_json(SEEN_PATH, []))
    seen = set()   # 최초 실행: 기존 데이터의 접수번호로 시드
    for p in (DISCLOSURES_PATH, DIVIDENDS_PATH, EARNINGS_PATH, EARN_SCHED_PATH):
        for e in load_json(p, {}).get("events", []):
            if e.get("rcept_no"):
                seen.add(e["rcept_no"])
    return seen

def save_seen(seen):
    # 오래된 접수번호(rcept_no 앞 8자리 = 접수일)는 정리 — 무한 성장 방지
    cutoff = ymd(days_ago(SEEN_KEEP_DAYS))
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
            ops.run(name, work)
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
            notify({"category": "점검", "name": "배당 데이터", "market": "",
                    "title": f"오류 {len(errors)}건 — {errors[0]}", "url": URL + "dividend/index.html"})
    _run_guarded(_pipe_lock, work, "들여오기·계산")

_earn_lock = threading.Lock()
def refresh_quotes():
    """실적 등락률만 갱신(가벼움) — 실적 목록 자체는 refresh() 의 계산이 만든다"""
    _run_guarded(_earn_lock, earnings.refresh_quotes, "실적 등락률")

def load_payload():
    return load_json(DISCLOSURES_PATH, {"range": {}, "count": 0, "events": []})

def save_payload(p):
    cutoff = iso(days_ago(RETENTION_DAYS))
    p["events"] = [e for e in p["events"] if (e.get("date") or "") >= cutoff]
    dates = [e["date"] for e in p["events"] if e.get("date")]
    p["range"] = {"bgn": min(dates), "end": max(dates)} if dates else {}
    p["count"] = len(p["events"])
    p["generated_at"] = stamp()
    save_json(DISCLOSURES_PATH, p)

def _chart_url(code):
    """토스트 클릭 → 로컬 서버(/chart)가 대시보드에 'alphachart' 탭으로 알파스퀘어 차트 열기 지시.
    대시보드가 열려 있으면 항상 같은 탭 재사용(종목만 교체), 없으면 새 탭 폴백."""
    return f"{URL}chart?code={code}" if code else ""

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

def _alert(tag, e, msg, button, tail=""):
    """종목 알림 하나 — 제목 '[tag] 회사명 (+등락률%) · 시장{tail}', 클릭하면 그 종목 차트(종목이 없으면 원문), 버튼은 원문.
    등락률은 이벤트에 있으면 그 값, 없으면 시세 허브 최신값."""
    code = e.get("code")
    rate = e.get("rate")
    if rate is None and code:
        rate = md_feed.get("알림", {md_feed.KRX_STOCK: [code]})[md_feed.KRX_STOCK][code].get("rate")
    who = f"{e['name']}{f' ({signed_pct(rate)})' if isinstance(rate, (int, float)) else ''} · " if e.get("name") else ""
    _toast(f"[{tag}] {who}{e.get('market', '')}{tail}", msg, _chart_url(code) or e["url"], [(button, e["url"])])

def notify(e):
    """공시 알림"""
    _alert(e["category"], e, f"⏰{datetime.datetime.now():%H:%M} · {e['title']}", "🔗 DART 원문")

def notify_limit(e):
    """선물 상하한가 알림 — 주식선물이면 기초주식 차트"""
    when = f"⏰{e['time']} · " if e.get("time") else ""
    _alert(f"선물 {'▲상한' if e['direction'] == '상승' else '▼하한'}", e,
           f"{when}{e['kind']} {e['stage']}단계 가격제한폭 도달", "🔗 KRX 공시")

def notify_action(e):
    """KRX 시장조치 알림 — 종목 조치면 그 종목 차트"""
    when = f"⏰{e['time']} · " if e.get("time") else ""
    side = {"매수": " ▲매수", "매도": " ▼매도"}.get(e["direction"], "")
    _alert(f"{e['kind']}{(' ' + e['action']) if e['action'] else ''}", e, f"{when}{e['title']}", "🔗 KRX", side)

def notify_halt(e):
    """매매거래정지 알림"""
    tag = "" if e.get("resume_confirmed") else "(예정)"
    _alert("🚫거래정지", e, f"{e.get('reason', '')} · 정지 {e.get('halt_date', '')} → 재개 {e.get('resume_date') or '미정'}{tag}",
           "🔗 KIND 공시")

def _unseen(evs, seen):
    """처음 보는 이벤트만 (보면서 seen 에 담는다)"""
    for e in evs:
        if e.get("rcept_no") and e["rcept_no"] not in seen:
            seen.add(e["rcept_no"])
            yield e

def _active(e):
    """사이드카·서킷 자동해제 전, 선물 가격제한폭 확대 전 — 생산자가 정한 구간(active_from ~ active_to) 안인지"""
    return bool(e.get("active_to")) and e["active_from"] <= datetime.datetime.now().strftime("%H:%M") < e["active_to"]

def alert_actions(evs, ac_seen, alert=True):
    """새 시장조치 토스트. ac_seen: 전용 in-memory 중복셋.
    사이드카·서킷브레이커는 재시작 baseline 이라도 '아직 안 풀렸으면' 무조건 토스트(놓침 방지)."""
    for e in _unseen(evs, ac_seen):
        if alert or (e["critical"] and _active(e)):
            print(f"  🚨 시장조치 [{e['kind']}] {e['market']} {e['action']} {e['title'][:40]}")
            notify_action(e)

def alert_limits(evs, lim_seen, alert=True):
    """새 선물 가격제한폭 도달 토스트 — 실시간 신규 + 재시작 baseline 이라도 아직 제한폭 확대 전이면(사이드카와 통일)"""
    for e in _unseen(evs, lim_seen):
        if (alert or _active(e)) and (e.get("stage") or 0) >= ALERT_MIN_STAGE:
            print(f"  🚨 선물 {'상한' if e['direction']=='상승' else '하한'}: {e['name']} {e['stage']}단계 ({e['kind']})")
            notify_limit(e)

def live_pass(lim_seen, ac_seen, alert=True):
    """장중 고속 바퀴 — KIND 오늘 목록만 들여오기 → 시장조치·상하한가 계산(등락률은 매번) → 새 건 토스트"""
    out = derive.run(ingest.run(fast=True) | {"live"}, log=False)
    alert_limits(out.get("limits") or [], lim_seen, alert)
    alert_actions(out.get("actions") or [], ac_seen, alert)

INDUSTRY_POLL_SECONDS = 60  # 인더스트리 액션 — 전 종목 스냅샷 1회(수 초), 묶음 재구성은 30일마다

def industry_pass():
    """인더스트리 액션 → data/out/industry_action.json."""
    industry.daily_upkeep()
    industry.build_action()

HALT_POLL_SECONDS = 600   # 거래정지/재개 알림 확인 — halts.json 은 들여오기·계산이 갱신

def halt_alerts():
    """거래정지 알림 — 계산이 만든 data/out/halts.json 에서 새 정지 종목 토스트(첫 확인은 시딩만, 폭주 방지)."""
    halt_seen, first = set(), [True]
    def check():
        for e in _unseen(load_json(HALTS_PATH, {}).get("events", []), halt_seen):
            if not first[0]:
                notify_halt(e)
        first[0] = False
    return check

def poll_once(seen, alert=True):
    # 어제~오늘 조회: 자정 직전 접수분을 다음 폴링이 놓치지 않게 (중복은 seen이 걸러줌)
    bgn, end = ymd(days_ago(1)), ymd(days_ago(0))
    watch = universe.stocks()
    events = collect_events(live_rows(bgn, end), watch=watch)
    new = [e for e in events if e["rcept_no"] not in seen]
    new_cal, new_div, new_earn, new_sched, new_cap = [], [], [], [], []
    for e in new:
        if alert:
            print(f"  🔔 신규: [{e['category']}] {e['name']} - {e['title']}")
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
        notify({"category": "테스트", "name": "테스트기업", "market": "KOSPI",
                "title": "이것은 알림 테스트입니다", "url": "https://dart.fss.or.kr"})
        print("테스트 알림 전송됨"); return

    if "--once" in sys.argv:
        n = poll_once(seen)
        live_pass(set(), set(), alert=False)
        print(f"1회 확인 완료 - 공시 신규 {n}건 (limits/krx_actions 갱신됨)"); return

    md_feed.start()
    print("  [시세 허브] 시작 (거래일 08:00~20:00)")
    refresh(full=True)            # 들여오기 + 전 계산 1회 (만기일·배당·실적·실적예고·증자·공시 탭·정지)
    lim_seen, ac_seen = set(), set()   # 상하한가·시장조치 전용 중복셋(공시 seen 과 분리)
    ops.run("상하한가·시장조치", lambda: live_pass(lim_seen, ac_seen, alert=False))   # baseline: 현재분을 담고 알림 억제(재시작 스팸 방지, 아직 걸린 건 알림)
    for name, seconds, work in (("상하한가·시장조치", LIMITS_POLL_SECONDS, lambda: live_pass(lim_seen, ac_seen)),
                                ("거래정지 알림", HALT_POLL_SECONDS, halt_alerts()),
                                ("인더스트리", INDUSTRY_POLL_SECONDS, industry_pass)):
        threading.Thread(target=ops.every, args=(name, seconds, work), daemon=True, name=name).start()
        print(f"  [{name}] {seconds}초마다")
    threading.Thread(target=closearb.start, daemon=True).start()
    print("  [종가 차익] 시세 허브 구독")
    if first_run:
        poll_once(seen, alert=False)   # 최초 baseline: 현재 공시를 seen에 담고 알림 억제
        save_seen(seen)
        print("  최초 baseline 완료 (알림 억제)")
    last_heal = [time.time()]
    def poll():
        n = poll_once(seen)   # 상하한가는 별도 스레드가 5초마다 전담
        refresh_quotes()      # 실적 등락률만 매 주기 갱신(가벼움; 신규 실적은 poll_once 가 refresh 트리거)
        if time.time() - last_heal[0] >= SELF_HEAL_SECONDS:   # 주기적 전체 재계산 — 받기 실패로 빠진 원문을 다시 시도
            last_heal[0] = time.time()
            refresh(also={"quotes"})   # 들여오기 + 시세 쓰는 계산(증자) — 날이 바뀌었으면 오늘 기준 계산도
            print("  [주기 재계산] 들여오기·시세 계산")
        print(f"[{datetime.datetime.now():%H:%M:%S}] 확인 완료 (공시 신규 {n} · seen {len(seen)})")
    ops.every("공시 확인", POLL_SECONDS, poll)

if __name__ == "__main__":
    main()
