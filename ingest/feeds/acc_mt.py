# -*- coding: utf-8 -*-
"""결산월 — OpenDART 회사정보 acc_mt. 답을 받은 종목은 다시 묻지 않음('결산월 없음' 답도 기록, 받기 실패만 다음에 다시) → store acc_mt"""
from concurrent.futures import ThreadPoolExecutor
from core import store, universe
from ingest.sources.dart import api, KEY, corp_code

def run():
    cache = store.load("acc_mt")
    def one(s):
        try:
            cc = corp_code(s)
            if not cc:
                return s, ""        # DART 고유번호 없음 = 물어볼 결산월 없음
            j = api({"crtfc_key": KEY, "corp_code": cc}, "company")
        except Exception:
            return s, None
        return s, {"000": j.get("acc_mt", ""), "013": ""}.get(j.get("status"))
    missing = sorted(universe.div() - set(cache))
    if not missing:
        return False
    print(f"  [결산월] {len(missing)}종목 조회")
    first = one(missing[0])
    if first[1] is None:
        print("  [!] 결산월 조회 실패 — 이번 계산은 12월 결산 가정(종목 근거에 표시)")
        return False
    with ThreadPoolExecutor(2) as ex:
        got = {s: m for s, m in [first, *ex.map(one, missing[1:])] if m is not None}
    store.save("acc_mt", {**cache, **got})
    return True
