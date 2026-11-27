# -*- coding: utf-8 -*-
"""배당 사실 — 저장소 → 종목별 사실(공시 회차·분할·정관 이력·주총·예고·실적발표 예정·회사 말·사업연도별 EPS·배당). 공시 원문 그대로, 사람 판정·추정 없음"""
import datetime, calendar, statistics
from core import store, universe
from core.krx_cal import to_date, iso, ymd, days_ago, days_between
from derive import earn_sched

# ── 회차 — 배당 하나 = 종목 · 배당종류 · 사업연도 · 분기 ──
TYPE_QUARTERS = {"분기배당": (1, 2, 3), "중간배당": (2,), "결산배당": (4,)}
GROUP = {"결산배당": "결산", "중간배당": "분기중간", "분기배당": "분기중간"}   # 정관 기준일 표의 칸
SEASON_DAYS = {"결산배당": 123, "중간배당": 92, "분기배당": 92}   # 결산기말·분기말 뒤 이만큼 안에 그 회차 공시가 나옴

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
    cons, alot, counts, xbrl = store.load("consensus"), store.load("alot"), store.load("shares"), store.load("xbrl")
    earn = {}
    for e in earn_sched.collect(watch=stocks):
        earn.setdefault(e["code"], []).append(e)
    since = to_date(min((e["rcept_dt"] for e in store.load("decision").values()), default="99991231"))
    return [{**context(s, by.get(s, []), pol.get(s), acc.get(s), agm_records.get(s)), "filed_since": since,
             "cons": [{**x, "date": to_date(x["date"])} for x in cons.get(s, [])],
             "years": _years(alot.get(s, {}).get("years", {})),
             "years_known": {int(fy): {k: to_date(dt) for k, dt in d.items()} for fy, d in alot.get(s, {}).get("known", {}).items()},
             "share_counts": {to_date(d): {"n": x["n"], "dt": to_date(x["dt"])} for d, x in counts.get(s, {}).get("dates", {}).items()},
             "earn": earn.get(s, []),
             "fin_reports": sorted(({"dt": to_date(r["dt"]), "end": r["end"], "facts": r["facts"]} for r in xbrl.get(s, {}).get("reports", {}).values() if r["end"]),
                               key=lambda r: r["dt"])} for s in sorted(stocks)]

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

def policy_text(rows):
    """정책 줄 → 사람이 읽는 한 줄 (재료표·확인 목록·참고 공시 탭·화면)"""
    unit = {"dps": "원", "cash": "원", "budget": "원", "buyback": "원", "payout": "%", "return": "%"}
    name = {"dps": "주당", "cash": "현금배당 총액", "budget": "총주주환원(자사주 포함)", "buyback": "자사주 매입",
            "payout": "배당성향", "return": "총주주환원율(자사주 포함)"}
    out = []
    for r in rows or []:
        if r["kind"] == "high_div":
            out.append(f"고배당기업 {'해당' if r['on'] else '미해당'}")
            continue
        v = lambda x: (f"{x:,g}" if unit[r["kind"]] == "%" or r["kind"] == "dps" else f"{x / 1e12:,g}조" if x >= 1e12 else f"{x / 1e8:,.0f}억") + unit[r["kind"]]
        rng = (f"{v(r['lo'])}" if r["lo"] == r["hi"] else f"{v(r['lo'])} 이상" if r["hi"] is None else f"{v(r['hi'])} 이하" if r["lo"] is None
               else f"{v(r['lo'])}~{v(r['hi'])}")
        fy = r["fy"] if isinstance(r["fy"], list) else [None, None]
        total = r.get("total") and f" ({fy[1] - fy[0] + 1}년 합 {v(r['total'][0] or r['total'][1])} ÷ {fy[1] - fy[0] + 1})"
        out.append(f"{'' if r['slot'] == '연간' else r['slot'] + ' '}{name[r['kind']]} {rng}{total or ''}"
                   + ("" if not any(fy) else f" ({fy[0]})" if fy[0] == fy[1] else f" ({fy[0] or ''}~{fy[1] or ''})"))
    return " · ".join(out)

# ── 사실 조회 — 재료(inputs)는 장부·시장 사실을 여기서만 읽는다 ──
OWNER_NI, NI = "ifrs-full:ProfitLossAttributableToOwnersOfParent", "ifrs-full:ProfitLoss"

def fin(ctx, concept, basis, start, end):
    """정기보고서 숫자 — (계정, 연결·별도, 시작, 끝)이 그 보고서 기간인 공시 중 가장 최근 값, 없으면 None. 시점 값(재무상태표)은 시작 ""
    기간은 원본(XBRL) 그대로라 사업연도를 맞춰 만들지 않음 — 결산월로 찾은 기간이 원본에 없으면 None(모름)"""
    key = [concept, basis, start, end]
    return next((f[4] for r in reversed(ctx["fin_reports"]) for f in r["facts"] if f[:4] == key), None)

def ytd(ctx, concept, basis, fy, q):
    """사업연도 fy 의 q 분기말까지 누적 (흐름 값: 손익·현금흐름). q=4 = 연간"""
    return fin(ctx, concept, basis, iso(quarter_end(fy - 1, 4, ctx["acc"]) + datetime.timedelta(days=1)), iso(quarter_end(fy, q, ctx["acc"])))

