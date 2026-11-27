# -*- coding: utf-8 -*-
"""배당 재료 — 사실(facts) + 사람·판독자 판정 → 날짜(date)·금액(amount)이 읽는 재료. 날짜·금액은 이 재료만 읽고, 사람·하네스는 같은 표로 확인한다.

contexts() → 종목마다 사실 + 정책 목록(사람 판정 반영). view(ctx, cut) = cut 전까지 알던 것만
samples(ctxs) → 수집대상 전체 표본 — date.learn·amount.learn 이 통계로 바꿈
for_date(ctx, t, fy, q) · for_amount(ctx, fy, rec) → {"v": 계산용 값, "table": 사람용 줄}
  table 한 줄 = {item 재료 이름, value 값 글, status 있음·없음·모름·의심·안 함, check 자동·판독자 확인·판독자 다름·사람 확인, src 출처, sig 판정 열쇠}
  금액 재료가 있음이 아니거나 회사 정책이 아직 사람 확인 전이면 확인 목록(review)에 오른다."""
import datetime
from core.paths import load_json, DIV_POLICY_PATH, DIV_CHECKED_PATH, DART_VIEW
from core.krx_cal import to_date, iso
from derive.dividend import facts
from derive.dividend.facts import (GROUP, SEASON_DAYS, NI, share_factor, paid, annual_dps, final_of, last_annual, shares, cons_of, eps_of, owner_ni,
                                   quarter_end, year_steps, paid_gap, said_bases, _base, policy_sig, fact_of, policy_text, ytd)

CASH_SHARE_YEARS = 3   # 총환원 중 현금배당 비율 = 최근 이만큼 사업연도 현금흐름표 배당금 지급 ÷ (배당금 지급 + 자기주식 취득)
DIV_PAID, BUYBACK = "ifrs-full:DividendsPaidClassifiedAsFinancingActivities", "dart:AcquisitionOfTreasuryShares"

OVERRIDE = {"올해 순이익(EPS)": ("eps",), "작년 연간 DPS": ("prev", "prev_adj"), "작년 결산 몫": ("last_final",), "올해 이미 정한 배당": ("paid",),
            "주식 수": ("shares",), "연간 컨센 DPS": ("cons_dps",), "현금 몫(총환원 중 배당)": ("share",)}   # 사람이 고칠 수 있는 재료 → 계산용 값 칸


def contexts():
    """사실 + 판정. 정책 목록 = 정책 사실(같은 내용은 여러 공시가 말해도 한 줄, 출처 said) − 사람이 틀림 판정한 사실 + 사람 줄"""
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
    out = facts.contexts()
    for c in out:
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
    v["earn"] = [e for e in ctx["earn"] if to_date(e["rcept_dt"]) < cut]
    v["fin_reports"] = [r for r in ctx["fin_reports"] if r["dt"] < cut]
    v["statements"] = [p for p in ctx["statements"] if p["date"] < cut]
    v["policies"] = [{**p, "date": s[-1][0], "src": s[-1][1]} for p in ctx["policies"] if (s := [x for x in p["said"] if x[0] < cut])]   # 그때까지 마지막으로 말한 공시가 대표
    v["share_counts"] = {d: x for d, x in ctx["share_counts"].items() if x["dt"] < cut}
    v["years"] = {}
    for fy, d in ctx["years"].items():
        for k, x in d.items():
            if ctx["years_known"].get(fy, {}).get(k, datetime.date.max) < cut:
                v["years"].setdefault(fy, {})[k] = x
    return v

