# -*- coding: utf-8 -*-
"""jhts 시세수집팀(jhts.marketdata) 어댑터 — 공시캘린더의 유일한 시세 창구.

공시캘린더는 시세를 직접 스크래핑하지 않는다. 모든 시세(실시간 현재값·지수·
전시장 스냅샷·일별 OHLC·수급·분봉)를 여기서 jhts **공개 API(md.*)**로
받는다 — jhts 소스 계층(kr/source/*)을 직접 import 하지 않는다. 일별 OHLC·수급은
md.candles/md.flows, 현재가·지수·분봉은 KIS(키 필요), 전시장은 네이버.

읽기 규칙:
- 일별(OHLC·수급): **로컬 미러(SISE_DB_PATH)**에서 읽는다. 미러는 sync_mirror.py가
  중앙 PG에서 채우고, 일봉은 장 마감 뒤 이 창구가 KIS 로 증분 수집한다 — 비어 있으면 빈 값.
- 실시간(호가·현재가·종목 상태·주식선물·지수): 시세 허브 하나가 받고 소비자는 구독·읽기만 한다.
- 분봉/참조: jhts 창구가 그때그때 소스에서 받아온다(휘발성, 저장 안 함).
  실시간·분봉은 KIS 를 쓰므로 SISE_KIS_* 키가 필요하다.

jhts.marketdata 미설치 시 AVAILABLE=False, 함수는 빈 값을 돌려준다(무크래시).
KIS 키가 없으면(mock 모드) 실시간은 가짜값 대신 '없음'을 돌려준다(데이터 정직성).
"""
import os, time, datetime, threading
from core.paths import CACHE_DIR, load_json, save_json
from core.krx_cal import is_business_day, session_delay, iso, ymd, minutes, default_years, CALENDAR_PATH
from core import krx_cal
from core.fmt import pct
from core import ops

os.environ.setdefault("SISE_DATA_DIR", CACHE_DIR)   # jhts 의 시세 DB·KIS 토큰 위치
os.environ.setdefault("SISE_DB_PATH", os.path.join(CACHE_DIR, "sise.db"))

try:
    import jhts.marketdata as md
    from jhts.marketdata import config as _md_config
    AVAILABLE = True
    # KIS 키 없으면 jhts 가 MockClient(가짜 시세)로 떨어진다 — 실시간을 가짜로
    # 내보내지 않도록 mock 모드면 실시간 창구를 막는다(일별/수급은 DB 미러라 무관).
    _REALTIME_OK = not _md_config.USE_MOCK
except Exception:
    md = None
    AVAILABLE = False
    _REALTIME_OK = False


