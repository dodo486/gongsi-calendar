# -*- coding: utf-8 -*-
"""배당 장부 — 재료(inputs) → 공시 회차 + 예상 회차(predict)를 회차마다 한 줄로 → data/out/div_book.json

화면·엑셀·지수배당포인트·점검은 이 장부만 읽는다 — companies(회사 판정·정관·회차 이력), rows(회차별 구분·날짜·만기·금액·근거·캘린더 표시).
기준일 공고만 있고 금액이 없는 공시 회차는 그 회차의 예상 금액을 붙인다.
참고 공시(배당 산출이 읽은 공시와 읽어 낸 내용) → data/out/div_sources.json

  python -m derive.dividend.book
"""
import datetime
from core import store
from core.paths import save_json, load_json, stamp, DIV_BOOK_PATH, DIV_SOURCES_PATH, EXPIRIES_PATH, DART_VIEW
from core.krx_cal import iso, ymd, days_ago, t_minus
from derive.dividend.facts import quarter_owner, pattern, paid_gap, cons_of, policy_text
from derive.dividend.inputs import contexts, view, for_date
from derive.dividend import predict
from derive.dividend.date import GRADES, TAGS, END_WORD, FLOAT_FROM, judge, ladder, grade_out
from derive.dividend.amount import AMOUNT_GRADES

FILED_DAYS = 90   # 공시 회차는 최근 접수분만 (지난 회차는 companies 이력)


def _md(d):
    return f"{int(d[5:7])}/{int(d[8:10])}" if d else ""

def _won(s):
    return f"{int(float(s)):,}"

def _label(key, t):
    return GRADES[key]["label"].format(end=END_WORD.get(t, "기말"))

def company_out(ctx, missed, today):
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
        j = judge(for_date(ctx, t, fy, q)["v"])
        judgments[t] = {"method": j["method"], "why": j["why"], **grade_out(j["grade"], t), "case": j["case"], "quarter": f"{fy} Q{q}"}
    ladders = {f"{r['type']}|{iso(r['record'])}": ladder(for_date(view(ctx, r["decided"] or r["record"]), r["type"], r["fy"], r["q"])["v"], True)
               for r in ctx["rounds"][-12:]}
    return {"name": ctx["name"], "market": ctx["market"], "acc_mt": ctx["acc"], "acc_known": ctx["acc_known"],
            "charter": {g: {"v": c["v"], "src": c["src"], "date": iso(c["date"])} for g, c in ctx["charter"].items()},
            "charter_pending": ctx["charter_pending"], "organ": ctx["organ"], "policy_report": ctx["policy_report"],
            "pattern": pattern(owner) if owner else "", "judgments": judgments, "ladders": ladders,
            "rounds": [{"quarter": f"{r['fy']} Q{r['q']}", "div_type": r["type"], "decided": iso(r["decided"]),
                        "record_date": iso(r["record"]), "quarter_end": iso(r["qe"]), "fixed": r["fixed"],
                        "amount_first": r["amount_first"], "per_share": r["dps"], "special": r["special"], "src": r["src"],
                        "note": r["note"]}
                       for r in reversed(ctx["rounds"])],
            "notices": [{"date": iso(n["date"]), "div_type": n["div_type"], "quarter": f"{n['fy']} Q{n['q']}",
                         "hint": n["hint"], "title": n["title"]} for n in ctx["notices"]][-4:],
            "agms": [{"date": iso(a["date"]), "kind": a["kind"], "regular": a["regular"], "charter": a["charter"]}
                     for a in ctx["agms"]][-4:],
            "statements": [{**x, "date": iso(x["date"]), "read": policy_text(x["rows"])} for x in ctx["statements"] if x["date"] >= today - datetime.timedelta(days=365)],
            "policies": [{**p, "date": iso(p["date"]), "said": [[iso(d), no] for d, no in p["said"]]} for p in ctx["policies"] if p["fy"][1] >= today.year - 1],
            "reports": {fy: {k: d[k] for k in ("dps", "q3_dps") if k in d} for fy, d in ctx["years"].items() if fy >= today.year - 1},
            "paid_gaps": {fy: list(g) for fy in (today.year - 1, today.year) if (g := paid_gap(ctx, fy))},   # 3분기보고서 누적 ≠ 배당결정 회차 합
            "cons_dps": {fy: c["dps"] for fy in (today.year, today.year + 1) if (c := cons_of(ctx, fy, "dps"))},
            "exdivs": [{**x, "date": iso(x["date"])} for x in ctx["exdivs"]][-6:],
            "suggested": [{**s, "text": policy_text([s["value"]])} for s in ctx["suggested"]],
            "evidence": ev}


