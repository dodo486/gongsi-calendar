# -*- coding: utf-8 -*-
"""배당 재원 수집 — OpenDART 전체 재무제표(fnlttSinglAcntAll) 현금흐름표, 사업연도별 (원).
영업현금흐름(ocf)·유형자산 취득(capex)·무형자산 취득(intang)·자기주식 취득(buyback)·배당금 지급(div_paid).
사업보고서는 3년치(당기·전기·전전기), 3분기보고서는 9개월 누적 당기만(q3_ 앞붙임). 연결 없으면 별도.
받은 (종목, 보고서)는 다시 묻지 않음 — 아직 안 나온 보고서는 다음에 다시. IP 차단을 피해 순차 → store fin"""
import datetime
from core import store, universe
from core.fmt import num
from ingest.sources.dart import api, KEY, corp_code

ACCOUNTS = {"ifrs-full_CashFlowsFromUsedInOperatingActivities": "ocf",
            "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities": "capex",
            "ifrs-full_PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities": "intang",
            "dart_AcquisitionOfTreasuryShares": "buyback",
            "ifrs-full_DividendsPaidClassifiedAsFinancingActivities": "div_paid"}


def run():
    today = datetime.date.today()
    last = today.year - 1
    asks = ([(str(y), "11011", "", (("thstrm_amount", 0), ("frmtrm_amount", 1), ("bfefrmtrm_amount", 2))) for y in (last, last - 3)]
            + [(f"{y}Q3", "11014", "q3_", (("thstrm_amount", 0),)) for y in range(last - 5, today.year + 1)])
    data, n = dict(store.load("fin")), 0
    for s in sorted(universe.div()):
        cc = corp_code(s)
        for tag, reprt, pre, cols in asks:
            if not cc or tag in data.get(s, {}).get("asked", []):
                continue
            y = int(tag[:4])
            try:
                for fs in ("CFS", "OFS"):
                    j = api({"crtfc_key": KEY, "corp_code": cc, "bsns_year": str(y), "reprt_code": reprt, "fs_div": fs}, "fnlttSinglAcntAll", tries=2)
                    if j.get("status") == "000":
                        break
            except Exception as ex:
                print(f"  [재원] {s} {tag} 받기 실패 — 다음에 다시: {ex}")
                continue
            d = data.setdefault(s, {"asked": [], "years": {}})
            for r in j.get("list", []):
                k = ACCOUNTS.get(r["account_id"]) if r["sj_div"] in ("CF", "SCF") else None
                if k:
                    for col, back in cols:
                        v = num(r.get(col))
                        if v is not None:
                            d["years"].setdefault(str(y - back), {}).setdefault(pre + k, v)
            if j.get("status") == "000" or y < last:
                d["asked"].append(tag)
            n += 1
            if n % 50 == 0:
                store.save("fin", data)
    if n:
        store.save("fin", data)
    return bool(n)