# ── 실시간 시세 허브 ──────────────────────────────────────────
# 실시간 시세는 허브 스레드 한 곳만 KIS 에 요청한다. 소비자는 직접 요청하지 않는다.
#   watch(이름, on_tick, {피드: 코드들})  구독 — 허브가 한 바퀴 받을 때마다 구독자 자기 스레드에서 on_tick(시간대, 시각)
#   latest(피드)                        구독한 피드의 최신값 {코드: 값}
#   get(이름, {피드: 코드들})            최신값 — 처음 묻는 종목만 그 자리에서 받고, 그 뒤로는 허브가 갱신
#   stats()                             관제탑용 — 피드별·구독자별로 언제 몇 개 받았나
# 피드 이름 = 거래소 상품 자료. 관제탑도 이 이름을 그대로 쓴다.
KRX_STOCK, KRX_FUT, NXT_STOCK = "KRX 주식 시세", "KRX 주식선물 시세", "NXT 주식 시세"
KRX_STATUS, KRX_INDEX, KRX_BOOK = "KRX 주식 상태", "KRX 지수 시세", "KRX 주식 호가"
PRE, OPEN_AUCTION, REGULAR, AUCTION, FUT_CLOSE, AFTER = "NXT 프리마켓", "개장 동시호가", "정규장", "마감 동시호가", "선물 마감까지", "NXT 애프터마켓"
KRX_OPEN, AUCTION_START, KRX_CLOSE = "0900", "1520", "1530"   # 평소 KRX 개장·마감 동시호가 시작·마감
PHASES = [
    (PRE, "0800", "0830"),
    (OPEN_AUCTION, "0830", KRX_OPEN),   # NXT 프리마켓은 0850 까지 겹침
    (REGULAR, KRX_OPEN, AUCTION_START),
    (AUCTION, AUCTION_START, "1531"),   # 랜덤엔드까지
    (FUT_CLOSE, "1531", "1546"),
    (AFTER, "1546", "2000"),
]
ONCE = float("inf")
SCHEDULE = {   # 시간대: {피드: 받는 주기(초)} — 0 매 바퀴, ONCE 시간대에 들어와서 한 번
    PRE: {NXT_STOCK: 0},
    OPEN_AUCTION: {KRX_STOCK: 0, KRX_FUT: 0, NXT_STOCK: 0},
    REGULAR: {KRX_STOCK: 0, KRX_FUT: 0, KRX_INDEX: 0, KRX_STATUS: 300},
    AUCTION: {KRX_STOCK: 0, KRX_FUT: 0, KRX_INDEX: 0, KRX_STATUS: 300, KRX_BOOK: 10},
    FUT_CLOSE: {KRX_STOCK: ONCE, KRX_INDEX: ONCE, KRX_FUT: 0, NXT_STOCK: 0},
    AFTER: {KRX_FUT: ONCE, NXT_STOCK: 300},
}
ROUND_SECONDS = 1   # 한 바퀴 최소 간격
CALENDAR_SECONDS = 86400   # KIS 휴장일 달력 — KIS 권장 하루 1회
CANDLE_HOUR = 18           # 이 시각 이후 하루 한 번 일봉 증분 수집
CANDLE_CHECK_SECONDS = 60
_candle_day = None         # 일봉 수집한 날
IDLE_SECONDS = 5    # 받을 게 없을 때 쉬는 초


def _krx_multi(due):
    """KRX 주식·주식선물 시세 — 멀티종목 한 콜, 같은 순번의 주식·선물이 같은 콜."""
    m = md.multi_quote(due.get(KRX_STOCK, ()), due.get(KRX_FUT, ()))
    return {KRX_STOCK: m["stock"], KRX_FUT: m["futures"]}


_FETCH = {   # 피드: 받는 법({피드: 코드들} → {피드: {코드: 값}}). 같은 받는 법을 쓰는 피드는 한 번에 받는다
    KRX_STOCK: _krx_multi,
    KRX_FUT: _krx_multi,
    NXT_STOCK: lambda due: {NXT_STOCK: md.multi_quote(due[NXT_STOCK], venue="NXT")["stock"]},
    KRX_STATUS: lambda due: {KRX_STATUS: md.quote(due[KRX_STATUS])},
    KRX_BOOK: lambda due: {KRX_BOOK: md.orderbook(due[KRX_BOOK], "KRX")},
    KRX_INDEX: lambda due: {KRX_INDEX: {c: md.index_rate(c) for c in due[KRX_INDEX]}},
}
FEEDS = tuple(_FETCH)
_lock = threading.Lock()
_latest = {f: {} for f in FEEDS}    # 피드: {코드: 값}
_at = {f: {} for f in FEEDS}        # 피드: {코드: 받은 시각}
_got = {}                           # 피드: {at, secs, asked, got} 마지막으로 받은 결과
_subs = {}                          # 구독자 이름: {피드: [코드]}
_watchers = {}                      # 구독자 이름: on_tick
_wake = {}                          # 구독자 이름: 새 바퀴 알림 — 구독자마다 자기 스레드에서 받는다
_served = {}                        # 구독자 이름: 마지막으로 시세를 건넨 시각
_busy = set()                       # 지금 받고 있는 피드
_state = {"phase": None, "round": None, "round_at": None, "tick": None}


def _minute(hhmm, d):
    """그날의 시간대 경계(자정부터 분) — 개장이 늦는 날은 개장을, 마감이 늦는 날은 마감 쪽 경계를 그만큼 민다."""
    open_late, close_late = session_delay(d)
    return minutes(hhmm) + (open_late if hhmm == KRX_OPEN else close_late if AUCTION_START <= hhmm <= "1546" else 0)


