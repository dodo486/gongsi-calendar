# -*- coding: utf-8 -*-
"""배당 1단계 수집 — OpenDART 사업보고서 배당에 관한 사항(alotMatter): 사업연도별 EPS·배당성향·주당 현금배당금(보통주).
보고서 하나가 3년치(당기·전기·전전기). 받은 (종목, 연도)는 다시 묻지 않음. IP 차단을 피해 순차 → data/div_alot.json"""
import os, datetime
from fetch import api, KEY, corp_code
from base import DATA_DIR, save_json
import dividends as dv
from div_collect import _load

PATH = os.path.join(DATA_DIR, "div_alot.json")
ROWS = {"(연결)주당순이익(원)": "eps", "(연결)현금배당성향(%)": "payout", "주당 현금배당금(원)": "dps"}
COLS = (("thstrm", 0), ("frmtrm", 1), ("lwfr", 2))

def _num(s):
    s = (s or "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None

def collect(years=None):
    last = datetime.date.today().year - 1
    store = _load(PATH, {})
    for s in sorted(dv.div_universe()):
        cc = corp_code(s)
        for y in years or (last, last - 3):
            if not cc or str(y) in store.get(s, {}).get("asked", []):
                continue
            try:
                j = api({"crtfc_key": KEY, "corp_code": cc, "bsns_year": str(y), "reprt_code": "11011"}, "alotMatter", tries=2)
            except Exception as ex:
                print(f"  [배당 이력] {s} {y} 받기 실패 — 다음에 다시: {ex}")
                continue
            d = store.setdefault(s, {"asked": [], "years": {}})
            for r in j.get("list", []):
                k = ROWS.get(r["se"])
                if k and (k != "dps" or r.get("stock_knd") == "보통주"):
                    for col, back in COLS:
                        v = _num(r.get(col))
                        if v is not None:
                            d["years"].setdefault(str(y - back), {}).setdefault(k, v)
            d["asked"].append(str(y))
        if len(store) % 50 == 0:
            save_json(PATH, store)
    save_json(PATH, store)
    print(f"저장: div_alot.json — 종목 {len(store)}")
    return store

if __name__ == "__main__":
    collect()
