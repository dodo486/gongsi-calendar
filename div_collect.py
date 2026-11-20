# -*- coding: utf-8 -*-
"""배당 1단계 수집 — 원천을 받아 원천별 저장 파일에 그대로 쌓는다. 판단 없음. 설계 정본 docs/배당예측-워크플로우.md §4

DART 거래소공시(I) 원문 파싱 → div_history.json (dividends 와 공용 캐시)
DART 정기공시(A) 「배당관련 예측가능성」 표 → div_policy.json
DART 회사정보 결산월 → acc_mt.json
"""
import os, re, json, datetime, threading
from concurrent.futures import ThreadPoolExecutor
from fetch import listing, doc_text, api, KEY, corp_code
from base import DATA_DIR, save_json
import dividends as dv

D = datetime.date
POLICY_PATH = os.path.join(DATA_DIR, "div_policy.json")
ACC_PATH = os.path.join(DATA_DIR, "acc_mt.json")
HIST_PATH = dv.HIST_PATH
DART = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
LOCK = threading.RLock()
HIST_DAYS = 800
POLICY_DAYS = 400
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
    with LOCK:
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
    with LOCK:
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