def samples(ctxs):
    """수집대상 전체 표본 — 이사회가 정한 회차의 날짜 간격(qe 기말→기준일 · dec 배당결정→기준일 · agm 주총→기준일, 종류별 일수)과 pay 기준일→지급,
    growth (사업연도, 올해·작년 3분기 누적 지배순이익, 올해·작년 결산 몫), steps 분기배당 새해 인상 (사업연도, 비율)"""
    s = {"qe": {}, "dec": {}, "pay": {}, "agm": [], "growth": [], "steps": []}
    for c in ctxs:
        agm_by_fy = {a["fy"]: a["date"] for a in c["agms"] if a["regular"]}
        for r in c["rounds"]:
            t = r["type"]
            if r["pay"]:
                s["pay"].setdefault(t, []).append((r["pay"] - r["record"]).days)
            if r["fixed"]:
                continue
            s["qe"].setdefault(t, []).append((r["record"] - r["qe"]).days)
            if r["decided"] and r["decided"] < r["record"]:
                s["dec"].setdefault(t, []).append((r["record"] - r["decided"]).days)
            if t == "결산배당" and r["fy"] in agm_by_fy:
                s["agm"].append((r["record"] - agm_by_fy[r["fy"]]).days)
        y = c["years"]
        for fy, d in y.items():
            s["growth"].append((fy, owner_ni(c, fy, 3)[0] or 0, owner_ni(c, fy - 1, 3)[0] or 0, final_of(c, fy), final_of(c, fy - 1)))
        s["steps"] += year_steps(c)
    return s

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
    flow = lambda c, y: next((x for b in ("연결", "별도") if (x := ytd(ctx, c, b, y, 4)) is not None), None)   # 연결 없는 회사는 별도
    ys = [(d, flow(BUYBACK, y)) for y in range(fy - CASH_SHARE_YEARS, fy) if (d := flow(DIV_PAID, y)) is not None]
    if not ys or all(b is None for _, b in ys):
        return None, ""   # 배당금 지급·자기주식 취득 계정이 빠진 해·회사(금융업 등)는 모름 — 0원으로 보면 현금 0%·100%로 틀림
    div, bb = sum(abs(d) for d, _ in ys), sum(abs(b or 0) for _, b in ys)
    return (div / (div + bb), f"최근 {len(ys)}년 현금흐름표 배당 {div / 1e8:,.0f}억·자사주 {bb / 1e8:,.0f}억") if div + bb else (None, "")

def run_dps(ctx, fy):
    """직전 사업연도부터 거슬러 끊김 없이 이어진 연간 DPS (오래된 것부터)"""
    ys = []
    while annual_dps(ctx, fy - 1 - len(ys)):
        ys.insert(0, annual_dps(ctx, fy - 1 - len(ys)))
    return ys

def for_date(ctx, t, fy, q):
    """한 회차의 날짜 재료 — 이번 회차 공시(기준일 안내·공고), 같은 종류 지난 회차, 정관·결정기관, 주총, 실적발표 예정일"""
    g, qe = GROUP[t], quarter_end(fy, q, ctx["acc"])
    season_end = qe + datetime.timedelta(days=SEASON_DAYS[t])
    past = [r for r in ctx["rounds"] if r["type"] == t]
    last = past[-1] if past else None
    since = ctx["charter_since"].get(g)
    kinds = (t,) if t == "결산배당" else ("분기배당", "중간배당")   # 'N분기 배당' 안내는 그 분기의 분기·중간배당 회차 모두에 해당
    regular = [a for a in ctx["agms"] if a["regular"]] if t == "결산배당" else []
    pre = [to_date(e["expected_date"]) for e in ctx["earn"] if e.get("period_end") == qe.isoformat()]
    ir = [to_date(e["expected_date"]) for e in ctx["earn"] if e.get("src") == "IR" and qe < to_date(e["expected_date"]) <= qe + datetime.timedelta(days=100)]
    lo = qe - datetime.timedelta(days=31)
    v = {"t": t, "fy": fy, "q": q, "qe": qe, "season_end": season_end, "season_days": SEASON_DAYS[t],
         "notice": next((n for n in reversed(ctx["notices"]) if n["div_type"] in kinds and (n["fy"], n["q"]) == (fy, q)), None),
         "ann_rec": next((n for n in reversed(ctx["rec_notices"]) if n["div_type"] in ("", t) and qe <= n["record"] <= season_end), None),
         "last": last, "before": next((r for r in reversed(past) if (r["fy"], r["q"]) != (fy, q)), None),
         "same_q": next((r for r in reversed(past) if r["q"] == q), None),
         "case": next((r for r in reversed(past) if since and r["record"] > since), None),
         "charter": ctx["charter"].get(g), "since": since, "pending": ctx["charter_pending"],
         "pend": (ctx["charter_pending"].get("block") or {}).get(g) == "O",
         "organ": ctx["organ"].get(g, ""), "report": ctx["policy_report"],
         "agm": next((a for a in reversed(regular) if a["fy"] == fy), None),
         "prev_agm": next((a for a in reversed(regular) if last and a["fy"] == last["fy"]), None),
         "announce": min(pre) if pre else min(ir) if ir else None,
         "div_rec": any(lo < n["date"] <= qe for n in ctx["rec_notices"]), "agm_rec": any(lo < d <= qe for d in ctx["agm_records"])}
    on = lambda r: (f"{r['fy']} Q{r['q']} 기준일 {iso(r['record'])} — {'결산기말·분기말' if r['fixed'] else '이사회가 따로 정함'}" if r else "",
                    "있음" if r else "없음", (DART_VIEW + r["rcept_no"] if r["rcept_no"] else r["src"]) if r else "")
    day = lambda d: (iso(d), "있음") if d else ("", "없음")
    ch, n, a = v["charter"], v["notice"], v["ann_rec"]
    rows = [("결산월", f"{ctx['acc']}월", "있음" if ctx["acc_known"] else "모름", "" if ctx["acc_known"] else "결산월 조회 실패 — 12월로 가정"),
            ("정관 기준일", ("이사회가 정할 수 있음" if ch["v"] == "O" else "결산기말·분기말 고정" if ch["v"] == "X" else ch["v"][:80]) if ch else "",
             "있음" if ch else "없음", ch["src"] if ch else ""),
            ("선진화 정관 시점", *day(since), ""),
            ("정관 기준일 변경 안건", f"{v['pending']['date']} 주총" if v["pending"] else "", "있음" if v["pending"] else "없음", ""),
            ("배당 결정기관", v["organ"], "있음" if v["organ"] else "없음", v["report"]),
            ("기준일 안내 자율공시", (iso(n["date"]) + (f" · {n['hint']} 이후" if n["hint"] else "")) if n else "", "있음" if n else "없음",
             DART_VIEW + n["rcept_no"] if n else ""),
            ("기준일 공고", f"기준일 {iso(a['record'])}" if a else "", "있음" if a else "없음", f"{iso(a['date'])} 공고" if a else ""),
            ("직전 같은 배당", *on(last)),
            ("작년 같은 분기", *on(v["same_q"])),
            ("정관 바꾼 뒤 같은 배당", *on(v["case"])),
            ("실적발표 예정일", *day(v["announce"]), "결산실적 예고·IR")]
    if t == "결산배당":
        rows += [("이번 회차 정기주총", *day(v["agm"] and v["agm"]["date"]), ""), ("직전 회차 정기주총", *day(v["prev_agm"] and v["prev_agm"]["date"]), ""),
                 ("기말 직전 한 달 명부폐쇄", f"배당 기준일 공고 {'있음' if v['div_rec'] else '없음'} · 주총 명부폐쇄 {'있음' if v['agm_rec'] else '없음'}", "있음", "")]
    return {"v": v, "table": [{"item": i, "value": val, "status": st, "check": "자동", "src": src, "sig": f"{i}|{val}"} for i, val, st, src in rows]}

