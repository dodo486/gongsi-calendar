# -*- coding: utf-8 -*-
"""네이버 연간 주당배당금 컨센서스(E) — 과거 값을 안 주므로 매일 받아 값이 바뀐 날만 쌓는다 → store dps_consensus"""
import datetime
from concurrent.futures import ThreadPoolExecutor
from core import store, universe
from ingest.sources.naver import get

def _dps(code):
    try:
        f = get(f"https://m.stock.naver.com/api/stock/{code}/finance/annual")["financeInfo"] or {}
    except Exception:
        return code, {}
    cons = {t["key"][:4] for t in f.get("trTitleList") or [] if t.get("isConsensus") == "Y"}
    row = next((r["columns"] for r in f.get("rowList") or [] if r["title"] == "주당배당금"), {})
    vals = {k[:4]: ((c or {}).get("value") or "").replace(",", "") for k, c in row.items()}
    return code, {fy: int(v) for fy, v in vals.items() if fy in cons and v.isdigit()}

def run():
    data, today, n = dict(store.load("dps_consensus")), datetime.date.today().isoformat(), 0
    with ThreadPoolExecutor(4) as ex:
        for code, got in ex.map(_dps, sorted(universe.div())):
            h = data.setdefault(code, [])
            for fy, v in got.items():
                if next((x["dps"] for x in reversed(h) if x["fy"] == fy), None) != v:
                    h.append({"date": today, "fy": fy, "dps": v})
                    n += 1
    if n:
        store.save("dps_consensus", data)
    return bool(n)