def krx_hours(d):
    """그날 KRX 개장·마감 (자정부터 분)"""
    return _minute(KRX_OPEN, d), _minute(KRX_CLOSE, d)


def phase_of(now):
    """거래일 시간대 이름 / 장 밖이면 None."""
    if not is_business_day(now.date()):
        return None
    t = now.hour * 60 + now.minute
    return next((name for name, start, end in PHASES if _minute(start, now.date()) <= t < _minute(end, now.date())), None)


def watch(name, on_tick, feeds):
    """구독 — {피드: 코드들}. 같은 이름으로 다시 부르면 종목을 바꾼다."""
    with _lock:
        _subs[name] = {f: list(codes) for f, codes in feeds.items()}
        _watchers[name] = on_tick
        fresh = name not in _wake
        _wake.setdefault(name, threading.Event())
    if fresh:
        threading.Thread(target=_serve, args=(name,), daemon=True, name=name).start()


def _serve(name):
    """구독자 하나의 스레드 — 깨면 가장 최근 바퀴로 on_tick. 느린 구독자는 밀린 바퀴를 건너뛸 뿐 허브와 다른 구독자를 막지 않는다."""
    while True:
        _wake[name].wait()
        _wake[name].clear()
        with _lock:
            on_tick, (phase, now) = _watchers[name], _state["tick"]
        ops.run(name, lambda: on_tick(phase, now), group="구독자")
        with _lock:
            _served[name] = time.time()


def latest(feed):
    with _lock:
        return dict(_latest[feed])


def get(name, feeds):
    """{피드: 코드들} 의 최신값 {피드: {코드: 값}}. 처음 묻는 종목만 그 자리에서 받고, 그 뒤로는 허브가 갱신한다."""
    feeds = {f: list(dict.fromkeys(codes)) for f, codes in feeds.items()}
    with _lock:
        subs = _subs.setdefault(name, {})
        for f, codes in feeds.items():
            subs[f] = list(dict.fromkeys(subs.get(f, []) + codes))
        miss = {f: [c for c in codes if c not in _latest[f]] for f, codes in feeds.items()}
    _fetch(miss)
    with _lock:
        _served[name] = time.time()
        return {f: {c: _latest[f].get(c, {}) for c in codes} for f, codes in feeds.items()}


def _fetch(due):
    """{피드: [코드]} 를 받아 최신값에 넣는다. 받는 법이 같은 피드는 한 번에. 실패한 종목은 전에 받은 값을 그대로 둔다."""
    due = {f: codes for f, codes in due.items() if codes}
    if not (due and AVAILABLE and _REALTIME_OK):
        return
    t0, got, errors = time.time(), {}, {}
    with _lock:
        _busy.update(due)
    for fetch in dict.fromkeys(_FETCH[f] for f in due):
        try:
            got.update(fetch(due))
        except Exception as ex:
            errors.update(dict.fromkeys((f for f in due if _FETCH[f] is fetch), f"{type(ex).__name__} {ex}"))
    t1 = time.time()
    with _lock:
        _busy.difference_update(due)
        for f, codes in due.items():
            rec = _got.setdefault(f, {})
            if f in errors:
                rec.update(error=errors[f], error_at=t1)
                continue
            vals = got.get(f) or {}
            for c in codes:
                if vals.get(c):
                    _at[f][c] = t1
                if vals.get(c) or c not in _latest[f]:
                    _latest[f][c] = vals.get(c) or {}
            rec.update(at=t1, secs=t1 - t0, asked=len(codes), got=sum(c in vals for c in codes))


def _loop():
    last, phase, since = {}, None, 0.0
    while True:
        t0 = time.time()
        now = datetime.datetime.now()
        if phase_of(now) != phase:
            phase, since = phase_of(now), t0
        with _lock:
            wanted = _wanted()
            busy = set(_busy)
            _state["phase"] = phase
        plan = SCHEDULE.get(phase, {})
        due = {f: wanted[f] for f, every in plan.items()
               if wanted[f] and f not in busy and (last.get(f, 0) < since or t0 - last[f] >= every)}
        behind = {f: codes for f, codes in due.items() if 0 < plan[f] < ONCE}   # 종목마다 묻는 느린 것은 바퀴를 붙잡지 않게 뒤에서
        if behind:
            threading.Thread(target=_fetch, args=(behind,), daemon=True).start()
        _fetch({f: codes for f, codes in due.items() if f not in behind})
        last.update(dict.fromkeys(due, t0))
        if due:
            with _lock:
                _state.update(round=time.time() - t0, round_at=time.time(), tick=(phase, now))
                for wake in _wake.values():
                    wake.set()
        time.sleep(max(ROUND_SECONDS - (time.time() - t0), 0) if due else IDLE_SECONDS)


