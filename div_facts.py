# -*- coding: utf-8 -*-
"""배당 2단계 사실 — 저장 파일 → 종목별 사실(회차·분할·정관 이력·주총·예고·계획). view(ctx, cut) = cut 전까지 알던 것만. 추정 없음"""
import datetime, statistics
from dividends import TYPE_QUARTERS, quarter_end, round_of
import dividends as dv
from div_collect import _d, _norm, _iso, _load, collect, policies, acc_months
import naver_dps

NOTICE_LOOKBACK_DAYS = 45
SPECIAL_MULT = 2
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
    v["dps_cons"] = [x for x in ctx["dps_cons"] if x["date"] < cut]
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

def contexts():
    universe = dv.div_universe()
    hist, agm_records = collect(universe)
    pol = policies(universe)
    acc = acc_months(universe)
    by = {}
    for no, e in hist.items():
        if e.get("stock") in universe:
            by.setdefault(e["stock"], []).append({**e, "rcept_no": no})
    cons = _load(naver_dps.PATH, {})
    return [{**context(s, by.get(s, []), pol.get(s), acc.get(s), agm_records.get(s)),
             "dps_cons": [{**x, "date": _d(x["date"])} for x in cons.get(s, [])]} for s in sorted(universe)]