def for_amount(ctx, fy, q, rec):
    """한 회차의 금액 재료 (회사 × 사업연도 × 분기 — 결산 4·중간 2·분기 1~3, 주식 수·분할 보정은 예상 기준일 rec 기준).
    3분기 누적 이익(q3·q3_prev·q3_hist)은 지배순이익 금액 — 비율로만 쓰여 주식 수가 필요 없고 분할에도 안 흔들림"""
    y = ctx["years"].get(fy, {})
    sh, sh_txt = shares(ctx, rec)
    eps, eps_txt = eps_of(ctx, fy, sh)
    per = lambda f: owner_ni(ctx, f, 3)[0] or 0
    upto = next((k for k in range(q, 0, -1) if owner_ni(ctx, fy, k)[0] is not None), 0)
    ni, ni_txt = owner_ni(ctx, fy, upto) if upto else (None, "")
    sep = ytd(ctx, NI, "별도", fy, 3) or y.get("q3_ni_sep", 0) * 1e6
    pol = policy(ctx, fy)
    share, share_txt = cash_share(ctx, fy)
    payouts = {fy - i: ctx["years"][fy - i]["payout"] for i in (3, 2, 1) if ctx["years"].get(fy - i, {}).get("payout", 0) > 0}   # 사업보고서 해마다 배당성향
    pays = list(payouts.values())
    c_dps, c_eps = cons_of(ctx, fy, "dps"), cons_of(ctx, fy, "eps")
    last = final_of(ctx, fy - 1)
    p, p_txt = paid(ctx, fy)
    rounds = [{**r, "adj": share_factor(ctx["splits"], r["record"], rec)} for r in ctx["rounds"]]
    v = {"fy": fy, "rounds": rounds, "acc": int(ctx["acc"]),
         "eps": eps, "eps_txt": eps_txt, "sep_eps": sep / sh if sep > 0 and sh else 0, "q": q, "ni_ytd": ni, "ni_upto": upto,
         "pays": pays, "pay_last": ctx["years"].get(fy - 1, {}).get("payout", 0),
         "q3": per(fy), "q3_prev": per(fy - 1),
         "q3_hist": [x for f in sorted(ctx["years"]) if f < fy and (x := per(f)) > 0],
         "final_hist": [x for f in sorted(ctx["years"]) if f < fy and (x := final_of(ctx, f))],
         "dps_hist": run_dps(ctx, fy), "prev": annual_dps(ctx, fy - 1), "prev_adj": last_annual(ctx, fy, rec),
         "last_final": last, "last_adj": share_factor(ctx["splits"], quarter_end(fy - 1, 4, ctx["acc"]), rec),
         "paid": p, "shares": sh, "cons_dps": c_dps, "cons_eps": c_eps,
         "cons_q": [x for x in ctx["cons"] if "quarter" in x],
         "policy": {k: {**r, "adj": share_factor(ctx["splits"], r["date"], rec)} for k, r in pol.items()},
         "share": share, "share_txt": share_txt, "steps": year_steps(ctx),
         "tax_hist": any(r["tax_rule"] for r in ctx["rounds"] if r["type"] == "결산배당" and r["fy"] < fy),
         "evidence": evidence(ctx, fy)}
    fixed = {item: val for (f, item), val in ctx["overrides"].items() if f == fy}
    for item, val in fixed.items():   # 사람이 확인 목록에서 고친 값이 자동 값을 대신함
        for k in OVERRIDE[item]:
            v[k] = {"date": datetime.date.today(), "dps": val} if k == "cons_dps" else val
    gap = paid_gap(ctx, fy)
    def row(item, value, status, src="", check="자동", sig=""):
        sig = sig or f"{item}|{value}"   # 값이 바뀌면 열쇠도 바뀌어 다시 확인
        man = item in fixed or sig in ctx["human_sigs"]
        return {"item": item, "value": value, "status": "있음" if item in fixed else status, "check": "사람 확인" if man else check,
                "src": "사람(확인 목록)" if item in fixed else src, "sig": sig}
    c_dps, eps, sh, share = v["cons_dps"], v["eps"], v["shares"], v["share"]
    table = [
        row("올해 순이익(EPS)", f"{eps:,.0f}원" if eps > 0 else "", "있음" if eps > 0 else "없음", v["eps_txt"]),
        row("지배순이익 누적", f"{ni / 1e8:,.0f}억({upto}분기까지)" if ni is not None else "", "있음" if ni is not None else "모름", ni_txt),
        row("과거 배당성향(3년)", " · ".join(f"{y} {x:g}%" for y, x in payouts.items()), "있음" if len(pays) == 3 else "모름" if pays else "없음", "사업보고서 배당 표"),
        row("작년 연간 DPS", f"{v['prev']:,.0f}원" if v["prev"] else "", "있음" if v["prev"] else "없음", "사업보고서·공시 회차"),
        row("작년 결산 몫", f"{v['last_final']:,.0f}원" if v["last_final"] else "", "있음" if v["last_final"] else "안 함" if v["last_final"] == 0 else "모름",
            "결산 공시 회차·사업보고서"),
        row("올해 이미 정한 배당", f"{v['paid']:,.0f}원", "의심" if gap else "있음", p_txt + (f" · 3분기보고서 누적 {gap[0]:,.0f}원과 다름" if gap else "")),
        row("주식 수", f"{sh / 1e8:,.2f}억주" if sh else "", "있음" if sh else "없음", sh_txt),
        row("연간 컨센 DPS", f"{c_dps['dps']:,.0f}원" if c_dps else "", "있음" if c_dps else "없음", f"네이버 {iso(c_dps['date'])}" if c_dps else "네이버"),
    ]
    if any(k[0] in ("return", "budget") for k in pol):
        table.append(row("현금 몫(총환원 중 배당)", f"{share:.0%}" if share is not None else "", "있음" if share is not None else "없음", v["share_txt"]))
    for r in pol.values():
        fx = r.get("fix") and {k: r["fix"][k] for k in ("kind", "slot", "lo", "hi", "fy")}   # 판독자가 고쳐 읽은 값
        table.append({**row("회사 정책", policy_text([r]), "의심" if r["check"] == "판독자 다름" else "있음",
                            f"{iso(r['date'])} {r.get('title', '')} — {r.get('text', '')[:80]}", r["check"], r["sig"]),
                      **({"fix": fx, "fix_text": policy_text([fx])} if fx else {})})
    return {"v": v, "table": table}