def _wanted():
    return {f: list(dict.fromkeys(c for s in _subs.values() for c in s.get(f, ()))) for f in FEEDS}


def stats():
    """관제탑 항목 — 피드마다, 구독자마다 {group, name, at, every, rest, detail}. 판정은 ops 가 한다."""
    now = time.time()
    with _lock:
        phase, rnd, plan, wanted = _state["phase"], _state["round"], SCHEDULE.get(_state["phase"], {}), _wanted()
        live = {f for f in plan if wanted[f]}   # 지금 시간대에 받는 피드
        every = {f: plan[f] or rnd or ROUND_SECONDS for f in live if plan[f] != ONCE}   # 피드: 받는 주기(초)
        join = lambda *parts: " · ".join(p for p in parts if p)
        items = [{"group": "시세 허브", "name": "허브", "at": _state["round_at"], "every": min(every.values(), default=None),
                  "rest": not live, "detail": join(phase or "장 밖", rnd and f"한 바퀴 {rnd:.1f}초")}]
        for f in FEEDS:
            g = _got.get(f, {})
            items.append({
                "group": "시세 허브", "name": f, "at": g.get("at"), "error": g.get("error"), "error_at": g.get("error_at"),
                "rest": f not in live, "every": every.get(f),
                "detail": join(f"구독 {len(wanted[f])}개" if wanted[f] else "구독 없음",
                               "at" in g and f"마지막에 {g['got']}/{g['asked']}개 받음 · {g['secs']:.1f}초 걸림",
                               wanted[f] and f not in live and "이 시간대엔 안 받음"),
            })
        for name, subs in _subs.items():
            now_codes = [(f, c) for f, codes in subs.items() if f in live for c in codes]
            ages = [now - _at[f][c] for f, c in now_codes if c in _at[f]]
            items.append({
                "group": "구독자", "name": name, "at": _served.get(name),
                "detail": join(", ".join(f"{f} {len(codes)}" for f, codes in subs.items() if codes),
                               ages and f"가장 오래된 값 {max(ages):.0f}초 전",
                               len(now_codes) > len(ages) and f"빈 값 {len(now_codes) - len(ages)}개"),
            })
    return items


def refresh_calendar():
    """KIS 휴장일 달력을 받아 CALENDAR_PATH 에 합치고 휴장일 달력을 다시 읽는다.
    달력 범위에서 아직 없는 첫날과 오늘 중 이른 날부터 받는다 — 지난 날은 남기고, 받다 끊긴 구간은 다음에 채운다."""
    have = load_json(CALENDAR_PATH, {})
    years = default_years()
    first, today = datetime.date(years[0], 1, 1), datetime.date.today()
    missing = next((d for d in (first + datetime.timedelta(n) for n in range((today - first).days)) if ymd(d) not in have), today)
    got = _call({}, lambda: md.market_calendar(ymd(missing), f"{years[-1]}1231"), realtime=True)
    if got:
        save_json(CALENDAR_PATH, dict(sorted({**have, **got}.items())))
        krx_cal.load()
    return len(got)


def start():
    ops.source(stats)
    threading.Thread(target=_loop, daemon=True, name="시세 허브").start()
    threading.Thread(target=ops.every, args=("휴장일 달력", CALENDAR_SECONDS, refresh_calendar), daemon=True, name="휴장일 달력").start()
    threading.Thread(target=ops.every, args=("일봉 수집", CANDLE_CHECK_SECONDS, refresh_candles), daemon=True, name="일봉 수집").start()


