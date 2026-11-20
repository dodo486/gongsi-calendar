# -*- coding: utf-8 -*-
"""배당 1단계 수집 — 네이버 연간 주당배당금 컨센서스(E). 과거 값을 안 주므로 매일 받아 값이 바뀐 날만 쌓는다 → data/div_consensus.json"""
import os, datetime
from concurrent.futures import ThreadPoolExecutor
from base import DATA_DIR, save_json
from consensus import _get
import dividends as dv
from div_collect import _load

PATH = os.path.join(DATA_DIR, "div_consensus.json")

def _dps(code):
    try:
        f = _get(f"https://m.stock.naver.com/api/stock/{code}/finance/annual")["financeInfo"] or {}
    except Exception:
        return code, {}
    cons = {t["key"][:4] for t in f.get("trTitleList") or [] if t.get("isConsensus") == "Y"}
    row = next((r["columns"] for r in f.get("rowList") or [] if r["title"] == "주당배당금"), {})
    vals = {k[:4]: ((c or {}).get("value") or "").replace(",", "") for k, c in row.items()}
    return code, {fy: int(v) for fy, v in vals.items() if fy in cons and v.isdigit()}

def collect():
    store, today = _load(PATH, {}), datetime.date.today().isoformat()
    with ThreadPoolExecutor(4) as ex:
        for code, got in ex.map(_dps, sorted(dv.div_universe())):
            h = store.setdefault(code, [])
            for fy, v in got.items():
                if next((x["dps"] for x in reversed(h) if x["fy"] == fy), None) != v:
                    h.append({"date": today, "fy": fy, "dps": v})
    save_json(PATH, store)
    print(f"저장: div_consensus.json — 컨센 있는 종목 {sum(1 for h in store.values() if h)}")
    return store

if __name__ == "__main__":
    collect()
