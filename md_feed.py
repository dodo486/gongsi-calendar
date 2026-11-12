# -*- coding: utf-8 -*-
"""jhts 시세수집팀(jhts.marketdata) 어댑터 — 공시캘린더의 유일한 시세 창구.

공시캘린더는 시세를 직접 스크래핑하지 않는다. 모든 시세(실시간 현재값·지수·
전시장 스냅샷·일별 OHLC·수급·분봉)를 여기서 jhts **공개 API(md.*)**로
받는다 — jhts 소스 계층(kr/source/*)을 직접 import 하지 않는다. 일별 OHLC·수급은
md.candles/md.flows, 현재가·지수·분봉은 KIS(키 필요), 전시장은 네이버.

읽기 규칙:
- 일별(OHLC·수급): **로컬 미러(SISE_DB_PATH)**에서 읽는다. 미러는 sync_mirror.py가
  중앙 PG에서 채운다 — 비어 있으면 빈 값(옛 네이버 라이브 폴백은 제거됨).
- 실시간/분봉/참조: jhts 창구가 그때그때 소스에서 받아온다(휘발성, 저장 안 함).
  실시간 현재가·지수·분봉은 KIS 를 쓰므로 SISE_KIS_* 키가 필요하다.

jhts.marketdata 미설치 시 AVAILABLE=False, 함수는 빈 값을 돌려준다(무크래시).
KIS 키가 없으면(mock 모드) 실시간은 가짜값 대신 '없음'을 돌려준다(데이터 정직성).
"""
import os

os.environ.setdefault(
    "SISE_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sise.db"),
)

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


def _ymd(d):
    return (d or "").replace("-", "")


# ── 실시간 현재값 / 지수 / 전시장 ─────────────────────────────
def stock_rate(code):
    """{price, rate(부호%), sign} / 실패 시 {}. KIS(키 필요)."""
    if not (AVAILABLE and _REALTIME_OK):
        return {}
    try:
        return md.stock_rate(code)
    except Exception:
        return {}


def quote(codes):
    """{code: {price, base_price, limit_up, limit_down, vi, market_warn, halted, …}} / 실패 시 {}. KIS."""
    if not (AVAILABLE and _REALTIME_OK):
        return {}
    try:
        return md.quote(codes)
    except Exception:
        return {}


def orderbook(codes, venue="KRX"):
    """{code: {price, ask, bid, total_ask_qty, total_bid_qty, expected_price, …}} / 실패 시 {}.
    venue KRX/NXT. 예상체결은 동시호가 중에만 값이 있다. KIS."""
    if not (AVAILABLE and _REALTIME_OK):
        return {}
    try:
        return md.orderbook(codes, venue)
    except Exception:
        return {}


def futures_quote(codes):
    """주식선물 {code: {price, prev_close, theo, disparity, basis, …}} / 실패 시 {}. KIS."""
    if not (AVAILABLE and _REALTIME_OK):
        return {}
    try:
        return md.futures_quote(codes)
    except Exception:
        return {}


def minute_bars_since(codes, since, futures=False):
    """오늘 since(HHMM)부터 1분봉 {code: [{dt(UTC), close, vol, …}]} / 실패 시 {}. futures=True 면 주식선물 코드. KIS."""
    if not (AVAILABLE and _REALTIME_OK):
        return {}
    try:
        return (md.futures_minute_bars_since if futures else md.minute_bars_since)(codes, since)
    except Exception:
        return {}


def stock_futures():
    """상장 주식선물 [{code, underlying, month, multiplier, …}] / 실패 시 []. KIS 마스터 파일."""
    if not AVAILABLE:
        return []
    try:
        return md.stock_futures()
    except Exception:
        return []


def index_rate(code):
    """지수 {price, rate(부호%), sign}. code: KPI200/KQI150. KIS(키 필요)."""
    if not (AVAILABLE and _REALTIME_OK):
        return {}
    try:
        return md.index_rate(code)
    except Exception:
        return {}


def market_snapshot():
    """전 종목 스냅샷 — 원본(미러, ETF/ETN/리츠 포함, 무정렬).
    [{code,name,market,kind,price,rate,value,volume,mktcap}]. 보통주 거래대금순은
    top_universe(뷰)를 쓴다 — jhts 데이터 계층이 미러로 바뀜(판정은 소비자)."""
    if not AVAILABLE:
        return []
    try:
        return md.market_snapshot()
    except Exception:
        return []


