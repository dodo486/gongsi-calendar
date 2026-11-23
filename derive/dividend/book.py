# -*- coding: utf-8 -*-
"""배당 장부 — 확정(dividends.json) + 예상(div_estimates.json)을 회차마다 한 줄로 합친다 → data/out/div_book.json

화면·엑셀·지수배당포인트는 이 장부만 읽는다(구분·날짜·만기·금액·근거·캘린더 표시가 다 들어 있음).
같은 종목·배당종류의 예상은 확정 기준일 ±45일 안이면 같은 회차로 보고 버린다. 기준일 공고만 있고 금액이 없으면 예상 금액을 빌린다.
참고 공시(배당 산출이 읽은 공시와 읽어 낸 내용) → data/out/div_sources.json

  python -m derive.dividend.book
"""
import datetime
from core.paths import save_json, load_json, stamp, DIV_BOOK_PATH, DIV_SOURCES_PATH, DIVIDENDS_PATH, DIV_ESTIMATES_PATH, EXPIRIES_PATH
from derive.dividend.date import GRADES, TAGS, END_WORD
from core import store
from core.paths import DART_VIEW
from core.krx_cal import iso, ymd, days_between

SAME_ROUND_DAYS = 45


def _md(d):
    return f"{int(d[5:7])}/{int(d[8:10])}" if d else ""

def _won(s):
    return f"{int(float(s)):,}"

def _label(key, t):
    return GRADES[key]["label"].format(end=END_WORD.get(t, "기말"))


def build():
    today = datetime.date.today().isoformat()
    conf = load_json(DIVIDENDS_PATH, {}).get("events", [])
    pred = load_json(DIV_ESTIMATES_PATH, {}).get("events", [])
    exps = sorted(e["date"] for e in load_json(EXPIRIES_PATH, {}).get("events", []))

    rows = []
    for e in conf:
        if not e.get("record_date"):
            continue
        dt = e.get("rcept_dt", "")
        rows.append({**e, "kind": "div", "grade": "확정" if e.get("per_share") else "공고",
                     "amount_src": "공시" if e.get("per_share") else "",
                     "amount_basis": "공시 금액" if e.get("per_share") else "",
                     "basis_text": f"DART 공시 {iso(dt)} {e.get('url', '')}".strip() if dt else e.get("url", "")})
    for p in pred:
        if not p.get("record_date"):
            continue
        same = [c for c in rows if c["code"] == p["code"] and (c.get("div_type") or "") in ("", p.get("div_type") or "")
                and days_between(c["record_date"], p["record_date"]) <= SAME_ROUND_DAYS]
        if same:
            for c in same:
                if not c.get("per_share") and p.get("per_share"):
                    c.update(per_share=p["per_share"], amount_src="예상", amount_basis=p.get("amount_basis", ""), amount_cands=p["amount_cands"])
            continue
        rows.append({**p, "kind": "est", "amount_src": "예상" if p.get("per_share") else "", "url": "",
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

    nxt = next((d for d in exps if d > today), "")
    payload = {"generated_at": stamp(), "today": today,
               "tags": TAGS,
               "grades": {k: {**g, **{f: g[f].format(end="기말") for f in ("label", "desc", "rule")}} for k, g in GRADES.items()}, "next_expiry": nxt,
               "today_ids": [r["id"] for r in rows if r["kind"] == "div" and r.get("rcept_dt") == ymd(today)],
               "upcoming_ids": [r["id"] for r in rows if r["kind"] == "div" and today < r["ex_date"] and (not nxt or r["ex_date"] <= nxt)],
               "count": {"div": sum(r["kind"] == "div" for r in rows), "est": sum(r["kind"] == "est" for r in rows)},
               "rows": rows}
    save_json(DIV_BOOK_PATH, payload)
    build_sources({r["code"] for r in rows})
    return payload


KIND = {"decision": "현금배당결정", "record": "배당 기준일(주주명부폐쇄) 공고", "notice": "기타경영사항(자율공시)",
        "agm_call": "주주총회소집결의", "agm_result": "주주총회결과", "plan": "기업가치제고계획·지배구조보고서",
        "split": "주식분할·병합결정"}

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
    "plan": lambda h: " ".join(h.get("lines") or [])[:500],
    "split": lambda h: f"1주 → {h['factor']:g}주 · 효력발생일 {h['effective']}" if h.get("factor") else "비율 못 읽음",
}

def build_sources(stocks):
    """엑셀 참고 공시 탭 — 장부 종목에 대해 배당 산출이 읽은 DART 공시(2년치)와 정기보고서 배당 표"""
    hist = {no: h for k in KIND for no, h in store.load(k).items()}
    companies = load_json(DIV_ESTIMATES_PATH, {}).get("companies", {})
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
