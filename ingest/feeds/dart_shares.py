# -*- coding: utf-8 -*-
"""배당 받는 주식 수 — OpenDART 주식의 총수 현황(stockTotqySttus) 유통주식 수(발행 − 자기주식, 보통+종류 합계), 결산일별.
반기보고서(올해)·사업보고서(작년)만 이 표가 있음(분기보고서는 빈칸). 받은 (종목, 보고서)는 다시 묻지 않음 → store shares"""
import datetime
from core import store, universe
from core.fmt import num
from ingest.sources.dart import api, KEY, corp_code


def run():
    today = datetime.date.today()
    asks = [(f"A{today.year - 1}", "11011"), (f"H{today.year}", "11012")]
    data, n = dict(store.load("shares")), 0
    for s in sorted(universe.div()):
        cc = corp_code(s)
        for tag, reprt in asks:
            if not cc or tag in data.get(s, {}).get("asked", []):
                continue
            try:
                j = api({"crtfc_key": KEY, "corp_code": cc, "bsns_year": tag[1:], "reprt_code": reprt}, "stockTotqySttus", tries=2)
            except Exception as ex:
                print(f"  [주식 수] {s} {tag} 받기 실패 — 다음에 다시: {ex}")
                continue
            d = data.setdefault(s, {"asked": [], "dates": {}})
            row = next((r for r in j.get("list", []) if r.get("se") == "합계" and num(r.get("distb_stock_co"))), None)
            if row:
                d["dates"][row["stlm_dt"]] = {"n": num(row["distb_stock_co"]), "dt": row["rcept_no"][:8]}
            if j.get("status") == "000":   # 아직 안 낸 보고서(013)는 다음에 다시
                d["asked"].append(tag)
            n += 1
            if n % 50 == 0:
                store.save("shares", data)
    if n:
        store.save("shares", data)
    return bool(n)
