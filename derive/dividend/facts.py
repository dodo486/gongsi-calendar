# -*- coding: utf-8 -*-
"""배당 사실 — 저장소 → 종목별 사실(공시 회차·분할·정관 이력·주총·예고·계획·사업연도별 EPS·배당·정책 장부). view(ctx, cut) = cut 전까지 알던 것만. 추정 없음"""
import datetime, calendar, statistics
from core import store, universe
from core.paths import load_json, DIV_POLICY_PATH, DIV_CHECKED_PATH
from core.krx_cal import to_date, iso, ymd, days_ago, days_between

# ── 회차 — 배당 하나 = 종목 · 배당종류 · 사업연도 · 분기 ──
TYPE_QUARTERS = {"분기배당": (1, 2, 3), "중간배당": (2,), "결산배당": (4,)}

def quarter_end(fy, q, acc):
    m, y = acc - 3 * (4 - q), fy
    if m <= 0:
        m, y = m + 12, y - 1
    return datetime.date(y, m, calendar.monthrange(y, m)[1])

def slot_of(t, ref, acc, record=None, upcoming=False):
    """배당 한 건이 몇 사업연도 몇 분기 몫인지 — 기준일이 그 종류의 분기말이면 그 분기. 예고(upcoming)는 ref 뒤 첫 분기말.
    아니면 결산은 ref 직전 결산기말(지난 사업연도 몫), 분기·중간은 ref 가 든 사업연도에서 ref 직전 분기말(아직 없으면 그 해 첫 분기 —
    이사회가 반기말 전에 중간배당을 정하면 그 해 몫, 3분기보고서 누적도 그렇게 셈). ref = 결의일(없으면 기준일)·공고일 등"""
    cands = [(fy, q, quarter_end(fy, q, acc)) for fy in range(ref.year - 1, ref.year + 2) for q in TYPE_QUARTERS[t]]
    same = [c for c in cands if c[2] == record]
    if same:
        return same[0]
    if upcoming:
        return min((c for c in cands if c[2] >= ref), key=lambda c: c[2])
    if t == "결산배당":
        return max((c for c in cands if c[2] < ref), key=lambda c: c[2])
    fy = next(fy for fy in range(ref.year - 1, ref.year + 2) if ref <= quarter_end(fy, 4, acc))
    mine = [c for c in cands if c[0] == fy]
    return max((c for c in mine if c[2] <= ref), key=lambda c: c[2], default=min(mine, key=lambda c: c[2]))

SAME_ROUND_GAP = 31

def assemble(entries, acc):
    """배당결정·기준일 공고 → 공시 회차. 접수 순으로 돌아 같은 회차는 나중 공시(정정)가 이긴다.
    같은 회차 = 같은 배당종류·사업연도·분기(정정으로 기준일이 바뀌어도 한 건). 배당종류를 못 읽은 공시는 같은 기준일 회차에 붙인다.
    반환: 회차마다 {code, name, market, div_type, record_date, per_share, pay_date,
    decided(첫 배당결정 접수일 ISO, 없으면 ""), rcept_no(최근 배당결정, 없으면 기준일 공고), rcept_dt(최근 접수일),
    note(「기타 투자판단」 정책 문장), tax_rule(고배당기업 과세특례 요건 충족을 밝힘), total(배당금총액 원)}"""
    merged = {}
    def key_of(e, decided):
        t, rec = e["div_type"], e["record_date"]
        if t not in TYPE_QUARTERS:
            same = [k for k, r in merged.items() if r["record_date"] == rec]
            if len(same) == 1:
                return same[0]
        if t not in TYPE_QUARTERS or not rec:
            return (t, rec)
        rd, dec = to_date(rec), to_date(decided)
        fy, q, _ = slot_of(t, dec if dec and dec < rd else rd, acc, rd)
        old = merged.get((t, fy, q), {}).get("record_date")
        if old and days_between(old, rec) > SAME_ROUND_GAP:
            return (t, rec)   # 배당종류를 잘못 읽은 다른 회차 — 정정은 기준일을 한 달 넘게 옮기지 않음
        return (t, fy, q)
    order = sorted(entries, key=lambda e: (e["kind"] != "decision", e["rcept_dt"], e["rcept_no"]))
    for e in order:
        if e["kind"] == "record" and not e.get("record_date"):
            continue
        dec = e["kind"] == "decision"
        r = merged.setdefault(key_of(e, iso(e["rcept_dt"]) if dec else None),
                              {"code": e["code"], "name": e["name"], "market": e["market"], "div_type": "",
                               "per_share": "", "record_date": "", "pay_date": "", "decided": "", "rcept_no": e["rcept_no"], "rcept_dt": "",
                               "note": [], "tax_rule": False, "total": 0})
        r["div_type"] = e["div_type"] if e.get("div_type") in TYPE_QUARTERS else r["div_type"] or e.get("div_type") or ""
        r["rcept_dt"] = max(r["rcept_dt"], e["rcept_dt"])
        if e.get("record_date"):
            r["record_date"] = e["record_date"]
        if dec:
            r["per_share"] = e.get("per_share") or r["per_share"]
            r["pay_date"] = e.get("pay_date") or r["pay_date"]
            r["note"], r["tax_rule"] = e.get("note") or r["note"], e.get("tax_rule", False) or r["tax_rule"]
            r["total"] = e.get("total") or r["total"]
            r["decided"] = r["decided"] or iso(e["rcept_dt"])
            r["rcept_no"] = e["rcept_no"]
    return [r for r in merged.values() if r["record_date"]]

