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
from fetch import collect_events, DATA_DIR, TLS_MODE, load_watchlist, CAL_EXCLUDE, save_json   # fetch import 시 콘솔 UTF-8 설정 공유
import dividends
import expiries
import research
import kind_limits
import earnings
import earn_sched
import krx_actions
import capital
import sectors

POLL_SECONDS = 20          # 공시/실적 폴링 주기 (DART·네이버는 무거워 20초)
LIMITS_POLL_SECONDS = 5    # 상하한가는 별도 스레드로 빠르게 — collect 0.1초라 장중 실시간(5초) 갱신
SELF_HEAL_SECONDS = 600    # 배당·실적 전체 재생성 주기 — upsert 유실분을 seen 무관하게 자동 복구
RETENTION_DAYS = 365       # 공시 캘린더 이력 보관 기간 (오래된 이벤트 자동 정리)
SEEN_KEEP_DAYS = 30        # 알림 중복방지 기록 보관 기간 (폴링 창은 2일이라 충분)
DATA_PATH = os.path.join(DATA_DIR, "disclosures.json")
SEEN_PATH = os.path.join(DATA_DIR, "seen.json")   # 알림 중복방지용 (공시/배당/상하한가 공통)
LIMITS_PATH = os.path.join(DATA_DIR, "limits.json")   # 선물 상하한가(가격제한폭)
ALERT_MIN_STAGE = 1   # 상하한가 토스트 최소 단계 (1=전부 / 2=2·3단계 / 3=실질 상한하한만)

def load_seen():
    if os.path.exists(SEEN_PATH):
        return set(json.load(open(SEEN_PATH, encoding="utf-8")))
    seen = set()   # 최초 실행: 기존 데이터의 접수번호로 시드
    for f in ("disclosures.json", "dividends.json", "earnings.json", "earn_sched.json"):
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

def _run_guarded(lock, work, name, after=None):
    """lock 이 비어 있을 때만 work() 를 데몬 스레드로 1회 실행(중복 방지).
    끝나면 lock 해제 후 after() 호출 — refresh_* 들의 공통 뼈대."""
    if not lock.acquire(blocking=False):
        return   # 이미 갱신 중
    def run():
        try:
            work()
        except Exception as ex:
            print(f"  [{name}] 갱신 실패: {ex}")
        finally:
            lock.release()
        if after:
            after()
    threading.Thread(target=run, daemon=True).start()

def refresh_dividends():
    """배당 데이터(dividends.json) 재생성 — 무겁고 느려서 별도 스레드/중복방지"""
    def work():
        print("  [배당] 데이터 갱신 시작..."); dividends.main(90); print("  [배당] 갱신 완료")
    _run_guarded(_div_lock, work, "배당", after=flush_pending_div)   # 재생성 뒤 대기분 반영

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

_earn_lock = threading.Lock()
def refresh_earnings(full=False):
    """실적 데이터 갱신 — full=True: 재수집+등락률(무거움) / False: 등락률만(가벼움). 중복 실행 방지."""
    def work():
        if full:
            print("  [실적] 재수집 시작..."); earnings.main(30); print("  [실적] 완료")
        else:
            earnings.refresh_quotes()
    _run_guarded(_earn_lock, work, "실적")

_res_lock = threading.Lock()
def refresh_research():
    """리서치(선진화 판별 + 예상배당) 갱신 — 이력은 rcept_no 캐시라 신규 문서만 파싱"""
    def work():
        print("  [리서치] 선진화·예상배당 갱신 시작..."); research.build(); print("  [리서치] 갱신 완료")
    _run_guarded(_res_lock, work, "리서치")

_cap_lock = threading.Lock()
def refresh_capital():
    """유·무상증자 데이터(capital.json) 재생성 — 원문 파싱이라 무거워 별도 스레드/중복방지(캐시로 신규분만 다운로드)"""
    def work():
        print("  [증자] 유·무상증자 갱신 시작..."); capital.main(); print("  [증자] 갱신 완료(과거 92일 공시 → 향후 3개월 기준일)")
    _run_guarded(_cap_lock, work, "증자")