def build():
    today = datetime.date.today()
    since = ymd(days_ago(FILED_DAYS))
    exps = sorted(e["date"] for e in load_json(EXPIRIES_PATH, {}).get("events", []))
    ctxs = contexts()
    med = predict.learn(ctxs, today)

    companies, rows = {}, []
    for c in ctxs:
        if not c["name"] and not c["charter"]:
            continue
        preds, missed = predict.upcoming(c, today, med)
        companies[c["code"]] = company_out(c, missed, today)
        noticed = {p["record_date"]: p for p in preds if p["grade"] == "공고"}
        for f in c["filings"]:
            if f["rcept_dt"] < since:
                continue
            url = DART_VIEW + f["rcept_no"]
            r = {**{k: v for k, v in f.items() if k != "decided"}, "url": url, "kind": "div",
                 "confirm_date": t_minus(f["record_date"], 2), "ex_date": t_minus(f["record_date"], 1),
                 "basis_text": f"DART 공시 {iso(f['rcept_dt'])} {url}"}
            p = noticed.get(f["record_date"])
            if f["per_share"]:
                r.update(grade="확정", amount_src="공시", amount_basis="공시 금액", amount_grade="공시")
            elif p and p["per_share"]:
                r.update(grade="공고", per_share=p["per_share"], amount_src="예상", amount_basis=p["amount_basis"], amount_cands=p["amount_cands"], amount_special=p["amount_special"],
                         amount_surprise=p["amount_surprise"], amount_grade=p["amount_grade"])
            else:
                r.update(grade="공고", amount_src="", amount_basis="", amount_grade="없음")
            rows.append(r)
        for p in preds:
            if p["grade"] == "공고":
                continue
            rows.append({"name": c["name"], "code": c["code"], "market": c["market"], **p, "kind": "est", "url": "",
                         "confirm_date": t_minus(p["record_date"], 2), "ex_date": t_minus(p["record_date"], 1),
                         "amount_src": "예상" if p["per_share"] else "",
                         "basis": f"{p['quarter']} {p['div_type']} · {p['method']} — {p['why']} · {p['anchor']} · 금액 {p['amount_basis']}",
                         "headline": f"예상 기준일 {_md(p['record_date'])} — {p['reason']}",
                         "basis_text": f"예상 기준일 {_md(p['record_date'])} — {p['reason']} / {p['why']} / 날짜 근거: {p['anchor']}"})

    for r in rows:
        t, g = r.get("div_type", ""), GRADES[r["grade"]]
        r["id"] = f"{r['code']}|{t}|{r['record_date']}"
        r["grade_label"] = _label(r["grade"], t)
        r["rank"] = g["rank"]
        after = [d for d in exps if d <= r["confirm_date"]]
        month = [d for d in exps if d >= r["ex_date"]]
        r["after_expiry"] = f"{int(after[-1][5:7])}월 만기 후" if after else ""
        r["ex_month"] = f"{month[0][:7]}물" if month else ""
        amt = r.get("per_share")
        if r["kind"] == "est":
            money = f"(예상 {_won(amt)}원)" if amt else "(예상 미정)"
            names = {"buy": "💡[예상 배당매수일]", "ex": "💡[예상 배당락]"}
        else:
            money = f"({'예상 ' if r['amount_src'] == '예상' else ''}{_won(amt)}원)" if amt else "(금액 미정)"
            names = {"buy": "[배당매수일]", "ex": "[배당권리락]"}
        r["marks"] = [{"type": k, "date": r["confirm_date" if k == "buy" else "ex_date"], "group": n,
                       "title": f"{n} {r['name']} {money} {r['grade_label']}"} for k, n in names.items()]
    rows.sort(key=lambda r: (r["ex_date"], r["name"]))

    today = today.isoformat()
    nxt = next((d for d in exps if d > today), "")
    payload = {"generated_at": stamp(), "today": today,
               "tags": TAGS, "amount_grades": AMOUNT_GRADES,
               "grades": {k: {**g, **{f: g[f].format(end="기말") for f in ("label", "desc", "rule")}} for k, g in GRADES.items()}, "next_expiry": nxt,
               "today_ids": [r["id"] for r in rows if r["kind"] == "div" and r.get("rcept_dt") == ymd(today)],
               "upcoming_ids": [r["id"] for r in rows if r["kind"] == "div" and today < r["ex_date"] and (not nxt or r["ex_date"] <= nxt)],
               "count": {"div": sum(r["kind"] == "div" for r in rows), "est": sum(r["kind"] == "est" for r in rows)},
               "companies": companies, "rows": rows}
    save_json(DIV_BOOK_PATH, payload)
    build_sources({r["code"] for r in rows}, companies)
    return payload