DOC_KINDS = ("decision", "record", "notice", "split", "stock_div", "treasury", "exdiv", "agm_call", "agm_result", "plan", "return_plan")
POLICY_DAYS = 400
POLICY_YEARS = 3   # 사업연도를 안 밝힌 정책 줄 = 공시한 해부터 3개 사업연도 (밸류업·중기 정책 기간)

NOTICE_LOOKBACK_DAYS = 45
SPECIAL_MULT = 2
EARN_UP, MIN_SAMPLES = 1.1, 20   # 이익 증가 회사 배당 증가율 — 그 사업연도 전 해들에서 배움
ANNUAL_REPORT_DAYS, HALF_REPORT_DAYS, Q3_REPORT_DAYS = 90, 45, 45   # 사업보고서 · 반기보고서 · 3분기보고서 제출 기한 — 그 뒤부터 안 값
CASH_SHARE_YEARS = 3   # 총환원 중 현금배당 비율 = 최근 이만큼 사업연도 현금흐름표 배당금 지급 ÷ (배당금 지급 + 자기주식 취득)

def spiked(prior, v):
    """튄 값인가 — 그 전 값들 중앙의 SPECIAL_MULT 배 넘음 (특별배당·일회성 이익 판정 한 곳)"""
    return bool(prior) and v > SPECIAL_MULT * statistics.median(prior)

def share_factor(splits, a, b):
    """a 날짜 주식 1주가 b 날짜에 몇 주인지 — 그 사이 효력이 난 분할·병합 배수의 곱"""
    f = 1.0
    for x in splits:
        if a < x["effective"] <= b:
            f *= x["factor"]
    return f

