# -*- coding: utf-8 -*-
"""재무제표 수집 — OpenDART 정기보고서(1분기·반기·3분기·사업, 정정 포함) XBRL 원본(fnlttXbrl), 공시마다.
원본은 data/raw/xbrl/<종목>/<접수번호>.zip 에 보관하고, 그 보고서 기간(가장 늦은 끝 날짜)의 대표 숫자만 store xbrl 에 뽑는다.
대표 숫자 = 연결·별도 축 말고 다른 축(사업부문·주식 종류·자본 구성요소)이 없는 숫자. 기간은 원본의 시작·끝(시점은 시작 없음) 그대로 — 연도를 만들지 않음.
store xbrl = {종목: {"asked": [접수번호], "reports": {접수번호: {"dt" 접수일, "nm" 보고서명, "end" 기간 끝, "facts": [[계정, 연결·별도, 시작, 끝, 값]]}}}}
뽑는 계정을 바꾸면 VERSION 을 올림 — 보관한 원본에서 다시 뽑음(다시 받지 않음). 원본이 없는 보고서(금융업 2023년 3분기 전 등)는 접수 RETRY_DAYS 뒤 다시 묻지 않음"""
import io, os, re, datetime, zipfile, urllib.request
from core import store, universe
from core.paths import DATA_DIR
from ingest.sources.dart import KEY, corp_code, periodic

RAW = os.path.join(DATA_DIR, "raw", "xbrl")
SINCE = "20190101"
RETRY_DAYS = 30
MAX_GET = 4000   # 한 번에 받는 원본 수 — OpenDART 하루 한도를 다른 원천과 나눠 씀
SAVE_EVERY = 200
VERSION = 1
CONCEPTS = ("ifrs-full:ProfitLossAttributableToOwnersOfParent", "ifrs-full:ProfitLoss", "ifrs-full:BasicEarningsLossPerShare",
            "ifrs-full:Revenue", "dart:OperatingIncomeLoss", "ifrs-full:ProfitLossBeforeTax",
            "ifrs-full:RetainedEarnings", "ifrs-full:CashAndCashEquivalents", "ifrs-full:Equity", "ifrs-full:EquityAttributableToOwnersOfParent",
            "ifrs-full:Liabilities",
            "ifrs-full:CashFlowsFromUsedInOperatingActivities", "ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
            "ifrs-full:PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities", "dart:AcquisitionOfTreasuryShares",
            "ifrs-full:DividendsPaidClassifiedAsFinancingActivities")
BASIS = {"ifrs-full:ConsolidatedMember": "연결", "ifrs-full:SeparateMember": "별도"}
AXIS = "ifrs-full:ConsolidatedAndSeparateFinancialStatementsAxis"


def parse(z):
    """XBRL 압축 → (기간 끝, 대표 숫자 [[계정, 연결·별도, 시작, 끝, 값]]) — 그 보고서 기간(가장 늦은 끝)의 숫자만"""
    x = z.read(next(n for n in z.namelist() if n.endswith(".xbrl"))).decode("utf-8", "replace")
    ctx = {}
    for m in re.finditer(r'<(?:xbrli:)?context id="([^"]+)">(.*?)</(?:xbrli:)?context>', x, re.S):
        per = dict(re.findall(r"<(?:xbrli:)?(startDate|endDate|instant)>\s*([^<\s]+)\s*<", m[2]))
        dims = re.findall(r'dimension="([^"]+)">\s*([^<\s]+)\s*<', m[2])
        if "typedMember" in m[2] or any(a != AXIS for a, _ in dims):
            continue
        ctx[m[1]] = (per.get("startDate", ""), per.get("endDate") or per.get("instant", ""), BASIS.get(dims[0][1], "?") if dims else "")
    rows = []
    for c in CONCEPTS:
        pre, name = c.split(":")
        for m in re.finditer(rf'<{pre}:{name}\s([^>]*?)>\s*(-?[\d.]+)\s*</{pre}:{name}>', x):
            ref = re.search(r'contextRef="([^"]+)"', m[1])
            if ref and ref[1] in ctx:
                start, end, basis = ctx[ref[1]]
                rows.append([c, basis, start, end, float(m[2])])
    end = max((r[3] for r in rows), default="")
    return end, sorted({tuple(r) for r in rows if r[3] == end})

def run():
    data, got, saved = dict(store.load("xbrl")), 0, 0
    old = (datetime.date.today() - datetime.timedelta(days=RETRY_DAYS)).strftime("%Y%m%d")
    for s in sorted(universe.div()):
        cc = corp_code(s)
        if not cc or got >= MAX_GET:
            continue
        d = data.setdefault(s, {"asked": [], "reports": {}})
        for no, rep in d["reports"].items():
            if rep.get("v") != VERSION:
                rep["end"], rows = parse(zipfile.ZipFile(os.path.join(RAW, s, no + ".zip")))
                rep.update(facts=[list(x) for x in rows], v=VERSION)
        try:
            todo = [r for r in periodic(cc, SINCE) if r["rcept_no"] not in d["asked"]]
        except Exception as ex:
            print(f"  [재무제표] {s} 목록 실패 — 다음에 다시: {ex}")
            continue
        for r in sorted(todo, key=lambda r: r["rcept_no"]):
            no, path = r["rcept_no"], os.path.join(RAW, s, r["rcept_no"] + ".zip")
            if not os.path.exists(path):
                if got >= MAX_GET:
                    break
                try:
                    raw = urllib.request.urlopen(f"https://opendart.fss.or.kr/api/fnlttXbrl.xml?crtfc_key={KEY}&rcept_no={no}&reprt_code=11011", timeout=60).read()
                except Exception as ex:
                    print(f"  [재무제표] {s} {no} 받기 실패 — 다음에 다시: {ex}")
                    continue
                got += 1
                if raw[:2] != b"PK":
                    status = re.search(rb"<status>(\d+)</status>", raw)
                    if not status or status[1] != b"014":   # 014 = 원본 없음, 그 밖(020 하루 한도 초과 등)은 오늘 그만
                        print(f"  [재무제표] 받기 멈춤 — {raw[:120]!r}")
                        store.save("xbrl", data)
                        return bool(got)
                    if no[:8] < old:
                        d["asked"].append(no)
                    continue
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, "wb").write(raw)
            end, rows = parse(zipfile.ZipFile(path))
            d["reports"][no] = {"dt": no[:8], "nm": r["report_nm"], "end": end, "facts": [list(x) for x in rows], "v": VERSION}
            d["asked"].append(no)
        if got - saved >= SAVE_EVERY:
            store.save("xbrl", data)
            saved = got
    store.save("xbrl", data)
    return bool(got)
