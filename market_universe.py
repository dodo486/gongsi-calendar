# -*- coding: utf-8 -*-
"""전체시장 유니버스 — jhts 시세수집팀 위임 shim.

예전엔 네이버 모바일 API(m.stock)로 전 종목 스냅샷을 직접 수집했다. 그 수집
코드는 jhts 시세수집팀(realtime/snapshot)으로 옮겼고, 여기서는 md_feed로 받는다.
공개 함수는 그대로다.

fetch_all() → [{code,name,market,price,rate,value,volume,mktcap}] (거래대금 내림차순)
top_universe(n) → 상위 N만
"""
import sys

import md_feed

DEFAULT_N = 800


def fetch_all():
    """전 종목 현재값 스냅샷(거래대금 내림차순, ETF/ETN/리츠 제외)."""
    return md_feed.market_snapshot()


def top_universe(n=DEFAULT_N):
    return md_feed.top_universe(n)


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_N
    rows = top_universe(n)
    print(f"상위 {len(rows)}종목(거래대금순):")
    for r in rows[:20]:
        print(f"  {r['code']} {r['name']:<12} 거래대금 {int(r['value'] or 0):>15,} rate {r['rate']}")