def context(stock, entries, policy, acc, agm_records):
    """한 종목의 회차·정관·결정기관·예고·주총·계획을 판정에 쓰는 모양으로"""
    acc_known = acc is not None
    acc = acc or 12
    corp = market = ""
    rec_notices, notices, agms, statements, splits, treasury, exdivs = [], [], [], [], [], [], []
    for e in sorted(entries, key=lambda x: (x["rcept_dt"], x["rcept_no"])):
        corp, market = e.get("name") or corp, e.get("market") or market
        k, rd = e["kind"], to_date(e["rcept_dt"])
        if k in ("split", "stock_div") and e.get("effective"):   # 주식배당도 주식 수 배수(분할과 같은 모양)
            sp = {"date": rd, "effective": to_date(e["effective"]), "factor": e["factor"]}
            splits = [x for x in splits if not (x["factor"] == sp["factor"] and abs((x["effective"] - sp["effective"]).days) <= 120)] + [sp]
        elif k == "treasury" and e.get("end"):
            treasury.append({"date": rd, "delta": e["delta"], "end": to_date(e["end"])})
        elif k == "exdiv" and e.get("ex_date"):
            exdivs.append({"date": rd, "ex_date": e["ex_date"], "reason": e.get("reason", ""), "rcept_no": e["rcept_no"]})
        elif k == "record" and e.get("record_date"):
            rec_notices.append({"date": rd, "record": to_date(e["record_date"]), "div_type": e.get("div_type", "")})
        elif k == "notice" and e.get("is_div"):
            t = e["div_type"]
            fy, q = e.get("fyq") or slot_of(t, rd - datetime.timedelta(days=NOTICE_LOOKBACK_DAYS), acc, upcoming=True)[:2]
            hint = next((m for m in e.get("months", []) if m > rd.strftime("%Y-%m")), "")
            notices.append({"date": rd, "div_type": t, "fy": fy, "q": q, "hint": hint, "title": e.get("title", ""), "rcept_no": e["rcept_no"]})
        elif k in ("agm_call", "agm_result") and e.get("agm_date"):
            agms.append({"date": to_date(e["agm_date"]), "kind": k, "rcept": rd, "charter": e.get("charter"),
                         "regular": e.get("regular", False), "rejected": e.get("rejected", False)})
        elif k in ("plan", "return_plan") and (e.get("lines") or e.get("rows") or e.get("text")):
            statements.append({"date": rd, "kind": k, "title": e.get("subject") or e.get("title", ""), "rcept_no": e["rcept_no"],
                               "lines": e.get("lines", []), "text": e.get("text", ""), "rows": e.get("rows", [])})

    filings = assemble([{**e, "div_type": e.get("div_type") or ""} for e in entries if e["kind"] in ("decision", "record")], acc)
    rounds = []
    for r in filings:
        if not r["decided"] or r["div_type"] not in TYPE_QUARTERS:
            continue
        t, record, decided = r["div_type"], to_date(r["record_date"]), to_date(r["decided"])
        fy, q, qe = slot_of(t, decided if decided < record else record, acc, record)
        rounds.append({"type": t, "fy": fy, "q": q, "qe": qe, "record": record, "decided": decided,
                       "dps": r["per_share"], "pay": to_date(r["pay_date"]), "src": "공시", "rcept_no": r["rcept_no"],
                       "note": r["note"], "tax_rule": r["tax_rule"], "total": r["total"]})
    seen = {(r["type"], r["record"]) for r in rounds}
    for t, decided, record in (policy or {}).get("rows", []):
        record, decided = to_date(record), to_date(decided)
        if decided and record and decided < record - datetime.timedelta(days=180):
            decided = None   # 회사 표 오기(확정일이 기준일보다 반년 넘게 앞섬 — 예: 2025-02-06 → 2026-02-28)
        if record and (t, record) not in seen:
            fy, q, qe = slot_of(t, decided if decided and decided < record else record, acc, record)
            rounds.append({"type": t, "fy": fy, "q": q, "qe": qe, "record": record, "decided": decided,
                           "dps": "", "pay": None, "src": "정기보고서 표", "rcept_no": "", "note": [], "tax_rule": False, "total": 0})
            seen.add((t, record))
    rounds.sort(key=lambda r: r["record"])
    for r in rounds:
        r["fixed"] = r["record"] == r["qe"]
        r["amount_first"] = bool(r["decided"] and r["decided"] < r["record"])
        prior = list(int(x["dps"]) / share_factor(splits, x["record"], r["record"]) for x in rounds
                       if x["type"] == r["type"] and x["record"] < r["record"] and x["dps"])
        r["special"] = bool(r["dps"]) and spiked(prior, int(r["dps"]))
    for a in agms:
        a["fy"] = slot_of("결산배당", a["date"], acc)[0]

    charter_cands = {"결산": [], "분기중간": []}
    seen_v = {"결산": [], "분기중간": []}
    for h in (policy or {}).get("charter_hist", []):
        for g, v in h["charter"].items():
            if v in ("O", "X"):
                seen_v[g].append((to_date(h["date"]), v))
    if policy and policy.get("charter"):
        for g, v in policy["charter"].items():
            if v in ("O", "X"):
                charter_cands[g].append({"v": v, "date": to_date(policy["rcept_dt"]), "src": f"정기보고서 표 · {policy['title']}"})
    elif policy and policy.get("charter_text"):
        charter_cands["결산"].append({"v": policy["charter_text"], "date": to_date(policy["rcept_dt"]),
                                     "src": f"정관 전문 · {policy['title']}"})
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
            pending = {"date": iso(a["date"]), "block": block}
    charter = {g: max(c, key=lambda x: x["date"]) for g, c in charter_cands.items() if c}
    since = {}
    agm_days = sorted(a["date"] for a in agms if a["kind"] == "agm_result" and not a["rejected"])
    for g, vs in seen_v.items():
        last_x = max((d for d, v in vs if v == "X"), default=None)
        o = min((d for d, v in vs if v == "O" and (last_x is None or d > last_x)), default=None)
        if o:
            since[g] = max((d for d in agm_days if d <= o and (last_x is None or d > last_x)), default=o)
    return {"code": stock, "name": corp, "market": market, "acc": acc, "acc_known": acc_known,
            "filings": filings, "rounds": rounds, "rec_notices": rec_notices, "notices": notices, "agms": agms, "statements": statements, "treasury": treasury, "exdivs": exdivs,
            "agm_records": sorted(agm_records or []), "charter": charter, "charter_since": since, "charter_pending": pending, "splits": splits,
            "organ": (policy or {}).get("organ") or {}, "policy_report": (policy or {}).get("title", "")}

