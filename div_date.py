# -*- coding: utf-8 -*-
"""배당 3단계 날짜 — 사실 → 회차별 구분(GRADES)과 예상 기준일. 금액은 div_amount. 설계 정본 docs/배당예측-워크플로우.md §4

  python div_date.py   → data/div_estimates.json (companies + events)
"""
import os, datetime
from base import DATA_DIR, save_json
import dividends as dv
from dividends import quarter_end
from krx_cal import t_minus
import earn_sched
import div_amount
from div_collect import D, _d, _iso, DART, LOCK, HIST_DAYS
from div_facts import view, quarter_owner, pattern, medians, contexts

OUT_PATH = os.path.join(DATA_DIR, "div_estimates.json")
HORIZON_DAYS = 270
MISS_GRACE_DAYS = 3
NOTICE_MIN_DAYS = 14
PAY_LIMIT_DAYS = 92
APPROVAL_PAY_DAYS = 31
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

    return {"div_type": t, "fy": fy, "q": q, "quarter": f"{fy} Q{q}", "quarter_end": _iso(qe),
            "record_date": _iso(rec), "pay_date": _iso(pay), "method": "확정 기준일" if ann_rec else method,
            **grade_out("공고" if ann_rec else grade, t), "case": j["case"], "why": why, "anchor": anchor, "reason": reason,
            "alt": {k: _iso(v) for k, v in alt.items()}, "warn": warn,
            "cond": cond_signals(ctx, qe, today) if t == "결산배당" else {},
            **div_amount.estimate(ctx, t, rec),
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

def build(days=HIST_DAYS):
    today = D.today()
    ctxs = contexts()
    try:
        earn_all = earn_sched.collect(watch=dv.div_universe())
    except Exception as ex:
        print(f"  [배당 날짜] 실적발표 예정일 수집 실패 — A2 이사회형 앵커 없이 추정: {ex}")
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
    with LOCK:
        save_json(OUT_PATH, payload)
    n = lambda m: sum(1 for e in events if e["method"] == m)
    print(f"저장: div_estimates.json — 종목 {len(companies)} · 예상 {len(events)}건 (고정 {n('고정')} · 유동 {n('유동')} · 기준일공고 {n('확정 기준일')})")
    return payload



if __name__ == "__main__":
    build()