def top_universe(n=800):
    if not AVAILABLE:
        return []
    try:
        return md.top_universe(n)
    except Exception:
        return []


def themes():
    """테마 소속표 [{no, name, description, stocks: [{code, name, reason}]}] (하루 1회 캐시)."""
    if not AVAILABLE:
        return []
    try:
        return md.themes()
    except Exception:
        return []


def closes_values(codes, start):
    """start(YYYYMMDD) 이후 {code: [(date, close, close×volume)]} 오름차순 — 미러(DB)."""
    if not AVAILABLE:
        return {}
    out = {}
    for code in codes:
        try:
            out[code] = [(c.date, c.close, c.close * c.volume) for c in md.candles(code, start)]
        except Exception:
            pass
    return out


def refresh_candles(codes):
    """일봉 미러를 종목별 MAX(date) 다음 날부터 오늘까지 KIS 로 증분 수집.
    오늘 캔들까지 저장하므로 장 마감 뒤에만 부른다."""
    if not (AVAILABLE and _REALTIME_OK):
        return {}
    try:
        return md.collect(codes, ["candles"]).get("candles", {})
    except Exception:
        return {}


# ── 분봉 / 참조 ───────────────────────────────────────────────
def minute_closes(code, count=3000):
    """분봉 종가 {UTC ISO-8601 "…Z": 종가}. KIS(키 필요)."""
    if not (AVAILABLE and _REALTIME_OK):
        return {}
    try:
        return md.minute_closes(code, count)
    except Exception:
        return {}


def daily_rate_map(code, days=40):
    """일별 {YYYY-MM-DD: {rate(부호%), open, close}} — 과거 공시일 등락률·시가갭용.

    옛 네이버 전용 창구를 공개 API(md.candles)로 대체. 등락률은 전일 종가 대비로
    직접 계산한다(네이버가 주던 값과 동일 정의). 미러(DB)에서 읽으므로 KIS 키 불필요.
    """
    if not AVAILABLE:
        return {}
    try:
        rows = md.candles(code)  # 오름차순, 거래일만(OHLCV)
    except Exception:
        return {}
    out = {}
    prev = None
    for c in rows:
        d = c.date  # "YYYYMMDD"
        key = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        rate = round((c.close - prev) / prev * 100, 2) if prev else 0.0
        out[key] = {"rate": rate, "open": int(c.open), "close": int(c.close)}
        prev = c.close
    # 최근 days 일만
    if days and len(out) > days:
        out = dict(list(out.items())[-days:])
    return out


# ── 일별 OHLC / 수급 (로컬 미러 우선 → jhts 네이버 라이브 폴백) ──
def _close_map(code):
    try:
        return {_ymd(c.date): c.close for c in md.candles(code)}
    except Exception:
        return {}


def daily_price(code, n=40):
    """일별 OHLC 최신→과거 [{date(YYYYMMDD), open, high, low, close, vol}]."""
    if not AVAILABLE:
        return []
    try:
        rows = md.candles(code)  # 미러(오름차순, 거래일만)
    except Exception:
        rows = []
    if rows:
        out = [{"date": _ymd(c.date), "open": c.open, "high": c.high,
                "low": c.low, "close": c.close, "vol": c.volume} for c in rows]
        out.reverse()
        return out[:n]
    return []  # 미러에 없으면 없음(옛 네이버 라이브 폴백 제거 — KIS→DB 미러만)


def investor_trend(code, n=20):
    """일별 투자자 순매수(주) 최신→과거 [{date, frgn, org, indi, close}]."""
    if not AVAILABLE:
        return []
    try:
        flows = md.flows(code)  # 미러
    except Exception:
        flows = []
    if flows:
        closes = _close_map(code)
        out = []
        for f in flows:
            d = _ymd(f.get("date"))
            out.append({
                "date": d,
                "frgn": int(f.get("frgn_net_qty") or 0),
                "org": int(f.get("org_net_qty") or 0),
                "indi": int(f.get("prsn_net_qty") or 0),
                "close": int(closes.get(d) or 0),
            })
        out.reverse()
        return out[:n]
    return []  # 미러에 없으면 없음(옛 네이버 라이브 폴백 제거)