def view(ctx, cut):
    """cut 이전에 공시된 것만 남긴 ctx — 검증·확정 회차 사다리용"""
    v = dict(ctx)
    v["rounds"] = [r for r in ctx["rounds"] if (r["decided"] or r["record"]) < cut]
    v["rec_notices"] = [n for n in ctx["rec_notices"] if n["date"] < cut]
    v["notices"] = [n for n in ctx["notices"] if n["date"] < cut]
    v["agms"] = [a for a in ctx["agms"] if a["rcept"] < cut]
    v["agm_records"] = [d for d in ctx["agm_records"] if d < cut]
    v["charter_since"] = {g: d for g, d in ctx["charter_since"].items() if d < cut}
    v["splits"] = [x for x in ctx["splits"] if x["date"] < cut]
    v["treasury"] = [x for x in ctx["treasury"] if x["date"] < cut]
    v["cons"] = [x for x in ctx["cons"] if x["date"] < cut]
    v["statements"] = [p for p in ctx["statements"] if p["date"] < cut]
    v["policies"] = [{**p, "date": s[-1][0], "src": s[-1][1]} for p in ctx["policies"] if (s := [x for x in p["said"] if x[0] < cut])]   # 그때까지 마지막으로 말한 공시가 대표
    v["share_counts"] = {d: n for d, n in ctx["share_counts"].items() if d + datetime.timedelta(days=ANNUAL_REPORT_DAYS if d.month == 12 else HALF_REPORT_DAYS) < cut}
    v["cash_flow"] = {fy: d for fy, d in ctx["cash_flow"].items() if quarter_end(fy, 4, ctx["acc"]) + datetime.timedelta(days=ANNUAL_REPORT_DAYS) < cut}
    v["years"] = {}
    for fy, d in ctx["years"].items():
        for k, x in d.items():
            q, days = (3, Q3_REPORT_DAYS) if k.startswith("q3_") else (4, ANNUAL_REPORT_DAYS)
            if quarter_end(fy, q, ctx["acc"]) + datetime.timedelta(days=days) < cut:
                v["years"].setdefault(fy, {})[k] = x
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

def year_steps(ctx):
    """분기배당 새해 인상 — [(사업연도, 그해 첫 분기배당 ÷ 작년 마지막 분기배당)] (특별배당 회차 빼고, 분할·병합 보정)"""
    q = [r for r in ctx["rounds"] if r["type"] == "분기배당" and r["dps"] and not r["special"]]
    out = []
    for r in q:
        first = min((x for x in q if x["fy"] == r["fy"]), key=lambda x: x["q"])
        last = max((x for x in q if x["fy"] == r["fy"] - 1), key=lambda x: x["q"], default=None)
        if r is first and last:
            out.append((r["fy"], int(r["dps"]) * share_factor(ctx["splits"], last["record"], r["record"]) / int(last["dps"])))
    return out

def medians(ctxs):
    """수집대상 전체 실측 중앙값 — 이력 없는 회사용 기본 오프셋, 이익 증가 회사의 결산 배당 증가율·분기배당 새해 인상(사업연도별, 그 전 해들만)"""
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
    up = []   # (사업연도, 결산 몫 ÷ 작년 결산 몫) — 3분기 누적 EPS 가 작년보다 EARN_UP 배 이상 늘었던 회사
    for c in ctxs:
        y = c["years"]
        for fy, d in y.items():
            p = y.get(fy - 1, {})
            fin, pfin = final_of(c, fy), final_of(c, fy - 1)
            if fin and pfin and d.get("q3_eps", 0) > 0 and p.get("q3_eps", 0) > 0 and d["q3_eps"] >= EARN_UP * p["q3_eps"]:
                up.append((fy, fin / pfin))
    fys = {fy for fy, _ in up}
    steps = [s for c in ctxs for s in year_steps(c)]
    sfys = {fy for fy, _ in steps}
    return {"qe": {t: md(acc["qe"].get(t), 60 if t == "결산배당" else 45) for t in TYPE_QUARTERS},
            "dec": {t: md(acc["dec"].get(t), 21) for t in TYPE_QUARTERS},
            "pay": {t: md(acc["pay"].get(t), 30) for t in TYPE_QUARTERS},
            "agm": md(acc["agm"], 7),
            "dps_up": {fy: statistics.median(xs) for fy in range(min(fys, default=0) + 1, max(fys, default=0) + 2)
                       if len(xs := [r for f, r in up if f < fy]) >= MIN_SAMPLES},
            "q_step": {fy: statistics.median(xs) for fy in range(min(sfys, default=0) + 1, max(sfys, default=0) + 2)
                       if len(xs := [r for f, r in steps if f < fy]) >= MIN_SAMPLES}}