_sched_lock = threading.Lock()
def refresh_earn_sched():
    """예상 실적발표 일정(earn_sched.json) 갱신 — 예고 문서는 rcept_no 캐시라 신규분만 파싱"""
    def work():
        print("  [실적예고] 예정일정 갱신 시작..."); earn_sched.main(); print("  [실적예고] 완료")
    _run_guarded(_sched_lock, work, "실적예고")

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
    """종목코드 → ' (+3.3%)' 등락률 문자열(부호 포함). 미상/실패면 ''. (네이버 실시간 재사용)"""
    if not code:
        return ""
    try:
        from earnings import naver_rate
        r = naver_rate(code).get("rate")
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
    """공용 OS 알림 — 윈도우(winotify, reminder 지속) / 맥 / 리눅스.
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

CRITICAL_ACTIONS = ("사이드카", "서킷브레이커")   # 시장 전체 조치 — 무조건 알림 대상

def _action_active(e):
    """사이드카·서킷 등 자동해제 조치가 아직 안 풀렸는지 (발동시각 ~ 해제예정시각 사이)."""
    tm, until = e.get("time"), e.get("until")
    if not tm or not until:
        return False
    return tm <= datetime.datetime.now().strftime("%H:%M") < until

def _limit_active(e):
    """선물 가격제한폭 도달이 아직 확대 전인지 (도달시각 ~ 확대예정시각 사이).
    확대예정시각 미상(본문 미보강)이면 판단 불가 → False(다음 주기 보강 후 판정)."""
    tm, exp = e.get("time"), e.get("expand_time")
    if not tm or not exp:
        return False
    return tm <= datetime.datetime.now().strftime("%H:%M") < exp[:5]

def poll_actions(ac_seen, alert=True):
    """KRX 시장조치 폴링 — krx_actions.json 최신화 + 신규 조치 토스트. ac_seen: 전용 in-memory 중복셋.
    사이드카·서킷브레이커는 재시작 baseline 이라도 '아직 안 풀렸으면' 무조건 토스트(놓침 방지)."""
    try:
        evs = krx_actions.collect()
    except Exception as ex:
        print(f"  [시장조치] 수집 실패: {ex}")
        return 0
    save_json(krx_actions.ACTIONS_PATH, {
        "date": datetime.date.today().strftime("%Y-%m-%d"),
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(evs), "events": evs,
    })
    alerted = 0
    for e in evs:
        if e["rcept_no"] in ac_seen:
            continue
        ac_seen.add(e["rcept_no"])
        # 일반 조치는 alert=True 일 때만 / 사이드카·서킷은 발동중이면 baseline 이라도 무조건
        if alert or (e["kind"] in CRITICAL_ACTIONS and _action_active(e)):
            print(f"  🚨 시장조치 [{e['kind']}] {e['market']} {e['action']} {e['title'][:40]}")
            notify_action(e)
            alerted += 1
    return alerted

def poll_limits(lim_seen, alert=True):
    """선물 상하한가 — KIND 파생 공시(가격제한폭 도달) 폴링. limits.json 최신화 + 신규 알림.
    lim_seen: 이 기능 전용 in-memory 중복셋(공시 seen 과 분리 → 전용 스레드에서 독립 동작)."""
    try:
        evs = kind_limits.collect()   # 오늘 코스피200/코스닥150 선물 가격제한폭 도달
    except Exception as ex:
        print(f"  [상하한가] 수집 실패: {ex}")
        return 0
    save_json(LIMITS_PATH, {
        "date": datetime.date.today().strftime("%Y-%m-%d"),
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(evs), "events": evs,
    })
    alerted = 0
    for e in evs:
        if e["rcept_no"] in lim_seen:
            continue
        lim_seen.add(e["rcept_no"])   # 신규는 등록(재알림 방지); 알림은 단계 조건만
        # 실시간 신규(alert) + 재시작 baseline 이라도 아직 제한폭 확대 전이면 무조건(사이드카와 통일)
        if (alert or _limit_active(e)) and (e.get("stage") or 0) >= ALERT_MIN_STAGE:
            print(f"  🚨 선물 {'상한' if e['direction']=='상승' else '하한'}: "
                  f"{e['name']} {e['stage']}단계 ({e['kind']})")
            notify_limit(e)
            alerted += 1
    return alerted

SECTORS_POLL_SECONDS = 240  # 섹터/테마 로테이션 집계 주기 — 유니버스 800종목 전수라 가장 무거워 넉넉히

def sectors_loop():
    """섹터로테이션 전용 루프(별도 스레드) — 테마/ETF 클러스터 수급 집계 → data/sectors_flow.json.
    장 시작 전엔 누적거래대금 0이라 유니버스가 비므로 집계가 0일 수 있음(정상, 개장 후 채워짐)."""
    while True:
        try:
            sectors.build()
        except Exception as ex:
            print(f"  [섹터] 갱신 실패: {ex}")
        time.sleep(SECTORS_POLL_SECONDS)

HALT_POLL_SECONDS = 1800  # 거래정지/재개 — 하루 단위로만 바뀌어 30분이면 충분 (KIND+DART 무거움)

def halt_loop():
    """거래정지/재개 전용 루프(별도 스레드) — KIND 정지·해제 공시 + DART 재개예정일 → data/halts.json.
    신규 정지 종목은 OS 토스트(첫 주기는 시딩만, 폭주 방지)."""
    import kind_halt
    halt_seen, first = set(), True
    while True:
        try:
            evs = kind_halt.collect()
            payload = {"generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "count": len(evs), "events": evs}
            save_json(os.path.join(DATA_DIR, "halts.json"), payload)
            for e in evs:
                rc = e.get("rcept_no")
                if rc and rc not in halt_seen:
                    halt_seen.add(rc)
                    if not first:
                        notify_halt(e)
            first = False
        except Exception as ex:
            print(f"  [거래정지] 갱신 실패: {ex}")
        time.sleep(HALT_POLL_SECONDS)

def limits_loop(lim_seen, ac_seen):
    """상하한가·시장조치 전용 고속 루프(별도 스레드) — 공시/실적과 무관하게 LIMITS_POLL_SECONDS 마다 갱신."""
    while True:
        try:
            poll_limits(lim_seen)
        except Exception as ex:
            print(f"  [상하한가 루프 오류] {ex}")
        try:
            poll_actions(ac_seen)
        except Exception as ex:
            print(f"  [시장조치 루프 오류] {ex}")
        time.sleep(LIMITS_POLL_SECONDS)

def poll_once(seen, alert=True):
    # 어제~오늘 조회: 자정 직전 접수분을 다음 폴링이 놓치지 않게 (중복은 seen이 걸러줌)
    today = datetime.date.today()
    bgn = (today - datetime.timedelta(days=1)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    watch = load_watchlist()
    events = collect_events(bgn, end, watch=watch, verbose=False)
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
    if new_div:
        with _pending_lock:
            _pending_div.extend(new_div)
    flush_pending_div()
    if new_cap:
        try:
            if capital.upsert(new_cap):
                print(f"  [증자] 신규 {len(new_cap)}건 반영")
        except Exception as ex:
            print(f"  [증자] 증분 반영 실패({ex}) → 전체 갱신")
            refresh_capital()
    if new_earn:
        refresh_earnings(full=True)           # 신규 실적 → 재수집(등락률 포함)
    if new_sched:
        refresh_earn_sched()                  # 신규 예고 → 예정일정 재수집
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
        poll_limits(set(), alert=False)
        poll_actions(set(), alert=False)
        print(f"1회 확인 완료 - 공시 신규 {n}건 (limits/krx_actions 갱신됨)"); return

    try:
        expiries.build()   # 선물·옵션 만기일 생성/갱신 (규칙 계산, 즉시)
        print("  [만기일] 생성 완료")
    except Exception as ex:
        print(f"  [만기일] 생성 실패: {ex}")
    refresh_dividends()   # 상주 시작 시 배당 데이터 1회 갱신
    refresh_capital()     # 유·무상증자 데이터 1회 갱신 (신주배정기준일 T-1/T-2)
    refresh_research()    # 선진화 판별 + 예상배당 갱신 (캐시 기반이라 신규 문서만 파싱)
    refresh_earnings(full=True)   # 실적 공시 + 현재 등락률 1회 재수집
    refresh_earn_sched()          # 예상 실적발표 일정 1회 재수집
    lim_seen, ac_seen = set(), set()   # 상하한가·시장조치 전용 중복셋(공시 seen 과 분리)
    try:
        poll_limits(lim_seen, alert=False)   # baseline: 현재 도달분을 담고 알림 억제(재시작 스팸 방지)
        poll_actions(ac_seen, alert=False)   # 시장조치 baseline
        print("  [상하한가·시장조치] baseline 완료")
    except Exception as ex:
        print(f"  [상하한가·시장조치] baseline 실패: {ex}")
    threading.Thread(target=limits_loop, args=(lim_seen, ac_seen), daemon=True).start()   # 5초 고속 갱신 스레드
    print(f"  [상하한가·시장조치] 전용 루프 시작 ({LIMITS_POLL_SECONDS}초 주기)")
    threading.Thread(target=sectors_loop, daemon=True).start()   # 섹터로테이션 집계 스레드
    print(f"  [섹터] 전용 루프 시작 ({SECTORS_POLL_SECONDS}초 주기)")
    threading.Thread(target=halt_loop, daemon=True).start()   # 거래정지/재개 집계 스레드
    print(f"  [거래정지] 전용 루프 시작 ({HALT_POLL_SECONDS}초 주기)")
    if first_run:
        poll_once(seen, alert=False)   # 최초 baseline: 현재 공시를 seen에 담고 알림 억제
        save_seen(seen)
        print("  최초 baseline 완료 (알림 억제)")
    last_heal = time.time()
    while True:
        try:
            n = poll_once(seen)   # 상하한가는 별도 스레드(limits_loop)가 5초마다 전담
            refresh_earnings()    # 실적 등락률만 매 주기 갱신(가벼움; 신규 실적은 poll_once가 재수집 트리거)
            # 자가치유: 주기적으로 배당·실적 전체 재생성 → upsert가 조용히 놓친 건도 자동 복구
            if time.time() - last_heal >= SELF_HEAL_SECONDS:
                last_heal = time.time()
                refresh_dividends()
                refresh_capital()
                refresh_earnings(full=True)
                refresh_earn_sched()
                print("  [자가치유] 배당·증자·실적·실적예고 전체 재생성 트리거")
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] 확인 완료 (공시 신규 {n} · seen {len(seen)})")
        except Exception as ex:
            print(f"[오류] {ex}")
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
