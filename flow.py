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

def _signals(rows):
    """일별 레코드(과거→최신, 투자자 데이터 포함분)에서 신호 배지 추출.
    임계값: 거래량 급증 x2.0 / 연속 순매수 3일 / 전환 판정 직전 3일 기준."""
    sig = []
    have = [r for r in rows if r.get("org") is not None]   # 투자자 데이터 있는 날만

    def conv(key):   # 순매수 전환일: 직전 3일 순매도 우세 → 당일 순매수
        for i in range(len(have) - 1, 0, -1):
            if (have[i].get(key) or 0) > 0:
                prev = [have[j].get(key) or 0 for j in range(max(0, i - 3), i)]
                if prev and sum(1 for x in prev if x <= 0) >= max(2, len(prev) - 1):
                    return have[i]["date"]
        return None

    def streak(key):   # 최근 연속 순매수 일수
        c = 0
        for r in reversed(have):
            if (r.get(key) or 0) > 0:
                c += 1
            else:
                break
        return c

    md = lambda d: f"{d[4:6]}/{d[6:8]}"
    dc = conv("org")
    if dc:
        sig.append({"t": "buyturn", "label": f"기관 매수전환 {md(dc)}"})
    fc = conv("frgn")
    if fc:
        sig.append({"t": "buyturn", "label": f"외국인 매수전환 {md(fc)}"})
    so = streak("org")
    if so >= 3:
        sig.append({"t": "streak", "label": f"기관 {so}일연속 순매수"})
    sf = streak("frgn")
    if sf >= 3:
        sig.append({"t": "streak", "label": f"외국인 {sf}일연속 순매수"})
    surge = [r for r in rows if (r.get("volx") or 0) >= 2.0]
    if surge:
        s = surge[-1]
        sig.append({"t": "vol", "label": f"거래량급증 x{s['volx']} {md(s['date'])}"})
    cl = [r for r in rows if r.get("close")]
    if cl:
        low = min(cl, key=lambda r: r["close"])
        sig.append({"t": "low", "label": f"기간저점 {md(low['date'])} {int(low['close']):,}"})
    return sig

def detail(code, show=15):
    """종목 상세 — 일별 가격·거래량·투자자(외국인/기관계/개인) + 신호. serve.py /api/flow 가 호출."""
    px = {p["date"]: p for p in quotes.daily_price(code, 40)}
    tr = {t["date"]: t for t in quotes.investor_trend(code, 25)}
    dates = sorted(set(px) | set(tr))   # 과거→최신
    vols = [px[d]["vol"] for d in dates if d in px and px[d].get("vol")]
    base = sum(vols[-20:]) / max(1, len(vols[-20:])) if vols else 0   # 최근 20일 평균 거래량
    rows, prev, cum_f, cum_o = [], None, 0, 0
    for d in dates:
        p, t = px.get(d, {}), tr.get(d, {})
        close, vol = p.get("close"), p.get("vol")
        rate = round((close / prev - 1) * 100, 2) if (close and prev) else None
        prev = close or prev
        f, o, ind = t.get("frgn"), t.get("org"), t.get("indi")
        if f is not None:
            cum_f += f
        if o is not None:
            cum_o += o
        rows.append({"date": d, "close": close, "rate": rate, "vol": vol,
                     "volx": round(vol / base, 1) if (vol and base) else None,
                     "frgn": f, "org": o, "indi": ind})
    signals = _signals(rows)
    q = quotes.quote_batch([code]).get(code, {})
    name = q.get("name") or ""
    return {"code": code, "name": name, "quote": q,
            "rows": rows[-show:], "signals": signals,
            "cum_frgn": cum_f, "cum_org": cum_o,
            "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

if __name__ == "__main__":
    build()
