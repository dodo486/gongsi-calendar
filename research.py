# -*- coding: utf-8 -*-
"""배당 날짜 유추 — 설계 정본 docs/배당예측-워크플로우.md (§4 워크플로우, §5 검증)

수집  DART 목록(공용 저장본 fetch.listing) → 원문 파싱(rcept_no 캐시)
      거래소공시(I): 배당결정 · 배당 기준일 공고 · 기준일 안내 자율공시 · 주총 소집결의/결과 · 주총용 명부폐쇄 · 밸류업 · 지배구조보고서
      정기공시(A): 사업·반기·분기보고서 「배당관련 예측가능성」 표 (div_policy.json)
      회사정보: 결산월 acc_mt (acc_mt.json)
판정  회차 기준일 = 결산기말·분기말 → 고정, 아니면 유동 (§4.2)
예측  고정 = 결산기말·분기말 / 유동 = 앵커(A2 주총일·실적발표, 없으면 A1 작년 반복) + 오프셋, 법·제도 보정 (§4.3·§4.4)
출력  data/research.json (companies + events) · --verify → data/research_verify.json (§5.2)

  python research.py [--verify]
"""
import sys, os, re, json, datetime, statistics, threading
from concurrent.futures import ThreadPoolExecutor
from fetch import listing, doc_text, api, KEY, corp_code
from base import DATA_DIR, save_json
import dividends as dv
from dividends import TYPE_QUARTERS, quarter_end, round_of
from krx_cal import t_minus
import earn_sched

D = datetime.date
OUT_PATH = os.path.join(DATA_DIR, "research.json")
VERIFY_PATH = os.path.join(DATA_DIR, "research_verify.json")
POLICY_PATH = os.path.join(DATA_DIR, "div_policy.json")
ACC_PATH = os.path.join(DATA_DIR, "acc_mt.json")
EXP_PATH = os.path.join(DATA_DIR, "expiries.json")
HIST_PATH = dv.HIST_PATH
DART = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
_LOCK = threading.RLock()

HIST_DAYS = 800
POLICY_DAYS = 400
HORIZON_DAYS = 270
MISS_GRACE_DAYS = 3
NOTICE_MIN_DAYS = 14
SPECIAL_MULT = 2
PAY_LIMIT_DAYS = 92
APPROVAL_PAY_DAYS = 31
NOTICE_LOOKBACK_DAYS = 45
SEASON_DAYS = {"결산배당": 123, "중간배당": 92, "분기배당": 92}
FLOAT_FROM = {"결산배당": 2023, "중간배당": 2025, "분기배당": 2025}
GROUP = {"결산배당": "결산", "중간배당": "분기중간", "분기배당": "분기중간"}
END_WORD = {"결산배당": "결산기말", "분기배당": "분기말", "중간배당": "반기말"}
_RED = {"buy": "background:#fca5a5;border:1px solid #fca5a5;color:#3a0a0a;font-weight:600",
        "ex": "background:#dc2626;border:1px solid #dc2626;color:#fff;font-weight:600"}
_EST = lambda css: {"buy": css + ";font-weight:600", "ex": css + ";font-weight:600"}
TAGS = {   # 구분 이름 앞 태그 — 날짜가 얼마나 확실한가
    "확정": "회사가 공시함 — 날짜(와 금액)가 정해짐",
    "사실상 확정": "공시는 없지만 정관·사례로 날짜가 정해짐 — 결산기말·분기말·반기말",
    "예상": "이사회가 따로 정하는 건 확실하고 날짜만 추정",
    "미정": "기말일지 이사회 지정일지조차 모름",
}
GRADES = {
    "확정": {"rank": 0, "tag": "확정", "label": "[확정] 날짜·금액 공시", "desc": "회사가 현금배당결정 공시를 냄 — 금액·기준일 확정",
             "rule": "이번 회차 현금배당결정 공시가 나옴", "firm": True, "style": _RED, "xl": "F8CBAD"},
    "공고": {"rank": 1, "tag": "확정", "label": "[확정] 날짜 공시 · 금액 미정", "desc": "회사가 기준일(주주명부폐쇄) 공고만 냄 — 날짜 확정, 금액은 아직",
             "rule": "이번 회차 기준일(주주명부폐쇄) 공고가 나옴", "firm": True, "style": _RED, "xl": "F4B084"},
    "미변경": {"rank": 2, "tag": "사실상 확정", "label": "[사실상 확정] 날짜 고정 · 정관 미변경({end})", "desc": "정관상 기준일이 {end}로 고정 — 선진화 정관이 아님",
               "rule": "정관상 기준일 {end} 고정, 또는 정관 정보 없이 직전 회차가 {end}", "firm": True,
               "style": _EST("background:#eab308;border:1px solid #eab308;color:#241a00"), "xl": "FFD966"},
    "사례기말": {"rank": 3, "tag": "사실상 확정", "label": "[사실상 확정] 날짜 고정 · 선진화 정관이지만 {end} 유지", "desc": "정관을 바꾼 뒤 같은 종류 배당을 {end}에 줌 — 그 회사 방식",
                 "rule": "선진화 정관 + 정관을 바꾼 뒤 같은 종류 배당 회차가 있고 그때 기준일이 {end}", "firm": True,
                 "style": _EST("background:#fde68a;border:1px solid #fde68a;color:#241a00"), "xl": "FFE699"},
    "사례지정": {"rank": 4, "tag": "예상", "label": "[예상] 이사회 지정일 · 날짜 추정", "desc": "정관을 바꾼 뒤 같은 종류 배당을 이사회가 정한 날에 줌 — 방식은 정해졌고 날짜는 추정",
                 "rule": "직전 같은 종류 회차 기준일이 {end}이 아님, 또는 이번 회차 '배당기준일 안내' 자율공시가 나옴", "firm": False,
                 "style": _EST("background:rgba(234,179,8,.18);border:1.5px solid #eab308;color:#fde047"), "xl": "FFF2CC"},
    "사례없음": {"rank": 5, "tag": "미정", "label": "[미정] 바뀔 수 있음 · 선진화 정관 첫 회차", "desc": "정관을 바꾼 뒤 같은 종류 배당을 아직 안 치렀거나, 정관 변경 안건이 주총에 올라옴",
                 "rule": "선진화 정관인데 바꾼 뒤 같은 종류 배당 회차가 없음, 또는 정관상 기준일 변경 안건이 주총에 상정", "firm": False,
                 "style": _EST("background:transparent;border:1.5px dashed #eab308;color:#fde047"), "xl": "EDEDED"},
    "근거약함": {"rank": 6, "tag": "미정", "label": "[미정] 근거 약함", "desc": "정관 정보·배당 이력이 없어 판별이 어려움",
                 "rule": "정관 정보·배당 이력 없음", "firm": False,
                 "style": _EST("background:transparent;border:1.5px dotted #a3a3a3;color:#d4d4d4"), "xl": "D9D9D9"},
}
PARSE_VERSION = {"notice": 5, "agm_call": 2, "agm_result": 2, "plan": 2}


