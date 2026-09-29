# -*- coding: utf-8 -*-
"""지수배당포인트 — 근월물(다음 선·옵 동시만기)까지 배당락으로 빠질 지수 포인트 → data/divpoints.json

공식:  배당포인트 = 지수(전일 종가) × Σ(PDF수량 × 주당배당금) ÷ Σ(PDF 주식 평가금액)
        = 지수 × (배당 때문에 지수가 빠지는 비율)   ← 지수를 곱하는 건 '비율 → 포인트' 환산
대상:  오늘 < 배당락일 ≤ 만기일   (오늘 배당락분은 이미 오늘 가격에 반영돼 제외)

배당 출처: dividends.json(DART 확정) 우선, 없으면 research.json(예상 — 직전 배당 반복 가정).
같은 종목·배당구분의 예상은 확정 기준일 ±45일 안이면 같은 회차로 보고 버린다(이중 계상 방지).
기준일 공고만 있고 금액이 없는 확정분은 예상 금액을 빌려 쓰고 '금액예상'으로 표시한다.

  python divpoints.py
"""
import os, json, datetime
from fetch import DATA_DIR, save_json
import index_pdf

OUT = os.path.join(DATA_DIR, "divpoints.json")
SAME_ROUND_DAYS = 45
FORMULA = "배당포인트 = 지수(전일종가) × Σ(PDF수량 × 주당배당금) ÷ Σ(PDF 주식평가액)"

def _load(name, default):
    try:
        return json.load(open(os.path.join(DATA_DIR, name), encoding="utf-8"))
    except Exception:
        return default

def _amt(s):
    s = str(s or "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None

def _days(a, b):
    return abs((datetime.date.fromisoformat(a) - datetime.date.fromisoformat(b)).days)

def near_expiry(today_iso):
    """근월물 만기 = 오늘 이후 첫 선·옵 동시만기(3·6·9·12월). 코스피200·코스닥150·KRX300 선물 공통."""
    evs = _load("expiries.json", {}).get("events", [])
    ds = sorted(e["date"] for e in evs if e.get("type") == "선·옵 동시만기" and e["date"] > today_iso)
    return ds[0] if ds else ""

def merged_dividends():
    """확정(dividends.json) + 예상(research.json) 병합 → 회차별 1건."""
    conf = _load("dividends.json", {}).get("events", [])
    pred = _load("research.json", {}).get("events", [])
    out = []
    for e in conf:
        if not e.get("record_date"):
            continue
        amt = _amt(e.get("per_share"))
        out.append({**e, "dps": amt, "status": "확정" if amt else "금액미정"})
    for p in pred:
        if not p.get("record_date"):
            continue
        same = [c for c in out if c["stock"] == p["stock"] and c.get("status") != "예상"
                and (c.get("div_type") or "") in ("", p.get("div_type") or "")
                and _days(c["record_date"], p["record_date"]) <= SAME_ROUND_DAYS]
        if same:
            for c in same:   # 기준일은 공시됐는데 금액이 없으면 예상 금액을 빌림
                if c["status"] == "금액미정" and _amt(p.get("per_share")):
                    c["dps"], c["status"], c["basis"] = _amt(p["per_share"]), "금액예상", p.get("basis", "")
            continue
        amt = _amt(p.get("per_share"))
        out.append({**p, "dps": amt, "status": "예상" if amt else "금액미정", "url": ""})
    return out

def build():
    pdfs = index_pdf.load().get("indices", {})
    today = datetime.date.today().isoformat()
    exp = near_expiry(today)
    divs = [d for d in merged_dividends() if d.get("ex_date") and today < d["ex_date"] <= (exp or today)]
    indices = []
    for cfg in index_pdf.INDICES:
        ix = pdfs.get(cfg["key"]) or {}
        rows = ix.get("rows") or []
        card = {"key": cfg["key"], "name": cfg["name"], "etf": cfg["etf"], "etf_code": cfg["etf_code"],
                "pdf_date": ix.get("pdf_date", ""), "index_close": ix.get("index_close"),
                "index_date": ix.get("index_date", ""), "fetched_at": ix.get("fetched_at", ""),
                "error": ix.get("error", ""), "n_stocks": len(rows),
                "total": 0.0, "confirmed": 0.0, "estimated": 0.0, "days": [], "undetermined": []}
        den = sum(r["value"] or 0 for r in rows)
        lvl = ix.get("index_close")
        if not rows or not den or not lvl:
            indices.append(card)
            continue
        card["pdf_value"] = den
        pos = {r["code"]: r for r in rows}
        by_day = {}
        for d in divs:
            r = pos.get(d["stock"])
            if not r or not r.get("qty"):
                continue
            if not d["dps"]:
                card["undetermined"].append({"corp": d["corp"], "stock": d["stock"], "ex_date": d["ex_date"],
                                             "div_type": d.get("div_type", ""), "url": d.get("url", "")})
                continue
            price = r["value"] / r["qty"]
            pt = lvl * r["qty"] * d["dps"] / den
            by_day.setdefault(d["ex_date"], []).append({
                "corp": d["corp"], "stock": d["stock"], "div_type": d.get("div_type", ""), "status": d["status"],
                "dps": d["dps"], "qty": r["qty"], "price": round(price, 2), "weight": round(r["value"] / den * 100, 3),
                "yield": round(d["dps"] / price * 100, 4), "points": round(pt, 4),
                "record_date": d["record_date"], "url": d.get("url", ""), "basis": d.get("basis", "")})
            card["total"] += pt
            card["confirmed" if d["status"] == "확정" else "estimated"] += pt
        cum = 0.0
        for day in sorted(by_day):
            items = sorted(by_day[day], key=lambda x: -x["points"])
            s = sum(x["points"] for x in items)
            cum += s
            card["days"].append({"ex_date": day, "points": round(s, 4), "cum": round(cum, 4),
                                 "remain": round(card["total"] - cum + s, 4), "items": items})
        for k in ("total", "confirmed", "estimated"):
            card[k] = round(card[k], 4)
        indices.append(card)
    payload = {"generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "today": today, "expiry": exp, "formula": FORMULA,
               "rule": "오늘 < 배당락일 ≤ 근월물 만기일 (오늘 배당락분 제외)", "indices": indices}
    save_json(OUT, payload)
    return payload

def refresh():
    """PDF·지수 종가 필요시 수집(하루 1회) 후 재계산 — monitor 가 배당/리서치 갱신 뒤 호출."""
    try:
        index_pdf.ensure()
    except Exception as ex:
        print(f"  [배당포인트] PDF 수집 오류: {ex}")
    p = build()
    print("  [배당포인트] " + " · ".join(f"{c['name']} {c['total']:.2f}pt" for c in p["indices"]) + f" (만기 {p['expiry']})")
    return p

if __name__ == "__main__":
    p = refresh()
    for c in p["indices"]:
        print(f"{c['name']} ({c['etf']}): 합계 {c['total']:.3f} = 확정 {c['confirmed']:.3f} + 예상 {c['estimated']:.3f} | "
              f"{len(c['days'])}일 · 미정 {len(c['undetermined'])} | {c['error'] or 'OK'}")