def _call(default, fetch, realtime=False):
    """jhts 창구 한 번 — 설치 안 됐거나(realtime 이면 KIS 키 없는 mock 도) 실패하면 default."""
    if not AVAILABLE or (realtime and not _REALTIME_OK):
        return default
    try:
        return fetch()
    except Exception:
        return default


# ── 전시장 ────────────────────────────────────────────────────
def market_snapshot():
    """전 종목 스냅샷 — 원본(미러, ETF/ETN/리츠 포함, 무정렬).
    [{code,name,market,kind,price,rate,value,volume,mktcap}]. 보통주 거래대금순은 top_universe."""
    return _call([], lambda: md.market_snapshot())


def top_universe(n=800):
    return _call([], lambda: md.top_universe(n))


def themes():
    """테마 소속표 [{no, name, description, stocks: [{code, name, reason}]}] (하루 1회 캐시)."""
    return _call([], lambda: md.themes())


def closes_values(codes, start):
    """start(YYYYMMDD) 이후 {code: [(date, close, close×volume)]} 오름차순 — 미러(DB). 실패한 종목은 빠진다."""
    out = {}
    for code in codes:
        rows = _call(None, lambda: [(c.date, c.close, c.close * c.volume) for c in md.candles(code, start)])
        if rows is not None:
            out[code] = rows
    return out


def refresh_candles():
    """전 종목 일봉 미러를 종목별 MAX(date) 다음 날부터 오늘까지 KIS 로 증분 수집.
    오늘 캔들까지 저장하므로 거래일 CANDLE_HOUR 이후 하루 한 번."""
    global _candle_day
    now = datetime.datetime.now()
    if now.hour < CANDLE_HOUR or not is_business_day(now.date()) or _candle_day == now.date():
        return
    codes = [r["code"] for r in market_snapshot() if r.get("kind") == "stock"]
    _call({}, lambda: md.collect(codes, ["candles"]), realtime=True)
    _candle_day = now.date()


# ── 분봉 / 참조 ───────────────────────────────────────────────
def minute_bars_since(codes, since, futures=False):
    """오늘 since(HHMM)부터 1분봉 {code: [{dt(UTC), close, vol, …}]}. futures=True 면 주식선물 코드. KIS."""
    return _call({}, lambda: (md.futures_minute_bars_since if futures else md.minute_bars_since)(codes, since), realtime=True)


def stock_futures():
    """상장 주식선물 [{code, underlying, month, multiplier, …}]. KIS 마스터 파일."""
    return _call([], lambda: md.stock_futures())


def minute_closes(code, count=3000):
    """분봉 종가 {UTC ISO-8601 "…Z": 종가}. KIS."""
    return _call({}, lambda: md.minute_closes(code, count), realtime=True)


# ── 일별 OHLC / 수급 — 로컬 미러(DB) ─────────────────────────
def daily_rate_map(code, days=40):
    """일별 {YYYY-MM-DD: {rate(전일 종가 대비 %), open, close}} 최근 days 일 — 과거 공시일 등락률·시가갭용."""
    out, prev = {}, None
    for c in _call([], lambda: md.candles(code)):
        out[iso(c.date)] = {"rate": pct(c.close, prev) if prev else 0.0, "open": int(c.open), "close": int(c.close)}
        prev = c.close
    return dict(list(out.items())[-days:]) if days else out


def daily_price(code, n=40):
    """일별 OHLC 최신→과거 [{date(YYYY-MM-DD), open, high, low, close, vol}] n개."""
    rows = _call([], lambda: md.candles(code))
    return [{"date": iso(c.date), "open": c.open, "high": c.high, "low": c.low, "close": c.close, "vol": c.volume}
            for c in reversed(rows)][:n]


def investor_trend(code, n=20):
    """일별 투자자 순매수(주) 최신→과거 [{date(YYYY-MM-DD), frgn, org, indi, close}] n개."""
    closes = {iso(c.date): c.close for c in _call([], lambda: md.candles(code))}
    return [{"date": iso(f.get("date")), "frgn": int(f.get("frgn_net_qty") or 0), "org": int(f.get("org_net_qty") or 0),
             "indi": int(f.get("prsn_net_qty") or 0), "close": int(closes.get(iso(f.get("date"))) or 0)}
            for f in reversed(_call([], lambda: md.flows(code)))][:n]