def contexts():
    """저장소 → 배당 대상 종목마다 사실 묶음"""
    stocks = universe.div()
    by, agm_records, pol = {}, {}, {}
    for k in DOC_KINDS:
        for no, e in store.load(k).items():
            if e["code"] in stocks:
                by.setdefault(e["code"], []).append({**e, "rcept_no": no})
    for e in store.load("agm_record").values():
        if e["code"] in stocks:
            agm_records.setdefault(e["code"], []).append(to_date(e["rcept_dt"]))
    cut = ymd(days_ago(POLICY_DAYS))
    for no, p in sorted(store.load("charter_table").items(), reverse=True):   # 최신 보고서부터
        if p["code"] in stocks and p["rcept_dt"] >= cut:
            pol.setdefault(p["code"], []).append(p)
    for st, got in pol.items():   # 최신 정기보고서 배당 표(없으면 정관 전문) + 기간 안 보고서들의 정관 이력
        latest = next((p for p in got if p.get("charter")), None) or next((p for p in got if p.get("charter_text")), None)
        pol[st] = latest and {**latest, "charter_hist": [{"date": p["rcept_dt"], "charter": p.get("charter") or {"결산": p["charter_text"]}}
                                                         for p in got if p.get("charter") or p.get("charter_text")]}
    acc = {s: int(m) for s, m in store.load("acc_mt").items() if str(m).isdigit()}
    cons, alot, fin, counts = store.load("consensus"), store.load("alot"), store.load("fin"), store.load("shares")
    human = {}
    for p in load_json(DIV_POLICY_PATH, {}).get("rows", []):
        human.setdefault(p["code"], []).append({**p, "date": to_date(p["date"])})
    checked = {}   # 회사 → {"사람": {열쇠: 판정}, "판독자": {열쇠: [공시마다 판정]}}
    for d in load_json(DIV_CHECKED_PATH, {}).get("decisions", []):
        who = checked.setdefault(d["code"], {"사람": {}, "판독자": {}})
        if d["by"] == "사람":
            who["사람"][d["sig"]] = d
        else:
            who["판독자"].setdefault(d["sig"], []).append(d)
    since = to_date(min((e["rcept_dt"] for e in store.load("decision").values()), default="99991231"))
    out = [{**context(s, by.get(s, []), pol.get(s), acc.get(s), agm_records.get(s)), "filed_since": since,
             "cons": [{**x, "date": to_date(x["date"])} for x in cons.get(s, [])],
             "years": _years(alot.get(s, {}).get("years", {})),
             "cash_flow": {int(fy): d for fy, d in fin.get(s, {}).get("years", {}).items()},
             "share_counts": {to_date(d): n for d, n in counts.get(s, {}).get("dates", {}).items()}} for s in sorted(stocks)]
    for c in out:   # 정책 목록 = 정책 사실(같은 내용은 여러 공시가 말해도 한 줄, 출처 srcs) − 사람이 틀림 판정한 사실 + 사람 줄
        facts_ = {}
        sts = sorted(c["statements"], key=lambda st: (st["date"], st["rcept_no"]))   # 같은 날이면 접수번호 뒤(정정)가 나중
        said = said_bases([r for st in sts for r in st["rows"]] + human.get(c["code"], []))
        for st in sts:
            for r in st["rows"]:
                f = fact_of(r, st["date"])
                if not f:
                    continue
                p = {**f, "base": _base(r, said), "date": st["date"], "src": st["rcept_no"], "title": st["title"], "by": "공시 자동"}
                sig = policy_sig(p)
                old = facts_.pop(sig, {})   # 같은 사실은 가장 최근 공시가 대표, 말한 공시는 모두 said(날짜·접수번호) — 검증은 그때까지 말한 것만(view)
                facts_[sig] = {**p, "sig": sig, "said": old.get("said", []) + [(st["date"], st["rcept_no"])]}
        mine = checked.get(c["code"], {"사람": {}, "판독자": {}})
        man, bot = mine["사람"], mine["판독자"]
        rows = []
        for p in list(facts_.values()) + [{**p, "base": _base(p, said), "sig": policy_sig({**p, "base": _base(p, said)}), "said": [(p["date"], p["src"])]} for p in human.get(c["code"], [])]:
            if man.get(p["sig"], {}).get("verdict") == "틀림":
                continue
            reads = bot.get(p["sig"], [])   # 판독자는 공시마다 판정 — 한 공시라도 맞음·빠짐(판독자도 읽은 사실)이면 확인됨, 전부 틀림이어야 다름
            check = ("사람 확인" if p["sig"] in man or p["by"].startswith("사람") else "판독자 확인" if any(d["verdict"] in ("맞음", "빠짐") for d in reads)
                     else "판독자 다름" if reads else "자동")
            fix = next((d["value"] for d in reads if "value" in d), None) if check == "판독자 다름" else None
            rows.append({**p, "check": check, **({"fix": fix} if fix else {})})
        c["policies"] = sorted(rows, key=lambda p: (p["date"], p["src"]))
        have = {p["sig"] for p in rows} | set(man)
        c["suggested"] = list({sig: ds[0] for sig, ds in bot.items() if sig not in have and all(d["verdict"] == "빠짐" for d in ds)}.values())   # 판독자만 읽은 사실
        c["human_sigs"] = set(man)
        c["overrides"] = {(d["fy"], d["item"]): float(d["value"]) for d in man.values()
                          if d["verdict"] == "틀림" and "value" in d and not d["item"].startswith("회사 정책")}   # 사람이 고친 재료 값
    return out

