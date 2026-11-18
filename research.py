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
import sys, os, re, json, datetime, calendar, statistics, threading
from concurrent.futures import ThreadPoolExecutor
from fetch import listing, doc_text, api, KEY, corp_code
from base import DATA_DIR, save_json
import dividends as dv
from krx_cal import t_minus
import earn_sched

D = datetime.date
OUT_PATH = os.path.join(DATA_DIR, "research.json")
VERIFY_PATH = os.path.join(DATA_DIR, "research_verify.json")
POLICY_PATH = os.path.join(DATA_DIR, "div_policy.json")
ACC_PATH = os.path.join(DATA_DIR, "acc_mt.json")
EXP_PATH = os.path.join(DATA_DIR, "expiries.json")
HIST_PATH = dv.HIST_PATH
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
TYPE_QUARTERS = {"분기배당": (1, 2, 3), "중간배당": (2,), "결산배당": (4,)}
GROUP = {"결산배당": "결산", "중간배당": "분기중간", "분기배당": "분기중간"}
PARSE_VERSION = {"notice": 2, "agm_call": 2, "agm_result": 2, "plan": 2}


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

def quarter_end(fy, q, acc):
    m, y = acc - 3 * (4 - q), fy
    if m <= 0:
        m, y = m + 12, y - 1
    return D(y, m, calendar.monthrange(y, m)[1])

def round_of(t, record, decided, acc):
    """회차가 몇 사업연도 몇 분기 몫인지 — 기준일이 그 종류의 분기말이면 그 분기, 아니면 결정(또는 기준일) 직전 분기말"""
    ref = decided if decided and decided < record else record
    cands = [(fy, q, quarter_end(fy, q, acc)) for fy in range(record.year - 1, record.year + 2) for q in TYPE_QUARTERS[t]]
    same = [c for c in cands if c[2] == record]
    return same[0] if same else max((c for c in cands if c[2] < ref), key=lambda c: c[2])


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
    typ = ("분기배당" if re.search(r"분기\s*배당\s*기준일", t) else
           "중간배당" if re.search(r"중간\s*배당\s*기준일", t) else "결산배당")
    return {"is_div": bool(re.search(r"배당\s*기준일", t)), "div_type": typ, "months": months}

def parse_agm(t):
    dt = re.search(r"주주총회일자\s*(\d{4}-\d{2}-\d{2})", t) or re.search(r"일시\s*(\d{4}-\d{2}-\d{2})", t)
    return {"agm_date": dt[1] if dt else "", "charter": parse_charter(t),
            "regular": bool(re.search(r"구분\s*정기주주총회|정기주주총회\s*결과", t)),
            "rejected": bool(re.search(r"특별결의[^.]{0,100}?부결", t))}

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
    """종목별 최신 정기보고서의 배당 표 (없으면 직전 보고서로, 최대 4건)"""
    cache = _load(POLICY_PATH, {})
    by = {}
    for r in listing("A", POLICY_DAYS):
        if r["stock_code"] in universe and _periodic(r["report_nm"]):
            by.setdefault(r["stock_code"], []).append(r)
    for rs in by.values():
        rs.sort(key=lambda r: r["rcept_no"], reverse=True)
    def first_table(stock):
        fallback = None
        for r in by[stock][:4]:
            if r["rcept_no"] not in cache:
                try:
                    cache[r["rcept_no"]] = {**parse_policy(doc_text(r["rcept_no"])), "rcept_dt": r["rcept_dt"],
                                            "report": re.sub(r"\s+", " ", r["report_nm"]).strip()}
                except Exception:
                    continue
            p = cache[r["rcept_no"]]
            if p.get("charter"):
                return stock, p
            fallback = fallback or (p if p.get("charter_text") else None)
        return stock, fallback
    todo = sum(1 for s in by for r in by[s][:4] if r["rcept_no"] not in cache)
    if todo:
        print(f"  [정기보고서] 원문 최대 {todo}건 받는 중")
    with ThreadPoolExecutor(2) as ex:
        out = dict(ex.map(first_table, list(by)))
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
def _agm_fy(agm, acc):
    return max(fy for fy in range(agm.year - 2, agm.year + 1) if quarter_end(fy, 4, acc) < agm)

