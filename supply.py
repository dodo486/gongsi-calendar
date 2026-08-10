# -*- coding: utf-8 -*-
"""종목 수급 신호 — '매물대 아래 횡보 중 외국인 매집' 데이카운팅 (선행 판별).

선행(🔵) 정의:
 - 최근 WINDOW일 중 '외국인 순매수(+) & 종가가 매물대(거래량 밀집대) 상단 아래'인 날을 데이카운팅.
   많을수록 조용한 매집 = 선행 후보.
 - 아직 매물대 아래  → 🔵 선행 (상승가능성)
 - 매물대 돌파 후 매집 → 🔥 후행/발현
(기관은 무시, 외국인 순매수만 사용)

데이터: 외국인 순매수는 데스크톱 소스(quotes.frgn_daily, ~40일)로 모바일 10일 한계 극복.
매물대(POC)는 일봉(종가·거래량) 근사.
하이브리드 캐시: TTL 5분 디스크 캐시.
"""
import os, json, time
from concurrent.futures import ThreadPoolExecutor
import quotes
from fetch import DATA_DIR

CACHE_PATH = os.path.join(DATA_DIR, "trend_cache.json")
TTL = 300
WINDOW = 20            # 매집일 데이카운팅 창(거래일)
PROFILE_DAYS = 40      # 매물대 산출 기간
FRGN_PAGES = 2         # 외국인 데이터 페이지(페이지당 ~21일)
BINS = 20
MIN_ACCUM = 5          # 이 이상 매집일이면 매집 인정
MIN_STRENGTH = 0.03    # 매집일 인정 강도: 외인 순매수금액/시총(%) 하한 (=순매수/상장주식수)
_MEM = None


def _load():
    global _MEM
    if _MEM is not None:
        return _MEM
    if os.path.exists(CACHE_PATH):
        try:
            _MEM = json.load(open(CACHE_PATH, encoding="utf-8")); return _MEM
        except Exception:
            pass
    _MEM = {"ts": 0, "px": {}, "fr": {}, "sh": {}}
    return _MEM


def _save(c):
    try:
        json.dump(c, open(CACHE_PATH, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def refresh(codes, force=False):
    """daily_price(40)+frgn_daily(~40) 캐시. 신선(<TTL)+전부덮으면 콜0."""
    c = _load()
    now = time.time()
    fresh = (now - c.get("ts", 0)) < TTL
    have = c.get("px", {})
    missing = [x for x in codes if x not in have]
    if fresh and not missing and not force:
        return
    targets = codes if (force or not fresh) else missing

    def _one(code):
        return (code, quotes.daily_price(code, PROFILE_DAYS),
                quotes.frgn_daily(code, FRGN_PAGES), quotes.float_shares(code))
    px, fr = ({}, {}) if (force or not fresh) else (dict(c.get("px", {})), dict(c.get("fr", {})))
    sh = {} if (force or not fresh) else dict(c.get("sh", {}))
    with ThreadPoolExecutor(max_workers=12) as ex:
        for code, p, f, fl in ex.map(_one, targets):
            if p:
                px[code] = p
            if f:
                fr[code] = f
            if fl:
                sh[code] = fl   # 유동가능주식수(강도 분모)
    c["px"], c["fr"], c["sh"], c["ts"] = px, fr, sh, now
    _save(c)


def _poc(px):
    pts = [(p["close"], p["vol"]) for p in px if p.get("close") and p.get("vol")]
    if len(pts) < 5:
        return None
    lo = min(p for p, _ in pts); hi = max(p for p, _ in pts)
    if hi <= lo:
        return None
    w = (hi - lo) / BINS
    vb = [0.0] * BINS
    for price, vol in pts:
        vb[min(BINS - 1, int((price - lo) / w))] += vol
    k = max(range(BINS), key=lambda i: vb[i])
    return lo + (k + 0.5) * w


def signal(code):
    """매물대 기반 외국인 선행/후행. 캐시 없으면 None."""
    c = _load()
    px = c.get("px", {}).get(code)
    fr = c.get("fr", {}).get(code)
    if not px or not fr:
        return None
    poc = _poc(px)
    price = px[0]["close"] if px and px[0].get("close") else None
    if poc is None or price is None:
        return None
    above = price > poc
    shares = c.get("sh", {}).get(code)

    # 매집일 = 외인 순매수(+) & 매물대 아래 & 순매수금액/시총 ≥ MIN_STRENGTH
    def _is_accum(d):
        f = d.get("frgn") or 0
        if f <= 0 or not d.get("close") or d["close"] > poc:
            return False
        return (f / shares * 100) >= MIN_STRENGTH if shares else True

    # 개념 검증용 전체기간 건수: 매집 케이스 / 이탈 케이스(상승인데 외인 순매도)
    # 개념 검증용: 전체기간 매집 케이스의 '기간'(첫~마지막 매집일)과 건수
    buy_dates = [d["date"] for d in fr if _is_accum(d)]
    buy_cases = len(buy_dates)
    buy_from = min(buy_dates) if buy_dates else None   # 매집 시작일
    buy_to = max(buy_dates) if buy_dates else None     # 매집 최근일

    days = fr[:WINDOW]
    acc = sum(1 for d in days if _is_accum(d))
    accum = acc >= MIN_ACCUM
    return {
        "poc": round(poc), "price": round(price), "above": above,
        "accum_days": acc,
        "lead": accum and not above,   # 🔵 매물대 아래에서 외국인 매집 지속
        "lag": accum and above,        # 🔥 돌파 후 매집(발현)
        "accum": accum,
        "frgn_today": fr[0].get("frgn"),
        "buy_cases": buy_cases, "buy_from": buy_from, "buy_to": buy_to,
    }