def quarter_only(ctx, concept, basis, fy, q):
    """그 분기 3개월 = 누적 − 직전 분기 누적 (한 곳에서만 뺌)"""
    now, before = ytd(ctx, concept, basis, fy, q), ytd(ctx, concept, basis, fy, q - 1) if q > 1 else 0
    return None if now is None or before is None else now - before

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
    known = ([(last["record"], last["total"] / int(last["dps"]), f"{iso(last['decided'])} 배당결정 총액 {last['total'] / 1e8:,.0f}억 ÷ 주당 {int(last['dps']):,}원")] if last else [])         + [(d, x["n"], f"{iso(d)} 기준 정기보고서 유통주식 수") for d, x in ctx["share_counts"].items()]
    if not known:
        return 0, ""
    d, n, src = max(known, key=lambda k: k[0])
    k = share_factor(ctx["splits"], d, rec)
    moves = [x for x in ctx["treasury"] if d < x["end"] <= rec]
    return (n * k + sum(x["delta"] * share_factor(ctx["splits"], x["end"], rec) for x in moves),
            f"{n / 1e8:,.2f}억주({src})" + (f" × 분할·병합 {k:g}" if k != 1 else "")
            + "".join(f" {'+' if x['delta'] > 0 else '−'} 자사주 {abs(x['delta']):,.0f}주({iso(x['date'])} {'처분' if x['delta'] > 0 else '취득'})" for x in moves))

def cons_of(ctx, fy, key):
    """사업연도 fy 의 최신 네이버 연간 컨센(key: dps·eps) — 네이버 연도 = 사업연도인 12월 결산만"""
    return next((x for x in reversed(ctx["cons"]) if x.get("fy") == str(fy) and x.get(key)), None) if int(ctx["acc"]) == 12 else None

def owner_ni(ctx, fy, q):
    """지배 순이익 누적(사업연도 fy, q 분기말까지) → (원, 쓴 원천). 연결 지배, 연결이 없는 회사는 별도 순이익,
    재무제표 원본이 없으면(금융업 2023년 3분기 전) 3분기만 배당 표 누적 순이익. 모르면 (None, "")"""
    x = ytd(ctx, OWNER_NI, "연결", fy, q)
    if x is not None:
        return x, f"{fy} {q}분기 누적 연결 지배순이익"
    x = ytd(ctx, NI, "별도", fy, q) if ytd(ctx, NI, "연결", fy, q) is None else None
    if x is not None:
        return x, f"{fy} {q}분기 누적 별도 순이익(연결 없음)"
    y = ctx["years"].get(fy, {})
    return (y["q3_ni"] * 1e6, f"{fy} 3분기보고서 배당 표 누적 순이익") if q == 3 and "q3_ni" in y else (None, "")

def quarter_cons(ctx, fy, q):
    """그 분기 지배 순이익 네이버 컨센(원) — 분기 끝 달로 찾음, 없으면 None"""
    key = quarter_end(fy, q, ctx["acc"]).strftime("%Y%m")
    return next((x["ni_own"] * 1e8 for x in reversed(ctx["cons"]) if x.get("quarter") == key and "ni_own" in x), None)

def eps_of(ctx, fy, sh):
    """사업연도 EPS(원) → (값, 근거). 잠정 실적 뒤 실제(네이버) > 지배순이익 누적 실제 + 남은 분기 컨센 ÷ 주식 수 > 네이버 연간 컨센 EPS
    > 3분기 누적 지배순이익 × 4/3 ÷ 주식 수 (주식 수 모르면 배당 표 3분기 EPS × 4/3). EPS 는 지배순이익 ÷ 배당 받는 주식 수로 우리가 계산 — 원천마다 EPS 기준(연결·별도·주식 종류)이 달라서"""
    a, c = cons_of(ctx, fy, "eps_act"), cons_of(ctx, fy, "eps")
    if a:
        return a["eps_act"], f"{iso(a['date'])} 네이버 실적 발표 EPS {a['eps_act']:,}원"
    done = next((q for q in (3, 2, 1) if ytd(ctx, OWNER_NI, "연결", fy, q) is not None or ytd(ctx, NI, "별도", fy, q) is not None), 0)
    rest = [quarter_cons(ctx, fy, q) for q in range(done + 1, 5)]
    if done and sh and None not in rest:
        ni, src = owner_ni(ctx, fy, done)
        return ((ni + sum(rest)) / sh, f"{src} {ni / 1e8:,.0f}억 + 남은 {4 - done}개 분기 컨센 {sum(rest) / 1e8:,.0f}억 ÷ {sh / 1e8:,.2f}억주")
    if c:
        return c["eps"], f"{iso(c['date'])} 네이버 컨센 EPS {c['eps']:,}원"
    ni, src = owner_ni(ctx, fy, 3)
    if ni and ni > 0 and sh:
        return ni * 4 / 3 / sh, f"{src} {ni / 1e8:,.0f}억 × 4/3 ÷ {sh / 1e8:,.2f}억주"
    q3 = ctx["years"].get(fy, {}).get("q3_eps", 0)   # 주식 수를 모르는 과거 시점
    return (q3 * 4 / 3, f"{fy} 3분기보고서 배당 표 누적 EPS {q3:,.0f}원 × 4/3") if q3 > 0 else (0, "")
