# -*- coding: utf-8 -*-
"""배당 1단계 수집 — OpenDART 정기보고서 배당에 관한 사항(alotMatter), 사업연도별. 한 칸 = 처음 나온 보고서 하나의 값 — 그해 보고서의 당기 열,
그해 보고서가 없으면(상장·분할 전) 그 값을 처음 실은 뒤 보고서의 전기·전전기 열. 나중 보고서가 고친 값으로 덮지 않음.
사업보고서: EPS·배당성향·연간 주당 현금배당금(보통주), 9년치. 9년인 까닭: 백테스트 첫 해(5년 전)에도 추세·안 깎음이 쓰는 3년 이력이 있어야 함.
3분기보고서: 9개월 누적 EPS(q3_eps)·연결·별도 순이익(q3_ni·q3_ni_sep, 백만원)·그때까지 정한 주당 배당금(q3_dps), 6년 + 올해.
known[사업연도][칸] = 그 값을 실은 보고서의 처음 접수일(정정본 값이어도 정정 전 접수일 — 정정이 배당 표를 바꾸는 일은 드묾) — 과거 시점 계산은 이 날짜로 거름.
받은 (종목, 보고서)는 다시 묻지 않음 — 아직 안 나온 보고서는 다음에 다시. IP 차단을 피해 순차 → store alot"""
import datetime
from core import store, universe
from core.fmt import num
from ingest.sources.dart import api, KEY, corp_code, periodic

ANNUAL = {"(연결)주당순이익(원)": "eps", "(연결)현금배당성향(%)": "payout", "주당 현금배당금(원)": "dps"}
Q3 = {"(연결)주당순이익(원)": "q3_eps", "주당 현금배당금(원)": "q3_dps", "(연결)당기순이익(백만원)": "q3_ni", "(별도)당기순이익(백만원)": "q3_ni_sep"}


def run():
    today = datetime.date.today()
    last = today.year - 1
    asks = ([(f"A{y}", y, "11011", ANNUAL, (("thstrm", 0), ("frmtrm", 1), ("lwfr", 2))) for y in range(last - 8, last + 1)]   # 오래된 보고서부터 — 처음 실은 보고서가 이김
            + [(f"Q{y}", y, "11014", Q3, (("thstrm", 0),)) for y in range(last - 5, today.year + 1)])
    data, n = dict(store.load("alot")), 0
    for s in sorted(universe.div()):
        cc, first = corp_code(s), None
        for tag, y, reprt, rows, cols in asks:
            if not cc or tag in data.get(s, {}).get("asked", []):
                continue
            if first is None:
                first = {r["rcept_no"]: r["first"] for r in periodic(cc, f"{last - 9}0101")}
            try:
                j = api({"crtfc_key": KEY, "corp_code": cc, "bsns_year": str(y), "reprt_code": reprt}, "alotMatter", tries=2)
            except Exception as ex:
                print(f"  [배당 이력] {s} {tag} 받기 실패 — 다음에 다시: {ex}")
                continue
            d = data.setdefault(s, {"asked": [], "years": {}, "known": {}})
            for (col, back), r in sorted(((c, r) for r in j.get("list", []) for c in cols), key=lambda x: x[0][1]):   # 당기 열 먼저
                k, v, fy = rows.get(r["se"]), num(r.get(col)), str(y - back)
                if k and v is not None and r.get("stock_knd") in ("-", "보통주", None) and k not in d["years"].get(fy, {}):
                    d["years"].setdefault(fy, {})[k] = v
                    d["known"].setdefault(fy, {})[k] = first.get(r["rcept_no"], r["rcept_no"][:8])
            if j.get("status") == "000" or j.get("status") == "013" and y < last:   # 013 = 그 보고서에 배당 표 없음, 그 밖(020 한도 초과 등)은 다음에 다시
                d["asked"].append(tag)
            n += 1
            if n % 50 == 0:
                store.save("alot", data)
    if n:
        store.save("alot", data)
    return bool(n)