def _years(raw):
    """사업연도별 정기보고서 배당 — 3분기보고서 '주당 현금배당금'이 작년 연간과 똑같으면 한 해 밀린 값(분기·중간배당 없는 회사의 보고서가
    전기 값을 당기 칸에 적음, 11종목 확인)이라 버림 — 남기면 결산 몫이 0·30원처럼 틀어짐"""
    ys = {int(fy): dict(d) for fy, d in raw.items()}
    for fy, d in ys.items():
        if "q3_dps" in d and d["q3_dps"] == ys.get(fy - 1, {}).get("dps"):
            del d["q3_dps"]
    return ys

RATIO_KINDS = ("payout", "return")   # 순이익에 곱하는 비율 — 연결·별도가 다른 정책(같은 공시에 "연결 30% 이상, 별도 22%")

def said_bases(rows):
    """회사가 비율마다 밝힌 기준 {kind: 연결·별도} — 오래된 것부터 받은 정책 줄에서 마지막으로 밝힌 것"""
    return {r["kind"]: r["base"] for r in rows if r.get("base") and r["kind"] in RATIO_KINDS}

def _base(p, said):
    """정책 기준 — 비율만: 문장에 밝힌 기준 > 그 회사가 같은 비율에 밝힌 기준(said_bases) > 연결. 금액(주당·총액·고배당 표)은 기준 없음"""
    return (p.get("base") or said.get(p["kind"]) or "연결") if p["kind"] in RATIO_KINDS else ""

def policy_sig(p):
    """정책 사실 열쇠 — 종류·칸·하한·상한·사업연도(출처 공시는 빼고: 여러 공시가 같은 말을 하면 한 사실). 값이 바뀌면 열쇠도 바뀌어 다시 확인 목록에"""
    return f"{p['kind']}|{p['slot']}|{float(p['lo']) if p.get('lo') is not None else None}|{float(p['hi']) if p.get('hi') is not None else None}|{p['fy'][0]}-{p['fy'][1]}|{p['base']}" + (f"|{p['on']}" if "on" in p else "")

def fact_of(r, date):
    """공시 원문의 정책 줄 → 정책 사실 (공시일 기준). 사업연도 채움(_fys), 기간 합은 해마다로(per 기간 → 합 ÷ 해 수),
    이미 배당을 정한 사업연도만 말하면 지난 실적이라 None — 공시 전 해는 1~3월(결산 배당 정하기 전)까지만 정책"""
    fy = [date.year, date.year + r["years"] - 1] if r.get("years") and not r["fy"][0] else _fys(r["fy"], date)   # "3년간 300억원" — 공시한 해부터
    if fy[1] < date.year - (1 if date.month <= 3 else 0):
        return None
    n = max(fy[1] - fy[0] + 1, 1) if r.get("per") == "기간" else 1
    return {**r, "fy": fy, "per": "해", "lo": r["lo"] / n if r.get("lo") else r.get("lo"), "hi": r["hi"] / n if r.get("hi") else r.get("hi"),
            **({"total": (r.get("lo"), r.get("hi"))} if n > 1 else {})}

def _fys(fy, date):
    """정책 줄의 사업연도 [첫, 끝] — 안 밝힌 첫 해는 공시한 해, 안 밝힌 끝은 첫 해부터 POLICY_YEARS 개"""
    a = fy[0] or date.year
    return [a, fy[1] or a + POLICY_YEARS - 1]

# ── 사실 조회 — 계산(amount·date·verify)은 장부·정책·시장 사실을 여기서만 읽는다 ──
def seen(ctx, fy, q):
    """사업연도 fy 의 q 분기 배당 결정 시기를 공시로 다 봤나 — 공시 수집 시작일(filed_since) 뒤면 회차가 없으면 정말 안 한 것"""
    return quarter_end(fy, q, ctx["acc"]) >= ctx["filed_since"]

def paid(ctx, fy):
    """이 사업연도에 이미 정한 결산 말고의 배당(주당) → (원, 쓴 원천) — 공시로 다 본 해는 배당결정 회차 합, 그 전 해는 3분기보고서 누적"""
    y = ctx["years"].get(fy, {})
    if "q3_eps" in y and not seen(ctx, fy, 1):
        return y.get("q3_dps", 0), f"{fy} 3분기보고서 누적 주당배당금 {y.get('q3_dps', 0):,.0f}원"
    rs = [r for r in ctx["rounds"] if r["fy"] == fy and r["type"] != "결산배당" and r["dps"]]
    return sum(int(r["dps"]) for r in rs), " + ".join(f"Q{r['q']} {int(r['dps']):,}원({iso(r['decided'])} 배당결정)" for r in rs) or "올해 정한 배당 없음"