KIND = {"decision": "현금배당결정", "record": "배당 기준일(주주명부폐쇄) 공고", "notice": "기타경영사항(자율공시)",
        "agm_call": "주주총회소집결의", "agm_result": "주주총회결과", "plan": "기업가치제고계획·지배구조보고서",
        "return_plan": "주주환원 공정공시", "split": "주식분할·병합결정"}

def _ox(c):
    return ", ".join(f"{k} {'이사회가 정할 수 있음' if v == 'O' else '결산기말 고정'}" for k, v in (c or {}).items())

READ = {
    "decision": lambda h: f"{h.get('div_type', '')} 주당 {h.get('per_share', '')}원 · 기준일 {h.get('record_date', '')}",
    "record": lambda h: f"{h.get('div_type', '')} 기준일 {h.get('record_date', '')}",
    "notice": lambda h: (f"{h.get('div_type', '')} 기준일 안내" + (f" · {', '.join(h['months'])}" if h.get("months") else "")
                         + (f" · {h['fyq']}" if h.get("fyq") else "")) if h.get("is_div") else "배당 기준일 안내 아님",
    "agm_call": lambda h: " · ".join(x for x in [f"{'정기' if h.get('regular') else '임시'}주총 {h.get('agm_date', '')}",
                                                  h.get("charter") and f"정관: {_ox(h['charter'])}"] if x),
    "agm_result": lambda h: " · ".join(x for x in [f"{'정기' if h.get('regular') else '임시'}주총 {h.get('agm_date', '')}",
                                                    h.get("rejected") and "부결", h.get("charter") and f"정관: {_ox(h['charter'])}"] if x),
    "plan": lambda h: policy_text(h.get("rows")) or " ".join(h.get("lines") or [])[:500],
    "return_plan": lambda h: policy_text(h.get("rows")) or "배당 숫자 못 읽음",
    "split": lambda h: f"1주 → {h['factor']:g}주 · 효력발생일 {h['effective']}" if h.get("factor") else "비율 못 읽음",
}

def build_sources(stocks, companies):
    """엑셀 참고 공시 탭 — 장부 종목에 대해 배당 산출이 읽은 DART 공시(2년치)와 정기보고서 배당 표"""
    hist = {no: h for k in KIND for no, h in store.load(k).items()}
    rows = [{"code": h["code"], "종목코드": "A" + h["code"], "종목명": h.get("name", ""), "공시일": iso(h["rcept_dt"]),
             "공시 종류": KIND[h["kind"]], "공시 제목": h.get("title") or KIND[h["kind"]], "읽은 내용": READ[h["kind"]](h), "DART": DART_VIEW + no}
            for no, h in hist.items() if h.get("code") in stocks and h.get("kind") in KIND]
    for st in stocks:
        c = companies.get(st) or {}
        if not c.get("policy_report"):
            continue
        read = [c.get("organ") and "배당액 결정기관 " + ", ".join(f"{k} {v}" for k, v in c["organ"].items()),
                c.get("charter") and "정관: " + ", ".join(f"{k} {'이사회가 정할 수 있음' if v['v'] == 'O' else '결산기말 고정'}" for k, v in c["charter"].items())]
        rows.append({"code": st, "종목코드": "A" + st, "종목명": c.get("name", ""), "공시일": "", "공시 종류": "정기보고서 배당 표",
                     "공시 제목": c["policy_report"], "읽은 내용": " · ".join(x for x in read if x), "DART": ""})
    by = {}
    for r in rows:
        by.setdefault(r["종목명"], []).append(r)
    rows = [r for nm in sorted(by) for r in sorted(by[nm], key=lambda r: r["공시일"], reverse=True)]
    save_json(DIV_SOURCES_PATH, {"generated_at": stamp(), "rows": rows})


if __name__ == "__main__":
    p = build()
    print(f"저장: div_book.json — 확정 {p['count']['div']} · 예상 {p['count']['est']} · 다음 만기 {p['next_expiry']}")