def context(stock, entries, policy, acc, agm_records):
    """한 종목의 회차·정관·결정기관·예고·주총·계획을 판정에 쓰는 모양으로"""
    acc_known = acc is not None
    acc = acc or 12
    corp = market = ""
    dec, rec_notices, notices, agms, plans = {}, [], [], [], []
    for e in sorted(entries, key=lambda x: x["rcept_dt"]):
        corp, market = e.get("corp") or corp, e.get("market") or market
        k, rd = e["kind"], _d(e["rcept_dt"])
        if k == "decision" and e.get("record_date") and e.get("div_type") in TYPE_QUARTERS:
            dec.setdefault((e["div_type"], e["record_date"]), []).append(e)
        elif k == "record" and e.get("record_date"):
            rec_notices.append({"date": rd, "record": _d(e["record_date"]), "div_type": e.get("div_type", "")})
        elif k == "notice" and e.get("is_div"):
            t = e["div_type"]
            fy, q, _ = min(((fy, q, quarter_end(fy, q, acc)) for fy in range(rd.year - 1, rd.year + 2) for q in TYPE_QUARTERS[t]
                            if quarter_end(fy, q, acc) >= rd - datetime.timedelta(days=NOTICE_LOOKBACK_DAYS)), key=lambda c: c[2])
            hint = next((m for m in e.get("months", []) if m > rd.strftime("%Y-%m")), "")
            notices.append({"date": rd, "div_type": t, "fy": fy, "q": q, "hint": hint, "title": e.get("title", "")})
        elif k in ("agm_call", "agm_result") and e.get("agm_date"):
            agms.append({"date": _d(e["agm_date"]), "kind": k, "rcept": rd, "charter": e.get("charter"),
                         "regular": e.get("regular", False), "rejected": e.get("rejected", False)})
        elif k == "plan" and e.get("lines"):
            plans.append({"date": rd, "title": e.get("title", ""), "lines": e["lines"]})

    rounds = []
    for (t, rds), es in dec.items():
        record, decided = _d(rds), _d(es[0]["rcept_dt"])
        fy, q, qe = round_of(t, record, decided, acc)
        rounds.append({"type": t, "fy": fy, "q": q, "qe": qe, "record": record, "decided": decided,
                       "dps": es[-1].get("per_share", ""), "pay": _norm(es[-1].get("pay_date")), "src": "공시"})
    seen = {(r["type"], r["record"]) for r in rounds}
    for t, decided, record in (policy or {}).get("rows", []):
        record, decided = _d(record), _d(decided)
        if record and (t, record) not in seen:
            fy, q, qe = round_of(t, record, decided, acc)
            rounds.append({"type": t, "fy": fy, "q": q, "qe": qe, "record": record, "decided": decided,
                           "dps": "", "pay": None, "src": "정기보고서 표"})
            seen.add((t, record))
    rounds.sort(key=lambda r: r["record"])
    for r in rounds:
        r["fixed"] = r["record"] == r["qe"]
        r["amount_first"] = bool(r["decided"] and r["decided"] < r["record"])
        prior = sorted(int(x["dps"]) for x in rounds if x["type"] == r["type"] and x["record"] < r["record"] and x["dps"])
        med = prior[len(prior) // 2] if prior else 0
        r["special"] = bool(med and r["dps"] and int(r["dps"]) > SPECIAL_MULT * med)
    for a in agms:
        a["fy"] = _agm_fy(a["date"], acc)

    charter_cands = {"결산": [], "분기중간": []}
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
        elif a["kind"] == "agm_call" and not any(c["kind"] == "agm_result" and c["date"] == a["date"] for c in agms):
            pending = {"date": _iso(a["date"]), "block": block}
    charter = {g: max(c, key=lambda x: x["date"]) for g, c in charter_cands.items() if c}
    return {"stock": stock, "corp": corp, "market": market, "acc": acc, "acc_known": acc_known,
            "rounds": rounds, "rec_notices": rec_notices, "notices": notices, "agms": agms, "plans": plans,
            "agm_records": sorted(agm_records or []), "charter": charter, "charter_pending": pending,
            "organ": (policy or {}).get("organ") or {}, "policy_report": (policy or {}).get("report", "")}

def view(ctx, cut):
    """cut 이전에 공시된 것만 남긴 ctx — 백테스트(§5.2)·확정 회차 사다리용"""
    v = dict(ctx)
    v["rounds"] = [r for r in ctx["rounds"] if (r["decided"] or r["record"]) < cut]
    v["rec_notices"] = [n for n in ctx["rec_notices"] if n["date"] < cut]
    v["notices"] = [n for n in ctx["notices"] if n["date"] < cut]
    v["agms"] = [a for a in ctx["agms"] if a["rcept"] < cut]
    v["agm_records"] = [d for d in ctx["agm_records"] if d < cut]
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
    return next((n for n in reversed(ctx["notices"]) if n["div_type"] == t and (n["fy"], n["q"]) == (fy, q)), None)

def judge(ctx, t, fy, q):
    """§4.2 — (방식, 근거, 바뀔 수 있음)"""
    notice = _notice(ctx, t, fy, q)
    if notice:
        return "유동", f"이번 회차 '배당기준일 안내' 자율공시({notice['date']}) — 이사회가 기준일을 따로 정함", False
    past = [r for r in ctx["rounds"] if r["type"] == t]
    ch = ctx["charter"].get(GROUP[t])
    if past:
        last = past[-1]
        if not last["fixed"]:
            return "유동", f"직전 회차({last['fy']} Q{last['q']}) 기준일 {last['record']}이 결산기말·분기말({last['qe']})이 아님 — 이사회가 따로 정함", False
        if ch and ch["v"] == "O":
            return "고정", f"직전 회차 기준일 {last['record']} = 결산기말·분기말. 다만 정관상 이사회가 기준일을 따로 정할 수 있어 바뀔 수 있음", True
        return "고정", f"직전 회차 기준일 {last['record']} = 결산기말·분기말", False
    if ch:
        return ("유동 가능", f"배당 이력 없음 · 정관상 이사회가 기준일을 따로 정할 수 있음 ({ch['src']})", False) if ch["v"] == "O" else \
               ("고정", f"배당 이력 없음 · 정관상 기준일이 결산기말로 고정 ({ch['src']})", False)
    return "판별 불가", "정관 정보·배당 이력 없음", False

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
                   else f"기준일이 결산기말로 고정 — {ch['src']}") if ch else "정관 정보 없음")
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
    method, why, may_change = judge(ctx, t, fy, q)
    organ = ctx["organ"].get(GROUP[t], "")
    regular = [a for a in ctx["agms"] if a["regular"]] if t == "결산배당" else []
    agm = next((a for a in reversed(regular) if a["fy"] == fy), None)
    prev_agm = next((a for a in reversed(regular) if prev and a["fy"] == prev["fy"]), None)
    ann_rec = next((n for n in reversed(ctx["rec_notices"]) if n["div_type"] in ("", t) and qe <= n["record"] <= qe + datetime.timedelta(days=SEASON_DAYS[t])), None)
    notice = _notice(ctx, t, fy, q)
    warn, alt, anchor_event = [], {}, None

    if not ann_rec and today > qe + datetime.timedelta(days=SEASON_DAYS[t]):
        return None, f"⚠ {fy} Q{q} {t}: 시즌 창(결산기말·분기말 {qe} +{SEASON_DAYS[t]}일) 지나도 배당 공시 없음 → 미실시로 보고 예상 제외"
    if ann_rec:
        rec, anchor = ann_rec["record"], f"기준일 공고 {ann_rec['date']} (금액은 아직 미공시)"
    elif method == "고정":
        rec, anchor = qe, "결산기말·분기말 고정"
    else:
        if same_q and not same_q["fixed"]:
            alt["A1"] = same_q["record"] + datetime.timedelta(weeks=52 * (fy - same_q["fy"]))
        if t == "결산배당" and organ == "주주총회" and agm:
            off = (prev["record"] - prev_agm["date"]).days if prev and prev_agm and not prev["fixed"] else med["agm"]
            alt["A2"], anchor_event = agm["date"] + datetime.timedelta(days=off), agm["date"]
            a2_txt = f"올해 정기주총({agm['date']}) + 작년 주총→기준일 간격 {off}일"
        else:
            ann = announce_date(earn, qe)
            if ann:
                off = ((prev["record"] - prev["decided"]).days if prev and not prev["fixed"] and prev["amount_first"]
                       else med["dec"][t])
                alt["A2"], anchor_event = ann + datetime.timedelta(days=off), ann
                a2_txt = f"올해 실적발표 예정일({ann}) + 작년 배당결정→기준일 간격 {off}일"
        if "A2" in alt:
            rec, anchor = alt["A2"], a2_txt
        elif "A1" in alt:
            rec, anchor = alt["A1"], f"작년 같은 회차 기준일({same_q['record']}) + 52주"
        else:
            rec, anchor = qe + datetime.timedelta(days=med["qe"][t]), f"이 회사 이력 없음 — 결산기말·분기말 + 수집대상 전체 중앙값 {med['qe'][t]}일"
        if notice and notice["hint"]:
            floor = D(int(notice["hint"][:4]), int(notice["hint"][5:]), 1)
            if rec < floor:
                rec, anchor = floor, anchor + f" · 자율공시 '{notice['hint']}' 이후로 보정"
        floor = today + datetime.timedelta(days=NOTICE_MIN_DAYS)
        if rec < floor:
            rec, anchor = floor, anchor + f" · 기준일 공고 전이라 {NOTICE_MIN_DAYS}일 뒤({floor}) 이후로 보정"
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
    return {"div_type": t, "fy": fy, "q": q, "quarter": f"{fy} Q{q}", "quarter_end": _iso(qe),
            "record_date": _iso(rec), "pay_date": _iso(pay), "method": "확정 기준일" if ann_rec else method,
            "may_change": may_change, "why": why, "anchor": anchor,
            "alt": {k: _iso(v) for k, v in alt.items()}, "warn": warn,
            "cond": cond_signals(ctx, qe, today) if t == "결산배당" else {},
            "per_share": reg["dps"] if reg else "",
            "amount_basis": f"직전 정기 회차({reg['fy']} Q{reg['q']}) {int(reg['dps']):,}원 반복" if reg else "금액 이력 없음",
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
        m, why, may = judge(ctx, t, fy, q)
        judgments[t] = {"method": m, "why": why, "may_change": may, "quarter": f"{fy} Q{q}"}
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
    for e in hist.values():
        if e.get("stock") in universe:
            by.setdefault(e["stock"], []).append(e)
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
               "medians": med, "companies": companies, "count": len(events), "events": events}
    with _LOCK:
        save_json(OUT_PATH, payload)
    n = lambda m: sum(1 for e in events if e["method"] == m)
    print(f"저장: research.json — 종목 {len(companies)} · 예상 {len(events)}건 (고정 {n('고정')} · 유동 {n('유동')} · 기준일공고 {n('확정 기준일')})")
    return payload


# ── 검증 (§5.2) ──
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
            rows.append({**base, "pred": p["method"], "pred_record": p["record_date"], "alt": p["alt"],
                         "err": abs((prec - r["record"]).days),
                         "err_alt": {k: abs((_d(x) - r["record"]).days) for k, x in p["alt"].items()},
                         "month_ok": month(pex) == month(aex), "cond": p["cond"],
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
        "유동 기준일 오차 중앙값(일)": medv([x["err"] for x in flo]),
        "A1 오차 중앙값(일)": medv([x["err_alt"]["A1"] for x in flo if "A1" in x["err_alt"]]),
        "A2 오차 중앙값(일)": medv([x["err_alt"]["A2"] for x in flo if "A2" in x["err_alt"]]),
        "유동 기준일 ±7일 이내": pct(sum(1 for x in flo if x["err"] <= 7), len(flo)),
        "월물 적중": pct(sum(1 for x in rows if x.get("month_ok")), sum(1 for x in rows if "month_ok" in x)),
        "조건부 신호(정관 O · 직전 고정)": cond,
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