def paid_gap(ctx, fy):
    """같은 사실 두 원천 대조 — 공시로 다 본 해의 배당결정 회차 합과 3분기보고서 누적 주당배당금이 다르면 (보고서, 회차 합), 아니면 None"""
    q3 = ctx["years"].get(fy, {}).get("q3_dps")
    got = paid(ctx, fy)[0] if seen(ctx, fy, 1) else None
    return (q3, got) if q3 is not None and got is not None and abs(q3 - got) >= 1 else None

def annual_dps(ctx, fy):
    """사업연도 연간 주당배당금 — 사업보고서, 없으면 공시로 다 본 해만 회차 합 (일부만 수집된 해의 합은 틀린 값이라 0)"""
    return ctx["years"].get(fy, {}).get("dps") or (sum(int(r["dps"]) for r in ctx["rounds"] if r["fy"] == fy and r["dps"]) if seen(ctx, fy, 1) else 0)

def last_annual(ctx, fy, rec):
    """작년 연간 주당배당금을 기준일 rec 주식 기준으로(그 사이 분할·병합 보정)"""
    return annual_dps(ctx, fy - 1) / share_factor(ctx["splits"], quarter_end(fy - 1, 4, ctx["acc"]), rec)

def final_of(ctx, fy):
    """사업연도 결산배당(주당) — 결산 공시 회차, 없으면 사업보고서 연간 − 3분기 누적.
    0 = 안 함(결산 결정 시기를 공시로 다 봤는데 회차 없음), None = 모름(보고서 전, 또는 보고서 배당금 칸이 비어 공시로도 못 봄)"""
    r = next((r for r in ctx["rounds"] if r["type"] == "결산배당" and r["fy"] == fy and r["dps"]), None)
    if r:
        return int(r["dps"])
    y = ctx["years"].get(fy, {})
    if y.get("dps") and "q3_eps" in y and y["dps"] > y.get("q3_dps", 0):
        return round(y["dps"] - y.get("q3_dps", 0))
    return 0 if "eps" in y and not y.get("dps") and seen(ctx, fy + 1, 1) else None   # 연간 배당이 있는데 결산 회차가 없으면(상장 전 결의 등) 모름

def shares(ctx, rec):
    """기준일 rec 의 배당 받는 주식 수(자사주 빠짐, 보통+종류) → (주, 쓴 원천) — 최근 배당결정(배당금총액 ÷ 주당)과 반기·사업보고서 유통주식 수 중 더 최근 것,
    그 뒤 분할·병합·주식배당 보정, rec 전에 기간이 끝나는 자기주식 취득(−)·처분(+) 예정 수량(다 사고판다고 봄)만큼 더함. 소각은 이미 빠진 주식이라 안 봄. 모르면 0"""
    last = next((r for r in reversed(ctx["rounds"]) if r["total"] and r["dps"] and int(r["dps"])), None)
    known = ([(last["record"], last["total"] / int(last["dps"]), f"{iso(last['decided'])} 배당결정 총액 {last['total'] / 1e8:,.0f}억 ÷ 주당 {int(last['dps']):,}원")] if last else [])         + [(d, n, f"{iso(d)} 기준 정기보고서 유통주식 수") for d, n in ctx["share_counts"].items()]
    if not known:
        return 0, ""
    d, n, src = max(known, key=lambda k: k[0])
    k = share_factor(ctx["splits"], d, rec)
    moves = [x for x in ctx["treasury"] if d < x["end"] <= rec]
    return (n * k + sum(x["delta"] * share_factor(ctx["splits"], x["end"], rec) for x in moves),
            f"{n / 1e8:,.2f}억주({src})" + (f" × 분할·병합 {k:g}" if k != 1 else "")
            + "".join(f" {'+' if x['delta'] > 0 else '−'} 자사주 {abs(x['delta']):,.0f}주({iso(x['date'])} {'처분' if x['delta'] > 0 else '취득'})" for x in moves))

