# -*- coding: utf-8 -*-
"""고배당기업 수집 — KIND 기업 밸류업 > 고배당기업 현황(거래소가 회사의 밸류업 공시 '고배당기업 여부: 해당'으로 만든 목록), 사업연도마다 제도 첫해부터.
행 = 회사 · 사업연도(그 실적으로 다음 해 받는 배당이 분리과세 대상) · 회사가 밝힌 배당성향(%) · 이익배당금 증가율(%) · 근거 공시 접수번호.
KIND 는 가장 최근 공시 기준이라 '미해당'으로 정정되면 목록에서 빠짐 — 매일 받아 쌓고, 빠지면 gone(빠진 걸 본 날)을 남김.
store kind_highdiv = {"종목|사업연도": {code, name, fy, payout, growth, rcept_no, seen 처음 본 날, last 마지막 본 날, gone}}"""
import re, html, datetime
from core import store
from ingest.sources.kind import get

URL = "https://kind.krx.co.kr/valueup/dividend.do"
FIRST_YEAR = 2025   # 조특법 §104의27 첫 판정 사업연도 (2026년 받는 배당부터)
PAGE = 100


def rows(fy, market):
    """사업연도 fy · 시장(1 유가, 2 코스닥) 목록 전부 → [{code, name, fy, payout, growth, rcept_no}]"""
    out, page = [], 1
    while True:
        t = get(URL, [("method", "valueupHighDividendSub"), ("currentPageSize", PAGE), ("pageIndex", page), ("orderMode", "0"), ("orderStat", "D"),
                      ("forward", "valueupHighDividend_sub"), ("marketType", market), ("selYear", fy), ("searchCorpName", ""), ("acntclsMm", "")],
                ref=URL + "?method=valueupHighDividendMain")
        got = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
            isur, no = re.search(r"companysummary_open\('(\w+)'\)", tr), re.search(r"openDisclsViewer\('(\d{14})'", tr)
            tds = [html.unescape(re.sub(r"<[^>]+>", "", td)).strip() for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if isur and no and len(tds) == 7:
                num = lambda s: float(s) if re.fullmatch(r"-?\d*\.?\d+", s) else None
                got.append({"code": isur[1] + "0", "name": tds[1], "fy": int(tds[3]), "payout": num(tds[5]), "growth": num(tds[6]), "rcept_no": no[1]})
        out += got
        if len(got) < PAGE:
            return out
        page += 1

def run():
    today = datetime.date.today().isoformat()
    data = dict(store.load("kind_highdiv"))
    now = {f"{r['code']}|{r['fy']}": r for fy in range(FIRST_YEAR, datetime.date.today().year + 1) for m in ("1", "2") for r in rows(fy, m)}
    changed = False
    for k, r in now.items():
        old = data.get(k, {})
        new = {**r, "seen": old.get("seen", today), "last": today}
        changed |= {x: old.get(x) for x in r} != r or "gone" in old
        data[k] = new
    for k, old in data.items():
        if k not in now and "gone" not in old:
            data[k] = {**old, "gone": today}
            changed = True
    store.save("kind_highdiv", data)
    return changed
