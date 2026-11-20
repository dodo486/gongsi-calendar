# -*- coding: utf-8 -*-
"""지수배당포인트 — 근월물·차월물(다음 두 선·옵 동시만기)까지 배당락으로 빠질 지수 포인트 → data/divpoints.json

공식:  배당포인트 = 지수(전일 종가) × Σ(PDF수량 × 주당배당금) ÷ Σ(PDF 주식 평가금액)
        = 지수 × (배당 때문에 지수가 빠지는 비율)   ← 지수를 곱하는 건 '비율 → 포인트' 환산
대상:  오늘 < 배당락일 ≤ 차월물 만기일, 배당락일이 든 첫 만기로 월물 배정 (오늘 배당락분은 이미 가격에 반영돼 제외)

배당 출처: 배당 장부 div_book.json (확정·예상을 회차마다 한 줄로 합친 것 — divbook.py).
월물 미정: 날짜가 확실하지 않은 구분(GRADES firm=False) 회차 중, 오늘+14일 기준일(상법 §354 2주 공고)로도
근월물 만기 전에 배당락이 올 수 있는 것 — 예상 배당락 월물에 넣되 따로 합산 (설계 §4.6).

  python -m derive.dividend.points
"""
import os, json, datetime
from core.paths import save_json, OUT_DIR
from core.krx_cal import t_minus
from core.universe import INDICES, PDF_PATH
from core.paths import load_json

OUT = os.path.join(OUT_DIR, "divpoints.json")
FORMULA = "배당포인트 = 지수(전일종가) × Σ(PDF수량 × 주당배당금) ÷ Σ(PDF 주식평가액)"

def _load(name, default):
    try:
        return json.load(open(os.path.join(OUT_DIR, name), encoding="utf-8"))
    except Exception:
        return default

def _amt(s):
    s = str(s or "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None

def expiries(today_iso):
    """근월물·차월물 만기 = 오늘 이후 선·옵 동시만기(3·6·9·12월) 앞 두 개. 코스피200·코스닥150·KRX300 선물 공통."""
    evs = _load("expiries.json", {}).get("events", [])
    return sorted(e["date"] for e in evs if e.get("type") == "선·옵 동시만기" and e["date"] > today_iso)[:2]

def book_dividends():
    """배당 장부(div_book.json) 회차 → 포인트 계산용 (금액·상태·월물 미정 여부는 장부 구분에서)"""
    book = _load("div_book.json", {})
    grades = book.get("grades", {})
    status = lambda r: ("확정" if r["amount_src"] == "공시" else "금액예상" if r["kind"] == "div" else "예상") if r.get("per_share") else "금액미정"
    return [{**r, "dps": _amt(r.get("per_share")), "status": status(r), "src": "공시" if r["kind"] == "div" else "예상",
             "firm": grades[r["grade"]]["firm"]} for r in book.get("rows", [])], grades

def build():
    pdfs = load_json(PDF_PATH, {}).get("indices", {})
    today = datetime.date.today()
    exps = expiries(today.isoformat())
    earliest_ex = t_minus((today + datetime.timedelta(days=14)).isoformat(), 1)
    rows, _ = book_dividends()
    divs = [d for d in rows if exps and today.isoformat() < d["ex_date"] <= exps[-1]]
    for d in divs:
        d["undecided"] = not d["firm"] and earliest_ex <= exps[0]
    indices = []
    for cfg in INDICES:
        ix = pdfs.get(cfg["key"]) or {}
        rows = ix.get("rows") or []
        card = {"key": cfg["key"], "name": cfg["name"], "etf": cfg["etf"], "etf_code": cfg["etf_code"],
                "pdf_date": ix.get("pdf_date", ""), "index_close": ix.get("index_close"),
                "index_date": ix.get("index_date", ""), "fetched_at": ix.get("fetched_at", ""),
                "error": ix.get("error", ""), "n_stocks": len(rows), "months": []}
        den = sum(r["value"] or 0 for r in rows)
        lvl = ix.get("index_close")
        pos = {r["code"]: r for r in rows}
        for i, exp in enumerate(exps):
            lo = exps[i - 1] if i else today.isoformat()
            m = {"expiry": exp, "label": "근월물" if i == 0 else "차월물",
                 "total": 0.0, "confirmed": 0.0, "estimated": 0.0, "undecided": 0.0, "days": [], "undetermined": []}
            card["months"].append(m)
            if not rows or not den or not lvl:
                continue
            by_day = {}
            for d in divs:
                r = pos.get(d["stock"])
                if not (lo < d["ex_date"] <= exp) or not r or not r.get("qty"):
                    continue
                if not d["dps"]:
                    m["undetermined"].append({"id": d["id"], "corp": d["corp"], "stock": d["stock"], "ex_date": d["ex_date"],
                                              "div_type": d.get("div_type", ""), "url": d.get("url", ""), "undecided": d["undecided"]})
                    continue
                price = r["value"] / r["qty"]
                pt = lvl * r["qty"] * d["dps"] / den
                by_day.setdefault(d["ex_date"], []).append({
                    "id": d["id"], "corp": d["corp"], "stock": d["stock"], "div_type": d.get("div_type", ""), "status": d["status"],
                    "undecided": d["undecided"],
                    "dps": d["dps"], "qty": r["qty"], "price": round(price, 2), "weight": round(r["value"] / den * 100, 3),
                    "yield": round(d["dps"] / price * 100, 4), "points": round(pt, 4),
                    "record_date": d["record_date"], "url": d.get("url", ""), "basis": d.get("basis", "")})
                m["total"] += pt
                m["undecided" if d["undecided"] else "confirmed" if d["status"] == "확정" else "estimated"] += pt
            cum = 0.0
            for day in sorted(by_day):
                items = sorted(by_day[day], key=lambda x: -x["points"])
                s = sum(x["points"] for x in items)
                cum += s
                m["days"].append({"ex_date": day, "points": round(s, 4), "cum": round(cum, 4),
                                  "remain": round(m["total"] - cum + s, 4), "items": items})
            for k in ("total", "confirmed", "estimated", "undecided"):
                m[k] = round(m[k], 4)
        if den:
            card["pdf_value"] = den
        indices.append(card)
    payload = {"generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "today": today.isoformat(), "expiries": exps, "earliest_ex": earliest_ex, "formula": FORMULA,
               "rule": "오늘 < 배당락일 ≤ 차월물 만기, 배당락일이 든 첫 만기로 월물 배정 (오늘 배당락분 제외)", "indices": indices}
    save_json(OUT, payload)
    return payload

def refresh():
    p = build()
    print("  [배당포인트] " + " · ".join(f"{c['name']} " + "/".join(f"{m['total']:.2f}" for m in c["months"]) + "pt" for c in p["indices"])
          + f" (만기 {' / '.join(p['expiries'])})")
    return p

if __name__ == "__main__":
    p = refresh()
    for c in p["indices"]:
        for m in c["months"]:
            print(f"{c['name']} {m['label']} {m['expiry']}: 합계 {m['total']:.3f} = 확정 {m['confirmed']:.3f} + 예상 {m['estimated']:.3f}"
                  f" + 월물미정 {m['undecided']:.3f} | {len(m['days'])}일 · 금액미정 {len(m['undetermined'])} | {c['error'] or 'OK'}")
