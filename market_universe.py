# -*- coding: utf-8 -*-
"""전체시장 유니버스 — 수급/섹터 분석용 (공시추적 watchlist와 분리).

네이버 모바일 API(m.stock.naver.com) 한 소스에서 전 종목의
code·거래대금·시총·등락률·거래량을 ~1초에 수집(ETF/ETN 제외).
거래대금 상위 N을 '수급 유니버스'로 사용해 소형주 로테이션까지 포착.

  python market_universe.py         # 상위 N(기본 800) 요약 출력
  python market_universe.py 1000

fetch_all() → [{code,name,market,price,rate,value,volume,mktcap}]  (거래대금 내림차순)
top_universe(n) → 상위 N만
"""
import sys, math, requests
from concurrent.futures import ThreadPoolExecutor
import fetch  # ssl 전역 패치 재사용

HDR = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
_S = requests.Session(); _S.headers.update(HDR)
_S.mount("https://", requests.adapters.HTTPAdapter(pool_connections=12, pool_maxsize=24))

API = "https://m.stock.naver.com/api/stocks/marketValue/{market}?page={page}&pageSize={size}"
PAGE = 100
DEFAULT_N = 800


def _num(x):
    if x is None:
        return None
    try:
        return float(str(x).replace(",", ""))
    except Exception:
        return None


def _page(market, page):
    try:
        return _S.get(API.format(market=market, page=page, size=PAGE), timeout=10).json().get("stocks", [])
    except Exception:
        return []


def fetch_all():
    """전 종목 실시간 스냅샷(거래대금 내림차순). ETF/ETN 제외."""
    rows = []
    for market in ("KOSPI", "KOSDAQ"):
        try:
            total = _S.get(API.format(market=market, page=1, size=1), timeout=10).json()["totalCount"]
        except Exception:
            continue
        pages = list(range(1, math.ceil(total / PAGE) + 1))
        with ThreadPoolExecutor(max_workers=5) as ex:   # TLS검사 프록시서 콜당 CPU 큼 → 병렬 낮춰 순간 점유 완화
            for st in ex.map(lambda p: _page(market, p), pages):
                for s in st:
                    if s.get("stockEndType") != "stock":   # ETF/ETN/리츠 등 제외
                        continue
                    code = s.get("itemCode")
                    if not code:
                        continue
                    sign = (s.get("compareToPreviousPrice") or {}).get("code", "3")
                    mag = _num(s.get("fluctuationsRatio")) or 0.0
                    rate = mag if sign in ("1", "2") else (-mag if sign in ("4", "5") else 0.0)
                    rows.append({
                        "code": code, "name": s.get("stockName", ""), "market": market,
                        "price": _num(s.get("closePriceRaw")),
                        "rate": round(rate, 2),
                        "value": _num(s.get("accumulatedTradingValueRaw")),
                        "volume": _num(s.get("accumulatedTradingVolumeRaw")),
                        "mktcap": _num(s.get("marketValueRaw")),
                    })
    rows = [r for r in rows if r["value"]]
    rows.sort(key=lambda r: -(r["value"] or 0))
    return rows


def top_universe(n=DEFAULT_N):
    return fetch_all()[:n]


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_N
    import time
    t0 = time.time()
    allrows = fetch_all()
    top = allrows[:n]
    dt = time.time() - t0
    print(f"전체 {len(allrows)}종목 수집({dt:.1f}s) · 상위 {n} 유니버스")
    print(f"거래대금 컷(상위 {n}위): {top[-1]['name']} {top[-1]['value']/1e8:.0f}억")
    print("TOP15:", [(r["name"], round(r["value"]/1e8)) for r in top[:15]])
