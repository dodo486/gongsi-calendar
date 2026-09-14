# -*- coding: utf-8 -*-
"""jhts 시세수집팀(jhts.marketdata) 어댑터 — 공시캘린더의 유일한 시세 창구.

공시캘린더는 시세를 직접 스크래핑하지 않는다. 모든 시세(실시간 현재값·지수·
전시장 스냅샷·일별 OHLC·수급·분봉·유동주식수)를 여기서 jhts로부터 받는다.
네이버/wisereport 접속·파싱 코드는 전부 jhts로 옮겼다(source/naver.py).

읽기 규칙:
- 일별(OHLC·수급): **로컬 미러(SISE_DB_PATH) 우선** → 없으면 jhts의 네이버
  라이브 소스로 폴백(둘 다 jhts 안). 미러는 sync_mirror.py가 중앙 PG에서 채운다.
- 실시간/분봉/참조: jhts 창구가 그때그때 소스에서 받아온다(휘발성, 저장 안 함).

jhts.marketdata 미설치 시 AVAILABLE=False, 함수는 빈 값을 돌려준다(무크래시).
"""
import os

os.environ.setdefault(
    "SISE_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sise.db"),
)

try:
    import jhts.marketdata as md
    from jhts.marketdata.source import naver as _naver
    AVAILABLE = True
except Exception:
    md = None
    _naver = None
    AVAILABLE = False


def _ymd(d):
    return (d or "").replace("-", "")


# ── 실시간 현재값 / 지수 / 전시장 ─────────────────────────────
def quote_batch(codes):
    """{code: {name, price, rate(부호%), value, volume, mktcap}}."""
    if not AVAILABLE:
        return {}
    try:
        return md.quote(list(codes))
    except Exception:
        return {}


def stock_rate(code):
    """{price, rate(부호%), sign} / 실패 시 {}."""
    if not AVAILABLE:
        return {}
    try:
        return md.stock_rate(code)
    except Exception:
        return {}


def index_rate(code):
    """지수 {price, rate(부호%), sign}. code: KPI200/KQI150."""
    if not AVAILABLE:
        return {}
    try:
        return md.index_rate(code)
    except Exception:
        return {}


def market_snapshot():
    """전 종목 스냅샷(거래대금 내림차순) [{code,name,market,price,rate,value,volume,mktcap}]."""
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


# ── 분봉 / 참조 ───────────────────────────────────────────────
def minute_closes(code, count=3000):
    """분봉 종가 {YYYYMMDDHHMM: 종가}."""
    if not AVAILABLE:
        return {}
    try:
        return md.minute_closes(code, count)
    except Exception:
        return {}


def float_shares(code):
    """유동가능주식수 / 실패 시 None."""
    if not AVAILABLE:
        return None
    try:
        return md.float_shares(code)
    except Exception:
        return None


def daily_rate_map(code, days=40):
    """일별 {YYYY-MM-DD: {rate(부호%), open, close}} — 과거 공시일 등락률·시가갭용."""
    if not AVAILABLE:
        return {}
    try:
        return _naver.daily_rate_map(code, days)
    except Exception:
        return {}


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
    # 미러에 없음 → jhts 네이버 라이브
    try:
        return _naver.daily_ohlc(code, n)
    except Exception:
        return []


def frgn_daily(code, pages=2):
    """일별 외국인·기관 순매매(주) 최신→과거 [{date, close, frgn, org, hold_qty, hold_pct}]."""
    if not AVAILABLE:
        return []
    try:
        flows = md.flows(code)  # 미러(오름차순)
    except Exception:
        flows = []
    if flows:
        closes = _close_map(code)
        out = []
        for f in flows:
            d = _ymd(f.get("date"))
            out.append({
                "date": d, "close": int(closes.get(d) or 0),
                "frgn": int(f.get("frgn_net_qty") or 0),
                "org": int(f.get("org_net_qty") or 0),
                "hold_qty": int(f.get("held_qty") or 0) if f.get("held_qty") is not None else 0,
                "hold_pct": f.get("hold_ratio"),
            })
        out.reverse()
        return out[:pages * 21]
    # 미러에 없음 → jhts 네이버 라이브
    try:
        return _naver.investor_daily(code, pages)
    except Exception:
        return []


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
    # 미러에 없음 → jhts 네이버 라이브
    try:
        return _naver.investor_trend(code, n)
    except Exception:
        return []