def _d(s):
    s = (s or "").replace("-", "")
    return D(int(s[:4]), int(s[4:6]), int(s[6:8])) if len(s) >= 8 and s[:8].isdigit() else None

def _norm(s):
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", s or "")
    try:
        return D(*map(int, m.groups())) if m else None
    except ValueError:
        return None

def _iso(d):
    return d.isoformat() if d else ""

def _load(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


# ── 수집: 목록 ──
def _kind(nm):
    c = re.sub(r"\s+", "", nm or "")
    if "자회사" in c:
        return None
    if "현금ㆍ현물배당결정" in c:
        return "decision"
    if "주주명부폐쇄" in c:
        return "record" if "배당" in c else "agm_record"
    if "기타경영사항" in c and ("배당" in c or "기준일" in c or c.endswith("기타경영사항(자율공시)")):
        return "notice"
    if "주식분할결정" in c or "주식병합결정" in c:
        return "split"
    if "주주총회소집결의" in c:
        return "agm_call"
    if "주주총회결과" in c:
        return "agm_result"
    if "기업가치제고계획" in c or "지배구조보고서" in c:
        return "plan"
    return None

def _periodic(nm):
    return any(k in (nm or "") for k in ("사업보고서", "반기보고서", "분기보고서"))

# ── 수집: 원문 파싱 ──
def parse_charter(t):
    """주총 「정관상 기준일 설정 및 변경 세부내역」 — 이익배당 기준일 칸 '-' = 정관 고정일 없음(이사회 지정) → O"""
    i = t.find("정관상 기준일 설정 및 변경")
    if i < 0:
        return None
    s = t[i:i + 800]
    m = re.search(r"결산배당\s*(\S+)\s*중간배당\s*(\S+)\s*분기배당\s*1분기\s*(\S+)\s*2분기\s*(\S+)\s*3분기\s*(\S+)", s)
    if m:
        return {"결산": "O" if m[1] == "-" else "X", "분기중간": "O" if all(x == "-" for x in m.groups()[1:]) else "X"}
    m = re.search(r"이익배당\s*\(\s*결산배당\s*\)\s*기준일\s*(\S+)\s*(\S+)", s)
    return {"결산": "O" if m[2] == "-" else "X"} if m else None

def parse_notice(t):
    i = t.find("기준일")
    months = [f"{m[1]}-{int(m[2]):02d}" for m in re.finditer(r"(\d{4})\s*년\s*(\d{1,2})\s*월", t[i:i + 800])] if i >= 0 else []
    fyq = re.search(r"(\d{4})\s*년\s*([1-3])\s*분기\s*배당", t)   # "2026년 2분기 배당여부 및 배당기준일을…" → 그 분기
    typ = ("분기배당" if fyq or re.search(r"분기\s*배당\s*기준일", t) else
           "중간배당" if re.search(r"중간\s*배당\s*기준일", t) else "결산배당")
    subsidiary = bool(re.search(r"종속회사인?\s*(?:\(주\)|㈜)?\s*\S{1,30}?(?:은|는)\s[^.]{0,120}배당(?:안|\s*계획|금|을)", t))   # 자회사가 주어인 배당 계획 공시
    return {"is_div": bool(re.search(r"배당\s*기준일", t)) and not subsidiary, "div_type": typ, "months": months,
            "fyq": [int(fyq[1]), int(fyq[2])] if fyq else None}

def parse_agm(t):
    dt = re.search(r"주주총회일자\s*(\d{4}-\d{2}-\d{2})", t) or re.search(r"일시\s*(\d{4}-\d{2}-\d{2})", t)
    return {"agm_date": dt[1] if dt else "", "charter": parse_charter(t),
            "regular": bool(re.search(r"구분\s*정기주주총회|정기주주총회\s*결과", t)),
            "rejected": bool(re.search(r"특별결의[^.]{0,100}?부결", t))}

def parse_split(t):
    """주식분할·병합결정 — 1주당 가액 전→후 비율 = 주식수 배수(분할 5,000→500 은 10, 병합 500→2,500 은 0.2), 신주 효력발생일"""
    m = re.search(r"1주당\s*가액\s*\(원\)\s*([\d,]+)\s+([\d,]+)", t)
    eff = re.search(r"효력발생일\s*(\d{4}-\d{2}-\d{2})", t)
    if not m or not eff:
        return {}
    before, after = (int(x.replace(",", "")) for x in m.groups())
    return {"factor": before / after, "effective": eff.group(1)} if before and after else {}

def parse_plan(t):
    lines = []
    for m in re.finditer(r"배당\s*기준일|배당\s*절차|선\s*배당|배당액을?\s*먼저", t):
        s = t[max(0, m.start() - 80): m.end() + 120].strip()
        if not any(s[20:60] in x for x in lines):
            lines.append(s)
        if len(lines) >= 3:
            break
    return {"lines": lines}

PARSE = {
    "decision": lambda t: {"per_share": "", "pay_date": "", **dv.parse_decision(t)},
    "record": dv.parse_record,
    "notice": parse_notice,
    "agm_call": parse_agm,
    "agm_result": parse_agm,
    "plan": parse_plan,
    "split": parse_split,
}

YM = r"(\d{4}\s*(?:\.|년)\s*\d{1,2}\s*월?|-)"
DT = r"(\d{4}\s*(?:\.|년)\s*\d{1,2}\s*(?:\.|월)\s*\d{1,2}\s*일?|-)"
ROW = re.compile(r"(결산배당|분기배당|중간배당)\s+" + YM + r"\s+([OX])\s+" + DT + r"\s+" + DT + r"\s+([OX])")
TABLE = re.compile(r"정관상\s*배당액\s*결정\s*기관\s*(\S+)\s+(\S+)\s+정관상\s*배당기준일을\s*배당액\s*결정\s*이후로\s*정할\s*수\s*있는지\s*여부\s*([OX-])\s*([OX-])")

def parse_policy(t):
    """정기보고서 「배당관련 예측가능성 제공에 관한 사항」 표1(정관·결정기관)·표2(회차) — 표가 없으면 정관 전문(결산만)"""
    m = TABLE.search(t)
    if m:
        rows = [[x[1], _iso(_norm(x[4])), _iso(_norm(x[5]))] for x in ROW.finditer(t[m.end(): m.end() + 5000]) if x[3] == "O"]
        return {"organ": {"결산": m[1], "분기중간": m[2]}, "charter": {"결산": m[3], "분기중간": m[4]}, "rows": rows}
    if re.search(r"이사회\s*결의로\s*[^.]{0,30}?배당을\s*받을\s*주주를\s*확정하기\s*위한\s*기준일", t):
        return {"charter_text": "O"}
    if re.search(r"(?:배당|이익)[^.]{0,60}매\s*결산기\s*말?\s*현재의?\s*주주명부", t):
        return {"charter_text": "X"}
    return {}

def _download(jobs, cache, label):
    """(rcept_no, parse, base) 작업을 병렬로 받아 cache 에 채움. 받기 실패는 캐시에 남기지 않음(다음 회차 재시도)."""
    if not jobs:
        return
    print(f"  [{label}] 원문 {len(jobs)}건 받는 중")
    def one(job):
        rno, parse, base = job
        try:
            return rno, {**base, **parse(doc_text(rno))}
        except Exception:
            return rno, None
    with ThreadPoolExecutor(2) as ex:
        for i, (rno, v) in enumerate(ex.map(one, jobs)):
            if v is not None:
                cache[rno] = v
            if (i + 1) % 100 == 0:
                print(f"    ...{i + 1}/{len(jobs)}")

def collect(universe):
    """거래소공시 이력(2년+) 원문 파싱 → div_history.json, 주총용 명부폐쇄는 목록만"""
    hist = dv.load_doc_cache()
    rows = [r for r in listing("I", HIST_DAYS) if r["stock_code"] in universe and _kind(r["report_nm"])]
    jobs, agm_records = [], {}
    for r in rows:
        k = _kind(r["report_nm"])
        if k == "agm_record":
            agm_records.setdefault(r["stock_code"], []).append(_d(r["rcept_dt"]))
            continue
        c = hist.get(r["rcept_no"])
        if c is not None and c.get("v", 1) >= PARSE_VERSION.get(k, 1):
            continue
        base = {"kind": k, "stock": r["stock_code"], "corp": r["corp_name"],
                "market": "KOSPI" if r["corp_cls"] == "Y" else "KOSDAQ", "rcept_dt": r["rcept_dt"],
                "title": re.sub(r"\s+", " ", r["report_nm"]).strip(), "v": PARSE_VERSION.get(k, 1)}
        jobs.append((r["rcept_no"], PARSE[k], base))
    _download(jobs, hist, "이력")
    with _LOCK:
        save_json(HIST_PATH, hist)
    return hist, agm_records

def policies(universe):
    """종목별 정기보고서 배당 표 — 최신 표(없으면 직전 보고서) + 기간 안 보고서들의 정관 이력"""
    cache = _load(POLICY_PATH, {})
    by = {}
    for r in listing("A", POLICY_DAYS):
        if r["stock_code"] in universe and _periodic(r["report_nm"]):
            by.setdefault(r["stock_code"], []).append(r)
    for rs in by.values():
        rs.sort(key=lambda r: r["rcept_no"], reverse=True)
    def tables(stock):
        got = []
        for r in by[stock]:
            if r["rcept_no"] not in cache:
                try:
                    cache[r["rcept_no"]] = {**parse_policy(doc_text(r["rcept_no"])), "rcept_dt": r["rcept_dt"],
                                            "report": re.sub(r"\s+", " ", r["report_nm"]).strip()}
                except Exception:
                    continue
            got.append(cache[r["rcept_no"]])
        latest = next((p for p in got if p.get("charter")), None) or next((p for p in got if p.get("charter_text")), None)
        hist = [{"date": p["rcept_dt"], "charter": p.get("charter") or {"결산": p["charter_text"]}}
                for p in got if p.get("charter") or p.get("charter_text")]
        return stock, latest and {**latest, "charter_hist": hist}
    todo = sum(1 for s in by for r in by[s] if r["rcept_no"] not in cache)
    if todo:
        print(f"  [정기보고서] 원문 최대 {todo}건 받는 중")
    with ThreadPoolExecutor(2) as ex:
        out = dict(ex.map(tables, list(by)))
    with _LOCK:
        save_json(POLICY_PATH, cache)
    return {s: p for s, p in out.items() if p}

def acc_months(universe):
    """종목별 결산월(company.json acc_mt). 답을 받은 종목은 다시 묻지 않는다 — '결산월 없음'("") 답도 기록,
    받기 실패만 기록하지 않고 다음 회차에 다시."""
    cache = _load(ACC_PATH, {})
    def one(s):
        try:
            cc = corp_code(s)
            if not cc:
                return s, ""        # DART 고유번호 없음 = 물어볼 결산월 없음
            j = api({"crtfc_key": KEY, "corp_code": cc}, "company")
        except Exception:
            return s, None
        return s, {"000": j.get("acc_mt", ""), "013": ""}.get(j.get("status"))
    missing = sorted(set(universe) - set(cache))
    if missing:
        print(f"  [결산월] {len(missing)}종목 조회")
        first = one(missing[0])
        if first[1] is not None:
            with ThreadPoolExecutor(2) as ex:
                cache.update({s: m for s, m in [first, *ex.map(one, missing[1:])] if m is not None})
            save_json(ACC_PATH, cache)
        else:
            print("  [!] 결산월 조회 실패 — 이번 계산은 12월 결산 가정(종목 근거에 표시)")
    return {s: int(m) for s, m in cache.items() if str(m).isdigit()}


# ── 회사 단위 정리 ──
def share_factor(splits, a, b):
    """a 날짜 주식 1주가 b 날짜에 몇 주인지 — 그 사이 효력이 난 분할·병합 배수의 곱"""
    f = 1.0
    for x in splits:
        if a < x["effective"] <= b:
            f *= x["factor"]
    return f

def _agm_fy(agm, acc):
    return max(fy for fy in range(agm.year - 2, agm.year + 1) if quarter_end(fy, 4, acc) < agm)

def context(stock, entries, policy, acc, agm_records):
    """한 종목의 회차·정관·결정기관·예고·주총·계획을 판정에 쓰는 모양으로"""
    acc_known = acc is not None
    acc = acc or 12
    corp = market = ""
    rec_notices, notices, agms, plans, splits = [], [], [], [], []
    for e in sorted(entries, key=lambda x: x["rcept_dt"]):
        corp, market = e.get("corp") or corp, e.get("market") or market
        k, rd = e["kind"], _d(e["rcept_dt"])
        if k == "split" and e.get("effective"):
            sp = {"date": rd, "effective": _d(e["effective"]), "factor": e["factor"]}
            splits = [x for x in splits if not (x["factor"] == sp["factor"] and abs((x["effective"] - sp["effective"]).days) <= 120)] + [sp]
        elif k == "record" and e.get("record_date"):
            rec_notices.append({"date": rd, "record": _d(e["record_date"]), "div_type": e.get("div_type", "")})
        elif k == "notice" and e.get("is_div"):
            t = e["div_type"]
            fy, q = e.get("fyq") or min(((fy, q, quarter_end(fy, q, acc)) for fy in range(rd.year - 1, rd.year + 2) for q in TYPE_QUARTERS[t]
                                          if quarter_end(fy, q, acc) >= rd - datetime.timedelta(days=NOTICE_LOOKBACK_DAYS)), key=lambda c: c[2])[:2]
            hint = next((m for m in e.get("months", []) if m > rd.strftime("%Y-%m")), "")
            notices.append({"date": rd, "div_type": t, "fy": fy, "q": q, "hint": hint, "title": e.get("title", ""), "rcept_no": e["rcept_no"]})
        elif k in ("agm_call", "agm_result") and e.get("agm_date"):
            agms.append({"date": _d(e["agm_date"]), "kind": k, "rcept": rd, "charter": e.get("charter"),
                         "regular": e.get("regular", False), "rejected": e.get("rejected", False)})
        elif k == "plan" and e.get("lines"):
            plans.append({"date": rd, "title": e.get("title", ""), "lines": e["lines"]})

    rounds = []
    for r in dv.assemble([{**e, "div_type": e.get("div_type") or ""} for e in entries if e["kind"] in ("decision", "record")], {stock: acc}):
        if not r["decided"] or r["div_type"] not in TYPE_QUARTERS:
            continue
        t, record, decided = r["div_type"], _d(r["record_date"]), _d(r["decided"])
        fy, q, qe = round_of(t, record, decided, acc)
        rounds.append({"type": t, "fy": fy, "q": q, "qe": qe, "record": record, "decided": decided,
                       "dps": r["per_share"], "pay": _norm(r["pay_date"]), "src": "공시", "rcept_no": r["rcept_no"]})
    seen = {(r["type"], r["record"]) for r in rounds}
    for t, decided, record in (policy or {}).get("rows", []):
        record, decided = _d(record), _d(decided)
        if decided and record and decided < record - datetime.timedelta(days=180):
            decided = None   # 회사 표 오기(확정일이 기준일보다 반년 넘게 앞섬 — 예: 2025-02-06 → 2026-02-28)
        if record and (t, record) not in seen:
            fy, q, qe = round_of(t, record, decided, acc)
            rounds.append({"type": t, "fy": fy, "q": q, "qe": qe, "record": record, "decided": decided,
                           "dps": "", "pay": None, "src": "정기보고서 표", "rcept_no": ""})
            seen.add((t, record))
    rounds.sort(key=lambda r: r["record"])
    for r in rounds:
        r["fixed"] = r["record"] == r["qe"]
        r["amount_first"] = bool(r["decided"] and r["decided"] < r["record"])
        prior = sorted(int(x["dps"]) / share_factor(splits, x["record"], r["record"]) for x in rounds
                       if x["type"] == r["type"] and x["record"] < r["record"] and x["dps"])
        med = prior[len(prior) // 2] if prior else 0
        r["special"] = bool(med and r["dps"] and int(r["dps"]) > SPECIAL_MULT * med)
    for a in agms:
        a["fy"] = _agm_fy(a["date"], acc)

    charter_cands = {"결산": [], "분기중간": []}
    seen_v = {"결산": [], "분기중간": []}
    for h in (policy or {}).get("charter_hist", []):
        for g, v in h["charter"].items():
            if v in ("O", "X"):
                seen_v[g].append((_d(h["date"]), v))
    if policy and policy.get("charter"):
        for g, v in policy["charter"].items():
            if v in ("O", "X"):
                charter_cands[g].append({"v": v, "date": _d(policy["rcept_dt"]), "src": f"정기보고서 표 · {policy['report']}"})
    elif policy and policy.get("charter_text"):
        charter_cands["결산"].append({"v": policy["charter_text"], "date": _d(policy["rcept_dt"]),
                                     "src": f"정관 전문 · {policy['report']}"})
    pending = {}
    for a in agms:
        block = a["charter"] or next((c["charter"] for c in agms if c["kind"] == "agm_call" and c["date"] == a["date"] and c["charter"]), None)
        if not block:
            continue
        if a["kind"] == "agm_result" and not a["rejected"]:
            for g, v in block.items():
                charter_cands[g].append({"v": v, "date": a["date"], "src": f"주총결과 정관상 기준일 표 · {a['date']}"})
                seen_v[g].append((a["date"], v))
        elif a["kind"] == "agm_call" and not any(c["kind"] == "agm_result" and c["date"] == a["date"] for c in agms):
            pending = {"date": _iso(a["date"]), "block": block}
    charter = {g: max(c, key=lambda x: x["date"]) for g, c in charter_cands.items() if c}
    since = {}
    agm_days = sorted(a["date"] for a in agms if a["kind"] == "agm_result" and not a["rejected"])
    for g, vs in seen_v.items():
        last_x = max((d for d, v in vs if v == "X"), default=None)
        o = min((d for d, v in vs if v == "O" and (last_x is None or d > last_x)), default=None)
        if o:
            since[g] = max((d for d in agm_days if d <= o and (last_x is None or d > last_x)), default=o)
    return {"stock": stock, "corp": corp, "market": market, "acc": acc, "acc_known": acc_known,
            "rounds": rounds, "rec_notices": rec_notices, "notices": notices, "agms": agms, "plans": plans,
            "agm_records": sorted(agm_records or []), "charter": charter, "charter_since": since, "charter_pending": pending, "splits": splits,
            "organ": (policy or {}).get("organ") or {}, "policy_report": (policy or {}).get("report", "")}

def view(ctx, cut):
    """cut 이전에 공시된 것만 남긴 ctx — 백테스트(§5.2)·확정 회차 사다리용"""
    v = dict(ctx)
    v["rounds"] = [r for r in ctx["rounds"] if (r["decided"] or r["record"]) < cut]
    v["rec_notices"] = [n for n in ctx["rec_notices"] if n["date"] < cut]
    v["notices"] = [n for n in ctx["notices"] if n["date"] < cut]
    v["agms"] = [a for a in ctx["agms"] if a["rcept"] < cut]
    v["agm_records"] = [d for d in ctx["agm_records"] if d < cut]
    v["charter_since"] = {g: d for g, d in ctx["charter_since"].items() if d < cut}
    v["splits"] = [x for x in ctx["splits"] if x["date"] < cut]
    return v

def quarter_owner(rounds):
    """분기 → 그 분기를 맡는 배당 종류 (분기·중간이 겹치면 최근 회차가 있는 쪽)"""
    by_type = {}
    for r in rounds:
        by_type.setdefault(r["type"], []).append(r)
    owner = {}
    for t, rs in by_type.items():
        qs = {r["q"] for r in rs}
        if t == "분기배당" and len(qs) >= 2:
            qs |= {1, 2, 3}
        for q in qs:
            if q not in owner or rs[-1]["record"] > by_type[owner[q]][-1]["record"]:
                owner[q] = t
    return by_type, owner

def pattern(owner):
    by_t = {}
    for q in sorted(owner):
        by_t.setdefault(owner[q], []).append(f"Q{q}")
    return " + ".join(f"{t} {'·'.join(qs)}" for t, qs in by_t.items()) + f" (연 {len(owner)}회)"


# ── 판정 · 예측 ──
def _notice(ctx, t, fy, q):
    """이번 회차의 기준일 안내 자율공시 — 'N분기 배당' 안내는 그 분기의 분기·중간배당 회차 모두에 해당"""
    kinds = (t,) if t == "결산배당" else ("분기배당", "중간배당")
    return next((n for n in reversed(ctx["notices"]) if n["div_type"] in kinds and (n["fy"], n["q"]) == (fy, q)), None)

def judge(ctx, t, fy, q):
    """§4.2 — {method 날짜 방식, why 근거, grade GRADES 키, case 사례 근거}. 사례 = 선진화 정관이 된 뒤 같은 종류 배당 회차"""
    g, end = GROUP[t], END_WORD[t]
    out = lambda method, why, grade, case="": {"method": method, "why": why, "grade": grade, "case": case}
    on_round = lambda r, how: f"{r['fy']} Q{r['q']} 기준일 {r['record']}({how})" + (f" {DART}{r['rcept_no']}" if r["rcept_no"] else f" · {r['src']}")
    notice = _notice(ctx, t, fy, q)
    if notice:
        return out("유동", f"이번 회차 '배당기준일 안내' 자율공시({notice['date']}) — 이사회가 기준일을 따로 정함", "사례지정",
                   f"이번 회차 기준일 안내 자율공시 {notice['date']} {DART}{notice['rcept_no']}")
    past = [r for r in ctx["rounds"] if r["type"] == t]
    ch = (ctx["charter"].get(g) or {}).get("v")
    since = ctx["charter_since"].get(g)
    pend = (ctx["charter_pending"].get("block") or {}).get(g) == "O"
    if past and not past[-1]["fixed"]:
        last = past[-1]
        return out("유동", f"직전 회차({last['fy']} Q{last['q']}) 기준일 {last['record']}이 {end}({last['qe']})이 아님 — 이사회가 따로 정함", "사례지정",
                   on_round(last, "이사회 지정"))
    if ch == "O":
        case = next((r for r in reversed(past) if since and r["record"] > since), None)
        if case:
            return out("고정", f"선진화 정관 확인({since}) 뒤 {case['fy']} Q{case['q']} 기준일 {case['record']}도 {end} — 그 회사 방식", "사례기말",
                       on_round(case, end))
        gone = f"없음 · 선진화 정관 {since}부터 ({ctx['charter'][g]['src']})"
        if past:
            return out("고정", f"직전 회차 기준일 {past[-1]['record']} = {end}. 선진화 정관 확인({since}) 뒤 이 배당은 아직 안 치러 바뀔 수 있음", "사례없음", gone)
        return out("유동 가능", f"배당 이력 없음 · 선진화 정관 ({ctx['charter'][g]['src']})", "사례없음", gone)
    if pend:
        return out("고정", f"{ctx['charter_pending']['date']} 주총에 정관상 기준일 변경 안건 상정 — 바뀔 수 있음", "사례없음",
                   f"없음 · {ctx['charter_pending']['date']} 주총 정관 변경 안건")
    if past:
        return out("고정", f"직전 회차 기준일 {past[-1]['record']} = {end}" + (" · 정관상 기준일 고정" if ch == "X" else " · 정관 정보 없음"), "미변경")
    if ch == "X":
        return out("고정", f"배당 이력 없음 · 정관상 기준일이 {end}로 고정 ({ctx['charter'][g]['src']})", "미변경")
    return out("판별 불가", "정관 정보·배당 이력 없음", "근거약함")

def grade_out(key, t):
    return {"grade": key, "grade_label": GRADES[key]["label"].format(end=END_WORD[t])}

def cond_signals(ctx, qe, today):
    """D13·D14 — 결산기말 직전 한 달: 주총용 명부폐쇄만 / 배당 기준일 공고 없음. 단독으론 틀린 신호(§2.6), 표시·적중률용"""
    lo = qe - datetime.timedelta(days=31)
    div_rec = any(lo < n["date"] <= qe for n in ctx["rec_notices"])
    agm_rec = any(lo < d <= qe for d in ctx["agm_records"])
    done = today > qe
    return {"D13": (agm_rec and not div_rec) if done else None, "D14": (not div_rec) if done else None}

def announce_date(earn, qe):
    pre = [_d(e["expected_date"]) for e in earn if e.get("period_end") == qe.isoformat()]
    if pre:
        return min(pre)
    ir = [_d(e["expected_date"]) for e in earn if e.get("src") == "IR" and qe < _d(e["expected_date"]) <= qe + datetime.timedelta(days=100)]
    return min(ir) if ir else None

def ladder(ctx, t, fy, q, confirmed):
    g = GROUP[t]
    ch, organ, pend = ctx["charter"].get(g), ctx["organ"].get(g, ""), ctx["charter_pending"]
    past = [r for r in ctx["rounds"] if r["type"] == t and (r["fy"], r["q"]) != (fy, q)]
    last = past[-1] if past else None
    notice = _notice(ctx, t, fy, q)
    return [
        {"n": "①", "name": "정관", "state": ("on" if ch["v"] == "O" else "off") if ch else "none",
         "text": ((f"이사회가 기준일을 따로 정할 수 있음 — {ch['src']}" if ch["v"] == "O"
                   else f"기준일이 {END_WORD[t]}로 고정 — {ch['src']}") if ch else "정관 정보 없음")
                 + (f" · 주총({pend['date']}) 정관 기준일 변경 안건 상정" if pend else "")},
        {"n": "②", "name": "결정기관", "state": "on" if organ else "none",
         "text": f"배당액 결정기관 {organ or '미상'}" + (f" · {ctx['policy_report']}" if ctx["policy_report"] else "")},
        {"n": "③", "name": "기준일 예고", "state": "on" if notice else "off",
         "text": (f"기준일 안내 자율공시 {notice['date']}" + (f" · {notice['hint']} 이후" if notice["hint"] else ""))
                 if notice else "이번 회차 기준일 안내 자율공시 없음"},
        {"n": "④", "name": "기준일 공고", "state": "on" if confirmed else "off",
         "text": "기준일 공고 — 확정" if confirmed else "기준일 공고 아직 (공고 뜨면 확정)"},
        {"n": "⑤", "name": "직전 회차", "state": ("off" if last["fixed"] else "on") if last else "none",
         "text": (f"{last['fy']} Q{last['q']} 기준일 {last['record']} — {'결산기말·분기말' if last['fixed'] else '이사회가 따로 정함'}"
                  if last else "직전 회차 없음")},
    ]

def medians(ctxs):
    """이력 없는 회사용 기본 오프셋 — 수집대상 전체 실측 중앙값"""
    acc = {"qe": {}, "dec": {}, "pay": {}, "agm": []}
    for c in ctxs:
        agm_by_fy = {a["fy"]: a["date"] for a in c["agms"] if a["regular"]}
        for r in c["rounds"]:
            t = r["type"]
            if r["pay"]:
                acc["pay"].setdefault(t, []).append((r["pay"] - r["record"]).days)
            if r["fixed"]:
                continue
            acc["qe"].setdefault(t, []).append((r["record"] - r["qe"]).days)
            if r["decided"] and r["decided"] < r["record"]:
                acc["dec"].setdefault(t, []).append((r["record"] - r["decided"]).days)
            if t == "결산배당" and r["fy"] in agm_by_fy:
                acc["agm"].append((r["record"] - agm_by_fy[r["fy"]]).days)
    md = lambda xs, dflt: int(statistics.median(xs)) if xs else dflt
    return {"qe": {t: md(acc["qe"].get(t), 60 if t == "결산배당" else 45) for t in TYPE_QUARTERS},
            "dec": {t: md(acc["dec"].get(t), 21) for t in TYPE_QUARTERS},
            "pay": {t: md(acc["pay"].get(t), 30) for t in TYPE_QUARTERS},
            "agm": md(acc["agm"], 7)}

def predict_round(ctx, t, fy, q, today, earn, med):
    """한 회차의 예상 기준일·지급일 (§4.3~§4.5.1) → (예측, None) 또는 미실시면 (None, 사유)"""
    qe = quarter_end(fy, q, ctx["acc"])
    past = [r for r in ctx["rounds"] if r["type"] == t]
    prev = past[-1] if past else None
    same_q = next((r for r in reversed(past) if r["q"] == q), None)
    j = judge(ctx, t, fy, q)
    method, why, grade = j["method"], j["why"], j["grade"]
    organ = ctx["organ"].get(GROUP[t], "")
    regular = [a for a in ctx["agms"] if a["regular"]] if t == "결산배당" else []
    agm = next((a for a in reversed(regular) if a["fy"] == fy), None)
    prev_agm = next((a for a in reversed(regular) if prev and a["fy"] == prev["fy"]), None)
    ann_rec = next((n for n in reversed(ctx["rec_notices"]) if n["div_type"] in ("", t) and qe <= n["record"] <= qe + datetime.timedelta(days=SEASON_DAYS[t])), None)
    notice = _notice(ctx, t, fy, q)
    warn, alt, anchor_event = [], {}, None
    md = lambda d: f"{d.month}/{d.day}"
    last_word = "작년" if t == "결산배당" else "직전 회차"
    end_word = END_WORD[t]

    if not ann_rec and today > qe + datetime.timedelta(days=SEASON_DAYS[t]):
        return None, f"⚠ {fy} Q{q} {t}: 시즌 창(결산기말·분기말 {qe} +{SEASON_DAYS[t]}일) 지나도 배당 공시 없음 → 미실시로 보고 예상 제외"
    if ann_rec:
        rec, anchor = ann_rec["record"], f"기준일 공고 {ann_rec['date']} (금액은 아직 미공시)"
        reason = f"회사가 기준일 공고({md(ann_rec['date'])}) · 금액은 아직"
    elif method == "고정":
        rec, anchor = qe, f"{end_word} 고정"
        reason = (f"{last_word}도 {md(prev['record'])}" if prev else "배당 이력 없음") + {
            "미변경": f" · 정관상 기준일이 {end_word}로 고정",
            "사례기말": f" · 정관을 바꾼 뒤에도 {end_word}",
            "사례없음": " · 정관을 바꾼 뒤 처음(또는 변경 안건 상정)이라 바뀔 수 있음"}[grade]
    else:
        if same_q and not same_q["fixed"]:
            alt["A1"] = same_q["record"] + datetime.timedelta(weeks=52 * (fy - same_q["fy"]))
        if t == "결산배당" and organ == "주주총회" and agm:
            off = (prev["record"] - prev_agm["date"]).days if prev and prev_agm and not prev["fixed"] else med["agm"]
            alt["A2"], anchor_event = agm["date"] + datetime.timedelta(days=off), agm["date"]
            a2_txt = f"올해 정기주총({agm['date']}) + 작년 주총→기준일 간격 {off}일"
            a2_why = f"올해 정기주총 {md(agm['date'])} + 작년처럼 주총 {off}일 뒤"
        else:
            ann = announce_date(earn, qe)
            if ann:
                off = ((prev["record"] - prev["decided"]).days if prev and not prev["fixed"] and prev["amount_first"]
                       else med["dec"][t])
                alt["A2"], anchor_event = ann + datetime.timedelta(days=off), ann
                a2_txt = f"올해 실적발표 예정일({ann}) + 작년 배당결정→기준일 간격 {off}일"
                a2_why = f"올해 실적발표 예정 {md(ann)} + 작년처럼 배당결정 {off}일 뒤"
        if "A2" in alt:
            rec, anchor, reason = alt["A2"], a2_txt, a2_why
        elif "A1" in alt:
            rec, anchor = alt["A1"], f"작년 같은 회차 기준일({same_q['record']}) + 52주"
            reason = f"작년 기준일 {md(same_q['record'])}(이사회가 따로 정한 날) → 올해도 같은 때(52주 뒤)"
        else:
            rec, anchor = qe + datetime.timedelta(days=med["qe"][t]), f"이 회사 이력 없음 — 결산기말·분기말 + 수집대상 전체 중앙값 {med['qe'][t]}일"
            reason = (f"작년 같은 {'회차' if t == '결산배당' else '분기'}에 이사회가 정한 기준일 없음" if past else "이 회사 배당 이력 없음") +                      f" → 다른 회사들 중간값({end_word} + {med['qe'][t]}일)"
        if notice:
            reason = f"이번 회차 기준일 안내 자율공시({md(notice['date'])}) → 이사회가 따로 정함 · " + reason
        if notice and notice["hint"]:
            floor = D(int(notice["hint"][:4]), int(notice["hint"][5:]), 1)
            if rec < floor:
                rec, anchor = floor, anchor + f" · 자율공시 '{notice['hint']}' 이후로 보정"
                reason += f" · 자율공시상 {notice['hint']} 이후"
        floor = today + datetime.timedelta(days=NOTICE_MIN_DAYS)
        if rec < floor:
            rec, anchor = floor, anchor + f" · 기준일 공고 전이라 {NOTICE_MIN_DAYS}일 뒤({floor}) 이후로 보정"
            reason += f" · 아직 공고 전이라 2주 뒤({md(floor)}) 이후"
        if anchor_event and anchor_event < today - datetime.timedelta(days=MISS_GRACE_DAYS):
            return None, f"⚠ {fy} Q{q} {t}: 근거 날짜(주총·실적발표 {anchor_event})가 지나도 배당 공시 없음 → 미실시로 보고 예상 제외"

    if not (qe <= rec <= qe + datetime.timedelta(days=SEASON_DAYS[t])):
        warn.append(f"시즌 창 밖: 기준일 {rec} (결산기말·분기말 {qe} ~ +{SEASON_DAYS[t]}일)")
    if method == "유동" and fy < FLOAT_FROM[t]:
        warn.append(f"제도 시행 전 사업연도({fy}) 유동 판정")

    if organ == "주주총회" and agm and prev and prev["pay"] and prev_agm:
        pay = agm["date"] + datetime.timedelta(days=(prev["pay"] - prev_agm["date"]).days)
    else:
        pay = rec + datetime.timedelta(days=(prev["pay"] - prev["record"]).days if prev and prev["pay"] else med["pay"][t])
    approval = agm["date"] if agm and organ == "주주총회" else anchor_event
    if approval and pay > approval + datetime.timedelta(days=APPROVAL_PAY_DAYS):
        warn.append(f"지급일 {pay} — 승인({approval}) 후 1개월 초과 (상법 §464의2)")
    if (pay - rec).days > PAY_LIMIT_DAYS:
        warn.append(f"기준일→지급 {(pay - rec).days}일 — 3개월 한도(상법 §354③) 초과")

    reg = next((r for r in reversed(past) if not r["special"] and r["dps"]), None)
    adj = share_factor(ctx["splits"], reg["record"], rec) if reg else 1.0
    return {"div_type": t, "fy": fy, "q": q, "quarter": f"{fy} Q{q}", "quarter_end": _iso(qe),
            "record_date": _iso(rec), "pay_date": _iso(pay), "method": "확정 기준일" if ann_rec else method,
            **grade_out("공고" if ann_rec else grade, t), "case": j["case"], "why": why, "anchor": anchor, "reason": reason,
            "alt": {k: _iso(v) for k, v in alt.items()}, "warn": warn,
            "cond": cond_signals(ctx, qe, today) if t == "결산배당" else {},
            "per_share": str(round(int(reg["dps"]) / adj)) if reg else "",
            "amount_basis": (f"직전 정기 회차({reg['fy']} Q{reg['q']}) {int(reg['dps']):,}원 반복"
                             + (f" · 그 뒤 {'분할' if adj > 1 else '병합'} 1주→{adj:g}주 보정" if adj != 1 else "")) if reg else "금액 이력 없음",
            "ladder": ladder(ctx, t, fy, q, bool(ann_rec))}, None

def forecast(ctx, today, earn, med):
    by_type, owner = quarter_owner(ctx["rounds"])
    observed = {(r["type"], r["fy"], r["q"]) for r in ctx["rounds"]}
    out, missed = [], []
    for q, t in owner.items():
        last_qe = max(r["qe"] for r in by_type[t])
        for fy in (today.year - 1, today.year, today.year + 1):
            qe = quarter_end(fy, q, ctx["acc"])
            if qe <= last_qe or (t, fy, q) in observed:
                continue
            p, miss = predict_round(ctx, t, fy, q, today, earn, med)
            if miss:
                missed.append(miss)
            elif today < _d(p["record_date"]) <= today + datetime.timedelta(days=HORIZON_DAYS):
                out.append(p)
    return out, missed

def company_out(ctx, missed):
    by_type, owner = quarter_owner(ctx["rounds"])
    ev = list(missed)
    for r in ctx["rounds"]:
        if not r["fixed"] and r["fy"] < FLOAT_FROM[r["type"]]:
            ev.append(f"⚠ 이상치: {r['fy']} Q{r['q']} {r['type']} 기준일 {r['record']} — 제도 시행 전인데 결산기말·분기말 아님")
    if not ctx["acc_known"]:
        ev.append("⚠ 결산월 조회 실패 — 12월 결산으로 가정")
    judgments = {}
    for t, rs in by_type.items():
        qs = sorted(q for q, ot in owner.items() if ot == t) or [rs[-1]["q"]]
        nq = next((x for x in qs if x > rs[-1]["q"]), None)
        fy, q = (rs[-1]["fy"], nq) if nq else (rs[-1]["fy"] + 1, qs[0])
        j = judge(ctx, t, fy, q)
        judgments[t] = {"method": j["method"], "why": j["why"], **grade_out(j["grade"], t), "case": j["case"], "quarter": f"{fy} Q{q}"}
    ladders = {f"{r['type']}|{_iso(r['record'])}": ladder(view(ctx, r["decided"] or r["record"]), r["type"], r["fy"], r["q"], True)
               for r in ctx["rounds"][-12:]}
    return {"corp": ctx["corp"], "market": ctx["market"], "acc_mt": ctx["acc"], "acc_known": ctx["acc_known"],
            "charter": {g: {"v": c["v"], "src": c["src"], "date": _iso(c["date"])} for g, c in ctx["charter"].items()},
            "charter_pending": ctx["charter_pending"], "organ": ctx["organ"], "policy_report": ctx["policy_report"],
            "pattern": pattern(owner) if owner else "", "judgments": judgments, "ladders": ladders,
            "rounds": [{"quarter": f"{r['fy']} Q{r['q']}", "div_type": r["type"], "decided": _iso(r["decided"]),
                        "record_date": _iso(r["record"]), "quarter_end": _iso(r["qe"]), "fixed": r["fixed"],
                        "amount_first": r["amount_first"], "per_share": r["dps"], "special": r["special"], "src": r["src"]}
                       for r in reversed(ctx["rounds"])],
            "notices": [{"date": _iso(n["date"]), "div_type": n["div_type"], "quarter": f"{n['fy']} Q{n['q']}",
                         "hint": n["hint"], "title": n["title"]} for n in ctx["notices"]][-4:],
            "agms": [{"date": _iso(a["date"]), "kind": a["kind"], "regular": a["regular"], "charter": a["charter"]}
                     for a in ctx["agms"]][-4:],
            "plans": [{"date": _iso(p["date"]), "title": p["title"], "lines": p["lines"]} for p in ctx["plans"]][-2:],
            "evidence": ev}

def contexts():
    universe = dv.div_universe()
    hist, agm_records = collect(universe)
    pol = policies(universe)
    acc = acc_months(universe)
    by = {}
    for no, e in hist.items():
        if e.get("stock") in universe:
            by.setdefault(e["stock"], []).append({**e, "rcept_no": no})
    return [context(s, by.get(s, []), pol.get(s), acc.get(s), agm_records.get(s)) for s in sorted(universe)]

def build(days=HIST_DAYS):
    today = D.today()
    ctxs = contexts()
    try:
        earn_all = earn_sched.collect(watch=dv.div_universe())
    except Exception as ex:
        print(f"  [리서치] 실적발표 예정일 수집 실패 — A2 이사회형 앵커 없이 추정: {ex}")
        earn_all = []
    earn = {}
    for e in earn_all:
        earn.setdefault(e["stock"], []).append(e)
    med = medians(ctxs)
    companies, events = {}, []
    for c in ctxs:
        if not c["corp"] and not c["charter"]:
            continue
        preds, missed = forecast(c, today, earn.get(c["stock"], []), med)
        companies[c["stock"]] = company_out(c, missed)
        for p in preds:
            events.append({"corp": c["corp"], "stock": c["stock"], "market": c["market"], **p,
                           "confirm_date": dv.t_minus_2(p["record_date"]), "ex_date": t_minus(p["record_date"], 1),
                           "basis": f"{p['quarter']} {p['div_type']} · {p['method']} — {p['why']} · {p['anchor']} · 금액 {p['amount_basis']}"})
    events.sort(key=lambda x: x["confirm_date"] or x["record_date"])
    payload = {"generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "days": days,
               "medians": med, "grades": GRADES, "companies": companies, "count": len(events), "events": events}
    with _LOCK:
        save_json(OUT_PATH, payload)
    n = lambda m: sum(1 for e in events if e["method"] == m)
    print(f"저장: research.json — 종목 {len(companies)} · 예상 {len(events)}건 (고정 {n('고정')} · 유동 {n('유동')} · 기준일공고 {n('확정 기준일')})")
    return payload


# ── 검증 (§5.2) ──
def amount_hits(xs):
    """예상 금액 적중 — 정확히 같음 · ±5% · ±20% · 오차 중앙값 (특별배당 섞인 실제 회차는 따로 셈)"""
    if not xs:
        return {}
    err = [abs(x["pred_dps"] - x["actual_dps"]) / x["actual_dps"] * 100 for x in xs]
    pct = lambda k: f"{k}/{len(xs)} ({k / len(xs) * 100:.0f}%)"
    return {"같음": pct(sum(x["pred_dps"] == x["actual_dps"] for x in xs)), "±5%": pct(sum(e <= 5 for e in err)),
            "±20%": pct(sum(e <= 20 for e in err)), "오차 중앙값(%)": round(statistics.median(err), 1),
            "특별배당 섞인 회차": sum(x["actual_special"] for x in xs)}

def verify():
    today = D.today()
    ctxs = contexts()
    med = medians(ctxs)
    exps = sorted(_d(e["date"]) for e in _load(EXP_PATH, {}).get("events", []) if e.get("type") == "선·옵 동시만기")
    month = lambda ex: next((x for x in exps if x >= ex), None)
    rows = []
    for c in ctxs:
        for r in c["rounds"]:
            if r["src"] != "공시" or not r["decided"] or r["record"] < today - datetime.timedelta(days=400):
                continue
            cut = min([r["decided"]] + [n["date"] for n in c["rec_notices"] if n["record"] == r["record"]])
            v = view(c, cut)
            p, miss = predict_round(v, r["type"], r["fy"], r["q"], cut, [], med)
            base = {"stock": c["stock"], "corp": c["corp"], "type": r["type"], "quarter": f"{r['fy']} Q{r['q']}",
                    "actual_record": _iso(r["record"]), "actual_fixed": r["fixed"]}
            if p is None:
                rows.append({**base, "pred": "미실시 판정", "note": miss})
                continue
            prec = _d(p["record_date"])
            pex, aex = _d(t_minus(_iso(prec), 1)), _d(t_minus(_iso(r["record"]), 1))
            rows.append({**base, "pred": p["method"], "grade": p["grade"], "pred_record": p["record_date"], "alt": p["alt"],
                         "err": abs((prec - r["record"]).days),
                         "err_alt": {k: abs((_d(x) - r["record"]).days) for k, x in p["alt"].items()},
                         "month_ok": month(pex) == month(aex), "cond": p["cond"],
                         "pred_dps": int(p["per_share"]) if p["per_share"] else None, "actual_dps": int(r["dps"]) if r["dps"] else None,
                         "actual_special": r["special"],
                         "charter": (c["charter"].get(GROUP[r["type"]]) or {}).get("v", ""),
                         "prev_fixed": next((x["fixed"] for x in reversed(v["rounds"]) if x["type"] == r["type"]), None)})
    judged = [x for x in rows if x["pred"] in ("고정", "유동")]
    ok = [x for x in judged if (x["pred"] == "고정") == x["actual_fixed"]]
    flo = [x for x in rows if x["pred"] == "유동" and not x["actual_fixed"]]
    pct = lambda a, b: f"{a}/{b} ({a / b * 100:.0f}%)" if b else "0/0"
    medv = lambda xs: int(statistics.median(xs)) if xs else None
    cond = {}
    for k in ("D13", "D14"):
        base = [x for x in rows if (x.get("cond") or {}).get(k) is not None and x["charter"] == "O" and x["prev_fixed"]]
        on = [x for x in base if x["cond"][k]]
        cond[k] = {"대상": len(base), "켜짐": len(on), "켜짐 중 실제 유동": sum(1 for x in on if not x["actual_fixed"]),
                   "꺼짐 중 실제 유동": sum(1 for x in base if not x["cond"][k] and not x["actual_fixed"])}
    summary = {
        "회차": len(rows),
        "판정 적중(고정/유동)": pct(len(ok), len(judged)),
        "판정 내역": {m: sum(1 for x in rows if x["pred"] == m) for m in ("고정", "유동", "유동 가능", "판별 불가", "확정 기준일", "미실시 판정")},
        "구분 내역": {k: sum(1 for x in rows if x.get("grade") == k) for k in GRADES},
        "유동 기준일 오차 중앙값(일)": medv([x["err"] for x in flo]),
        "A1 오차 중앙값(일)": medv([x["err_alt"]["A1"] for x in flo if "A1" in x["err_alt"]]),
        "A2 오차 중앙값(일)": medv([x["err_alt"]["A2"] for x in flo if "A2" in x["err_alt"]]),
        "유동 기준일 ±7일 이내": pct(sum(1 for x in flo if x["err"] <= 7), len(flo)),
        "월물 적중": pct(sum(1 for x in rows if x.get("month_ok")), sum(1 for x in rows if "month_ok" in x)),
        "조건부 신호(정관 O · 직전 고정)": cond,
        "금액": {t or "전체": amount_hits([x for x in rows if x.get("pred_dps") and x.get("actual_dps") and (not t or x["type"] == t)])
                 for t in ("", "결산배당", "분기배당", "중간배당")},
        "한계": "정관(정기보고서 표)은 최신값 사용 — 과거 시점 값이 아님. 실적발표 예정일 과거분 없음 → 백테스트에서 A2 이사회형 미사용",
    }
    save_json(VERIFY_PATH, {"generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "summary": summary, "rows": rows})
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    wrong = [x for x in judged if (x["pred"] == "고정") != x["actual_fixed"]]
    print(f"\n판정 틀린 회차 {len(wrong)}건 (앞 20):")
    for x in wrong[:20]:
        print(f"  {x['corp']} {x['quarter']} {x['type']} 예측 {x['pred']} · 실제 기준일 {x['actual_record']}")
    return summary

if __name__ == "__main__":
    verify() if "--verify" in sys.argv else build()
