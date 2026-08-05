# -*- coding: utf-8 -*-
"""수급분석기 수집기 — 감시대상(코스피200·코스닥150) 실시간 배치 시세로
거래대금/등락 랭킹을 만들고, 거래대금 상위 종목엔 투자자(외국인·기관) 순매수(당일 잠정)를 부착.
→ data/flow.json  (serve.py 가 정적 서빙, SSE 로 flow.html 자동 갱신)
사용:
  python flow.py            # 1회 스냅샷 → data/flow.json
"""
import os, datetime
from fetch import DATA_DIR, load_watchlist, save_json, TLS_MODE
import quotes

FLOW_PATH = os.path.join(DATA_DIR, "flow.json")
TOP_N = 40      # 거래대금 상위 표시 종목수
TREND_N = 40    # 투자자 순매수 부착 종목수(상위부터, 과도한 /trend 호출 방지)
MOVE_N = 15     # 상승/하락 상위 표시수

def build():
    wl = load_watchlist() or {}
    q = quotes.quote_batch(list(wl.keys()))
    rows = []
    for code, info in wl.items():
        d = q.get(code)
        if not d or d.get("value") is None:
            continue
        mktcap, value = d["mktcap"], d["value"]
        rows.append({
            "code": code, "name": d["name"] or info.get("name", ""),
            "market": info.get("market", ""),
            "price": d["price"], "rate": d["rate"],
            "value": value, "volume": d["volume"], "mktcap": mktcap,
            # 거래대금이 시총 대비 몇 % (거래대금 회전율)
            "turnover": round(value / mktcap * 100, 2) if (value and mktcap) else None,
        })
    by_value = sorted(rows, key=lambda r: (r["value"] or 0), reverse=True)[:TOP_N]
    # 거래대금 상위 종목에 당일 외국인 순매수 부착 + 외국인 순매수가 시총 대비 몇 %
    for r in by_value[:TREND_N]:
        tr = quotes.investor_trend(r["code"], n=1)
        if tr:
            r["frgn"], r["org"], r["indi"] = tr[0]["frgn"], tr[0]["org"], tr[0]["indi"]
            if r["price"] and r["mktcap"]:
                r["frgn_pct"] = round(r["frgn"] * r["price"] / r["mktcap"] * 100, 3)
    payload = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "universe": len(rows),
        "by_value": by_value,
        "up": sorted(rows, key=lambda r: (r["rate"] if r["rate"] is not None else -999), reverse=True)[:MOVE_N],
        "down": sorted(rows, key=lambda r: (r["rate"] if r["rate"] is not None else 999))[:MOVE_N],
    }
    save_json(FLOW_PATH, payload)
    print(f"TLS={TLS_MODE} | flow.json 저장: 거래대금상위 {len(by_value)} · 전체 {len(rows)}종목")
    return payload

if __name__ == "__main__":
    build()
