# -*- coding: utf-8 -*-
"""배당 1단계 수집 — OpenDART 정기보고서 배당에 관한 사항(alotMatter), 사업연도별.
사업보고서: EPS·배당성향·연간 주당 현금배당금(보통주), 보고서 하나가 3년치(당기·전기·전전기).
3분기보고서: 9개월 누적 EPS(q3_eps)·연결·별도 순이익(q3_ni·q3_ni_sep, 백만원)·그때까지 정한 주당 배당금(q3_dps), 당기만 — 사업보고서와 같은 6년 + 올해.
받은 (종목, 보고서)는 다시 묻지 않음 — 아직 안 나온 보고서는 다음에 다시. IP 차단을 피해 순차 → store alot"""
import datetime
from core import store, universe
from core.fmt import num
from ingest.sources.dart import api, KEY, corp_code

ANNUAL = {"(연결)주당순이익(원)": "eps", "(연결)현금배당성향(%)": "payout", "주당 현금배당금(원)": "dps"}
Q3 = {"(연결)주당순이익(원)": "q3_eps", "주당 현금배당금(원)": "q3_dps", "(연결)당기순이익(백만원)": "q3_ni", "(별도)당기순이익(백만원)": "q3_ni_sep"}


def run():
    today = datetime.date.today()
    last = today.year - 1
    asks = ([(str(y), "11011", ANNUAL, (("thstrm", 0), ("frmtrm", 1), ("lwfr", 2))) for y in (last, last - 3)]
            + [(f"{y}Q3", "11014", Q3, (("thstrm", 0),)) for y in range(last - 5, today.year + 1)])
    data, n = dict(store.load("alot")), 0
    for s in sorted(universe.div()):
        cc = corp_code(s)
        for tag, reprt, rows, cols in asks:
            if not cc or tag in data.get(s, {}).get("asked", []):
                continue
            y = int(tag[:4])
            try:
                j = api({"crtfc_key": KEY, "corp_code": cc, "bsns_year": str(y), "reprt_code": reprt}, "alotMatter", tries=2)
            except Exception as ex:
                print(f"  [배당 이력] {s} {tag} 받기 실패 — 다음에 다시: {ex}")
                continue
            d = data.setdefault(s, {"asked": [], "years": {}})
            for r in j.get("list", []):
                k = rows.get(r["se"])
                if k and r.get("stock_knd") in ("-", "보통주", None):
                    for col, back in cols:
                        v = num(r.get(col))
                        if v is not None:
                            d["years"].setdefault(str(y - back), {}).setdefault(k, v)
            if j.get("status") == "000" or y < last:
                d["asked"].append(tag)
            n += 1
            if n % 50 == 0:
                store.save("alot", data)
    if n:
        store.save("alot", data)
    return bool(n)