def cash_share(ctx, fy):
    """총주주환원(배당 + 자사주) 중 현금배당 비율 → (비율, 근거). 한 공시 안에서 그해 현금·자사주(또는 총환원·자사주) 금액을 나눠 밝혔으면
    그 비율(가장 최근 공시, 자사주는 처음 나온 금액 — 뒤 금액은 그 안의 나눔일 수 있음), 아니면 최근 CASH_SHARE_YEARS 사업연도
    현금흐름표(배당금 지급 ÷ (배당금 지급 + 자기주식 취득)). 모르면 (None, "")"""
    by_src = {}
    for p in ctx["policies"]:
        if p["fy"][0] <= fy <= p["fy"][1] and p["kind"] in ("cash", "budget", "buyback") and p["slot"] == "연간" and p["lo"]:
            by_src.setdefault(p["src"], {}).setdefault(p["kind"], p)
    for g in sorted(by_src.values(), key=lambda g: max(p["date"] for p in g.values()), reverse=True):
        bb = g.get("buyback")
        if bb and g.get("cash"):
            return g["cash"]["lo"] / (g["cash"]["lo"] + bb["lo"]), f"{iso(bb['date'])} 회사가 밝힌 {fy} 현금 {g['cash']['lo'] / 1e8:,.0f}억·자사주 {bb['lo'] / 1e8:,.0f}억"
        if bb and g.get("budget") and g["budget"]["lo"] > bb["lo"]:
            b = g["budget"]["lo"]
            return (b - bb["lo"]) / b, f"{iso(bb['date'])} 회사가 밝힌 총환원 {b / 1e8:,.0f}억 중 자사주 {bb['lo'] / 1e8:,.0f}억"
    ys = [ctx["cash_flow"][y] for y in range(fy - CASH_SHARE_YEARS, fy) if "div_paid" in ctx["cash_flow"].get(y, {})]
    if not ys or not any("buyback" in y for y in ys):
        return None, ""   # 배당금 지급·자기주식 취득 계정이 빠진 해·회사(금융업 등)는 모름 — 0원으로 보면 현금 0%·100%로 틀림
    div, bb = sum(abs(y.get("div_paid", 0)) for y in ys), sum(abs(y.get("buyback", 0)) for y in ys)
    return (div / (div + bb), f"최근 {len(ys)}년 현금흐름표 배당 {div / 1e8:,.0f}억·자사주 {bb / 1e8:,.0f}억") if div + bb else (None, "")

def cons_of(ctx, fy, key):
    """사업연도 fy 의 최신 네이버 연간 컨센(key: dps·eps) — 네이버 연도 = 사업연도인 12월 결산만"""
    return next((x for x in reversed(ctx["cons"]) if x.get("fy") == str(fy) and x.get(key)), None) if int(ctx["acc"]) == 12 else None

def eps_of(ctx, fy):
    """사업연도 EPS — 실적 발표(네이버 연간 실제, 1~2월 잠정 실적 뒤), 없으면 연간 컨센, 없으면 3분기 누적 × 4/3 → (원, 근거)"""
    a, c, q3 = cons_of(ctx, fy, "eps_act"), cons_of(ctx, fy, "eps"), ctx["years"].get(fy, {}).get("q3_eps", 0)
    return ((a["eps_act"], f"{iso(a['date'])} 네이버 실적 발표 EPS {a['eps_act']:,}원") if a else (c["eps"], f"{iso(c['date'])} 네이버 컨센 EPS {c['eps']:,}원") if c
            else (q3 * 4 / 3, f"{fy} 3분기보고서 누적 EPS {q3:,.0f}원 × 4/3") if q3 > 0 else (0, ""))

def policy(ctx, fy):
    """사업연도 fy 에 걸린 정책 줄 {(kind, slot, base): 줄} — 같은 칸은 사람 줄이 이기고, 그다음 최근 공시(같은 날이면 접수번호 뒤 = 정정).
    상한만 있는 줄("최대 배당성향 40%", "총 주주환원 최대 5천억")은 수준이 아니라 칸을 다투지 않음 — 현금배당 총액만 상한이 범위로 쓰임, 고배당 표는 숫자 없음"""
    rows = sorted((p for p in ctx["policies"] if p["fy"][0] <= fy <= p["fy"][1] and (p["kind"] in ("cash", "high_div") or p.get("lo") is not None)),
                  key=lambda p: (p["by"].startswith("사람"), p["date"], p["src"]))
    return {(p["kind"], p["slot"], p["base"]): p for p in rows}

def evidence(ctx, fy):
    """이 사업연도에 결산배당이 있다고 볼 근거(문장) — 작년 결산배당, 회사 정책, 연간 컨센 DPS. 없으면 """""
    last, pol, c = final_of(ctx, fy - 1), policy(ctx, fy), cons_of(ctx, fy, "dps")
    p = max(pol.values(), key=lambda p: p["date"]) if pol else None
    po = ctx["years"].get(fy - 1, {}).get("payout", 0)
    return (f"작년({fy - 1}) 결산배당 {last:,}원" if last else f"작년({fy - 1}) 배당함(연간 {annual_dps(ctx, fy - 1):,.0f}원 · 배당성향 {po:g}%)"
            if last is None and (annual_dps(ctx, fy - 1) or po > 0)
            else f"{iso(p['date'])} {p['title']}" if p else f"{iso(c['date'])} 연간 컨센 DPS {c['dps']:,}원" if c else "")
