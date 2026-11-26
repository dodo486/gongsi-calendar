# -*- coding: utf-8 -*-
"""배당 금액 — 신호마다 후보 하나({signal, name, value 원, basis}) 또는 None, estimate 가 회사가 밝힌 것(LAW) → 배당종류·회사 성향(style)별
신호 순서(verify 가 과거 적중률로 rank, med["priority"])대로 고른다. 결산 신호는 연간 DPS 를 추정해 from_annual 한 곳에서 올해 이미 정한 배당을 뺀다.
사실(장부·정책·컨센·주식 수)은 facts 조회만 쓴다. 날짜는 date"""
import itertools, math, statistics
from core.krx_cal import iso
from derive.dividend.facts import (share_factor, spiked, paid, annual_dps, final_of, shares, cash_share, cons_of, eps_of, policy, evidence, quarter_end, year_steps, last_annual,
                                   EARN_UP, MIN_SAMPLES, SPECIAL_MULT)


def run_dps(ctx, fy):
    """직전 사업연도부터 거슬러 끊김 없이 이어진 연간 DPS (오래된 것부터)"""
    ys = []
    while ctx["years"].get(fy - 1 - len(ys), {}).get("dps"):
        ys.insert(0, ctx["years"][fy - 1 - len(ys)]["dps"])
    return ys

def has_special(vals):
    """금액 이력(오래된 것부터)에 튄 값이 있나. 추세·안 깎음은 튄 해를 이어 그리면 폭주해 안 씀"""
    return any(spiked(vals[:i], v) for i, v in enumerate(vals) if i)

def style(ctx, fy):
    """연간 DPS 3년 이상 이어졌고 줄인 적 없으면 안정, 줄인 적 있으면 연동"""
    ys = run_dps(ctx, fy)
    return "이력 짧음" if len(ys) < 3 else "안정" if all(b >= a for a, b in zip(ys, ys[1:])) else "연동"

def from_annual(ctx, fy, signal, name, annual, basis):
    """연간 DPS 추정 → 이번 결산 몫 = 연간 − 올해 이미 정한 배당"""
    p = paid(ctx, fy)
    v = round(annual - p)
    return {"signal": signal, "name": name, "value": v, "basis": basis + (f" − 올해 이미 정한 {p:,.0f}원" if p else "")} if v > 0 else None

def repeat_last(ctx, t, fy, rec, med):
    """직전 같은 배당 반복 — 결산은 작년 결산(안 했으면 없음), 분기·중간은 직전 같은 종류 회차. 그 뒤 분할·병합은 예상 기준일 주식 기준으로.
    2배 넘게 뛴 회차도 건너뛰지 않음 — 6년 이력에서 그런 해 40번 중 26번은 다음 해도 유지(실제 인상), 뛴 금액 반복이 오차 37% vs 63%"""
    if t == "결산배당":
        now = [r for r in ctx["rounds"] if r["fy"] == fy and r["type"] != t and r["dps"] and not r["special"]]
        if now and not any(r["fy"] == fy - 1 and r["type"] != t and r["dps"] for r in ctx["rounds"]):   # 올해 분기·중간을 시작 — 작년 결산이 곧 작년 연간
            annual, p, q = annual_dps(ctx, fy - 1), paid(ctx, fy), max(now, key=lambda r: r["q"])
            v = max(round(annual - p), int(q["dps"]))
            return {"signal": "repeat_last", "name": "직전 반복", "value": v,
                    "basis": f"올해 {q['type']} 시작 — " + (f"작년 연간 {annual:,.0f}원 − 올해 이미 정한 {p:,.0f}원" if v > int(q["dps"]) else f"올해 Q{q['q']} {int(q['dps']):,}원 반복")}
        last = final_of(ctx, fy - 1)
        if not last:
            return None
        adj = share_factor(ctx["splits"], quarter_end(fy - 1, 4, ctx["acc"]), rec)
        return {"signal": "repeat_last", "name": "직전 반복", "value": round(last / adj),
                "basis": f"작년({fy - 1}) 결산 {last:,}원 반복" + (f" · 그 뒤 {'분할' if adj > 1 else '병합'} 1주→{adj:g}주 보정" if adj != 1 else "")}
    reg = next((r for r in reversed(ctx["rounds"]) if r["type"] == t and r["dps"]), None)
    if not reg:
        return None
    adj = share_factor(ctx["splits"], reg["record"], rec)
    return {"signal": "repeat_last", "name": "직전 반복", "value": round(int(reg["dps"]) / adj),
            "basis": f"직전 회차({reg['fy']} Q{reg['q']}) {int(reg['dps']):,}원 반복"
                     + (f" · 그 뒤 {'분할' if adj > 1 else '병합'} 1주→{adj:g}주 보정" if adj != 1 else "")}

def consensus_left(ctx, t, fy, rec, med):
    """연간 주당배당금 컨센서스 − 같은 사업연도에 이미 정한 배당, 남은 회차 수로 나눔"""
    c = cons_of(ctx, fy, "dps")
    if not c:
        return None
    done = [r for r in ctx["rounds"] if r["fy"] == fy and r["dps"]]
    paid_ = sum(int(r["dps"]) for r in done)
    left = max(len({r["q"] for r in ctx["rounds"] if r["fy"] == fy - 1 and r["dps"]}) - len({r["q"] for r in done}), 1)   # 같은 분기가 분기·중간 두 줄로 잡힌 회차는 하나로
    v = round((c["dps"] - paid_) / left)
    return {"signal": "consensus_left", "name": "컨센", "value": v,
            "basis": f"{iso(c['date'])} 네이버 연간 주당배당금 컨센 {c['dps']:,}원" + (f" − 올해 이미 정한 {paid_:,}원" if paid_ else "")
                     + (f" ÷ 남은 {left}회" if left > 1 else "")} if v > 0 else None

def trend(ctx, t, fy, rec, med):
    """연간 DPS 최근 3년 줄지 않았으면 그 3년 평균 증가폭만큼 더"""
    ys = run_dps(ctx, fy)[-3:]
    if t != "결산배당" or len(ys) < 3 or any(b < a for a, b in zip(ys, ys[1:])) or has_special(run_dps(ctx, fy)):
        return None
    annual = ys[-1] + (ys[-1] - ys[0]) / 2
    return from_annual(ctx, fy, "trend", "추세", annual, f"연간 DPS {fy - 3}~{fy - 1} {'→'.join(f'{y:,.0f}' for y in ys)} 추세 → 올해 {annual:,.0f}원")

def payout_eps(ctx, t, fy, rec, med):
    """최근 3년 배당성향 중앙값 × 올해 EPS — 작년 결산을 안 했으면 그 전 성향은 근거가 못 됨(재개는 회사 공시·컨센으로만)"""
    pays = [ctx["years"][fy - i]["payout"] for i in (1, 2, 3) if ctx["years"].get(fy - i, {}).get("payout", 0) > 0]
    eps, eps_txt = eps_of(ctx, fy)
    if t != "결산배당" or not pays or final_of(ctx, fy - 1) == 0 or eps <= 0:
        return None
    po = statistics.median(pays)
    return from_annual(ctx, fy, "payout_eps", "성향×이익", po / 100 * eps, f"배당성향 {po:.1f}%(최근 {len(pays)}년 중앙) × {eps_txt}")

def no_cut(ctx, t, fy, rec, med):
    """연간 DPS 4년 넘게 줄인 적 없는 회사 — 작년 연간을 바닥으로, 성향×이익이 더 크면 그만큼(작년의 SPECIAL_MULT 배 넘으면 일회성 이익이라 안 씀)"""
    ys = run_dps(ctx, fy)
    if t != "결산배당" or len(ys) < 4 or any(b < a for a, b in zip(ys, ys[1:])) or has_special(ys):
        return None
    pe = payout_eps(ctx, t, fy, rec, med)
    if pe and ys[-1] < pe["value"] + paid(ctx, fy) <= SPECIAL_MULT * ys[-1]:
        return {**pe, "signal": "no_cut", "name": "안 깎음", "basis": f"작년 연간 {ys[-1]:,.0f}원 넘음 · " + pe["basis"]}
    return from_annual(ctx, fy, "no_cut", "안 깎음", ys[-1], f"연간 DPS {len(ys)}년 줄인 적 없음 — 작년 {ys[-1]:,.0f}원 바닥")

def earn_up(ctx, t, fy, rec, med):
    """올해 3분기 누적 EPS 가 작년보다 EARN_UP 배 이상이면 직전 반복 × 과거 그런 회사들의 결산 배당 증가율, 아니면 직전 반복"""
    rl, up = repeat_last(ctx, t, fy, rec, med), med["dps_up"].get(fy)
    q3, lq3 = ctx["years"].get(fy, {}).get("q3_eps", 0), ctx["years"].get(fy - 1, {}).get("q3_eps", 0)
    if t != "결산배당" or not rl or not up or q3 <= 0 or lq3 <= 0:
        return None
    k = up if q3 >= EARN_UP * lq3 else 1
    return {"signal": "earn_up", "name": "이익 반영", "value": round(rl["value"] * k),
            "basis": f"3분기 누적 EPS 작년 {lq3:,.0f}원 → 올해 {q3:,.0f}원" + (f", 이렇게 늘었던 회사들 배당 {k:.2f}배 × " if k != 1 else " (크게 늘지 않음) · ") + rl["basis"]}

def step_up(ctx, t, fy, rec, med):
    """분기배당 회사의 새해 첫 분기 — 작년 마지막 분기배당 × 새해 인상 비율(이 회사 과거 중앙, 없으면 수집대상 전체 중앙 med["q_step"])"""
    q = [r for r in ctx["rounds"] if r["type"] == t and r["dps"] and not r["special"]]
    if t != "분기배당" or not q or q[-1]["fy"] != fy - 1 or any(r["fy"] == fy for r in q):
        return None
    own = [k for f, k in year_steps(ctx) if f < fy]
    k = statistics.median(own) if own else med["q_step"].get(fy)
    if not k:
        return None
    adj = share_factor(ctx["splits"], q[-1]["record"], rec)
    return {"signal": "step_up", "name": "새해 인상", "value": round(int(q[-1]["dps"]) * k / adj),
            "basis": f"작년 마지막 분기({q[-1]['fy']} Q{q[-1]['q']}) {int(q[-1]['dps']):,}원 × 새해 인상 {k:.2f}배("
                     + (f"이 회사 과거 {len(own)}번 중앙" if own else "수집대상 전체 중앙") + ")"}

TAX_PAYOUT_HIGH, TAX_PAYOUT_LOW, TAX_GROWTH = 0.40, 0.25, 0.10   # 조특법 §104의27 고배당기업: 배당성향 40% 이상, 또는 25% 이상 + 배당 전년 대비 10% 증가
TAX_MAX_JUMP = 2   # 요건 맞추는 데 작년의 이 배수 넘게 필요하면(일회성 이익) 포기한다고 봄 — 2025 요건 충족 회사들의 필요 인상은 최대 1.48배

def income_per_share(ctx, fy, p, sh):
    """정책 줄이 말하는 순이익(주당) → (원, 근거) — 별도 기준이면 별도 순이익 3분기 누적 ÷ 주식 수
    (별도 이익은 자회사 배당이 상반기에 몰려 ×4/3 하면 과대 — 과거 21건 오차 중앙 3.3% vs 17.5%), 아니면 eps_of"""
    y = ctx["years"].get(fy, {})
    if p["base"] == "별도" and y.get("q3_ni_sep", 0) > 0 and sh:
        return y["q3_ni_sep"] * 1e6 / sh, f"별도 순이익 3분기 누적 ÷ {sh / 1e8:,.2f}억주"
    return eps_of(ctx, fy)

def policy_levels(ctx, t, fy, rec):
    """1단계 — 이 사업연도 정책 줄 하나하나를 연간 주당 금액으로 → [{role, value, why, floor}].
    role: exact 회사가 정한 금액 · level 정책으로 계산한 금액 · range 현금 총액 범위(value = (하한, 상한)). 최소 주당 금액은 금액이 아니라 바닥이라 estimate 에서.
    floor(값) = 최소 요건이라 단위 맞출 때 올림. 순이익 × 비율이 작년·연간 컨센 DPS 중 큰 값의 SPECIAL_MULT 배 넘으면 일회성 이익이라 뺌
    (컨센은 같은 정책을 보고 낸 숫자라 실적이 진짜 는 해는 통과). 총환원율·총환원 금액은 자사주 포함이라 현금 몫(cash_share)을 곱함"""
    pol, sh, out = policy(ctx, fy), shares(ctx, rec), []
    prev = last_annual(ctx, fy, rec)
    c, eps = cons_of(ctx, fy, "dps"), eps_of(ctx, fy)[0]
    cap = SPECIAL_MULT * max(prev, c["dps"] if c else 0)
    share, share_txt = cash_share(ctx, fy)
    src = lambda p: f"{iso(p['date'])} {p['title']}"
    more = lambda p: " 이상" if p["hi"] is None else ""
    for (kind, slot), p in pol.items():
        if kind == "dps" and slot == "결산" and p["hi"]:
            out.append({"role": "exact", "value": paid(ctx, fy) + p["hi"], "why": f"{src(p)} 결산 주당 {p['hi']:,.0f}원", "floor": False})
        elif kind == "dps" and slot == "연간" and p["hi"]:
            out.append({"role": "exact", "value": p["hi"], "floor": False, "why": f"{src(p)} {fy}년 주당 {p['hi']:,.0f}원"})
        elif kind == "cash" and slot == "연간" and sh:
            out.append({"role": "range", "value": ((p["lo"] or 0) / sh, (p["hi"] or math.inf) / sh), "floor": True,
                        "why": f"{src(p)} {fy} 현금배당 총액 {(p['lo'] or 0) / 1e8:,.0f}억~" + (f"{p['hi'] / 1e8:,.0f}억" if p["hi"] else "") + f" ÷ {sh / 1e8:,.2f}억주"})
        elif kind in ("payout", "return") and p["lo"]:
            rate = (p["lo"] + p["hi"]) / 2 if p["hi"] else p["lo"]
            base, base_txt = income_per_share(ctx, fy, p, sh)
            k = 1 if kind == "payout" else share
            if base > 0 and k is not None and rate / 100 * base * k <= cap:
                out.append({"role": "level", "value": rate / 100 * base * k, "floor": kind == "payout" and p["hi"] is None,
                            "why": f"{src(p)} {'목표 배당성향' if kind == 'payout' else '총주주환원율'} {rate:g}%{more(p)} × 주당 {base:,.0f}원({base_txt})"
                                   + ("" if kind == "payout" else f" × 현금 {share:.0%}({share_txt})")})
        elif kind == "budget" and share is not None and sh:
            out.append({"role": "level", "value": p["lo"] * share / sh, "floor": False,
                        "why": f"{src(p)} 총주주환원 {p['lo'] / 1e8:,.0f}억원{more(p)} × 현금 {share:.0%}({share_txt}) ÷ {sh / 1e8:,.2f}억주"})
    hd = pol.get(("high_div", "연간"))
    if (hd["on"] if hd else any(r["tax_rule"] for r in ctx["rounds"] if r["type"] == t and r["fy"] < fy)) and eps > 0 and prev:
        prev_t = hd["prev_total"] / sh if hd and hd["prev_total"] and sh else prev   # 요건은 배당총액 기준 — 자사주 소각으로 주식이 줄면 DPS 는 10% 넘게 올라야 함
        need = max(prev, min(TAX_PAYOUT_HIGH * eps, max(TAX_PAYOUT_LOW * eps, (1 + TAX_GROWTH) * prev_t)))
        if need <= TAX_MAX_JUMP * prev or any(x["role"] == "range" for x in out):
            out.append({"role": "level", "value": need, "floor": True,
                        "why": f"고배당기업({src(hd) + ' 밸류업 표' if hd else '배당결정 공시'}) 요건 최소 — 작년 배당총액의 1.1배·순이익 25% 중 큰 값, 40% 넘지 않게"})
    return out

def stated(ctx, t, fy, rec, med):
    """회사가 밝힌 배당(트레이더 단계 2·3). 분기·중간: 그 배당의 주당 금액.
    결산: 1단계 policy_levels → 2단계 고르기: 회사가 정한 금액 > 계산한 금액 중 큰 값(현금 총액 범위가 있으면 그 안에서 컨센도 후보, 벗어나면 경계) —
    정책 숫자는 최소치라 작년 DPS 아래로 안 줌(2025: 최소치 그대로 오차 28%, 작년 바닥 14.5%) →
    3단계 from_annual(올해 이미 정한 배당 빼기)"""
    if t != "결산배당":
        q = policy(ctx, fy).get(("dps", "분기" if t == "분기배당" else "중간"))
        return q and {"signal": "stated", "name": "회사 정책", "value": round(q["lo"] / share_factor(ctx["splits"], q["date"], rec)),
                      "basis": f"{iso(q['date'])} {q['title']} {t} 주당 {q['lo']:,.0f}원" + (" 이상" if q["hi"] is None else "")}
    lv = policy_levels(ctx, t, fy, rec)
    exact = next((x for x in lv if x["role"] == "exact"), None)
    rng = next((x for x in lv if x["role"] == "range"), None)
    cands = [x for x in lv if x["role"] == "level"]
    if exact:
        pick = exact
    elif rng:
        c = cons_of(ctx, fy, "dps")
        lo, hi = rng["value"]
        want = max(cands + ([{"value": c["dps"], "why": "컨센", "floor": False}] if c else []), key=lambda x: x["value"], default={"value": lo, "why": "", "floor": True})
        v = min(max(want["value"], lo, paid(ctx, fy)), hi)
        pick = {"value": v, "floor": want["floor"] or v > want["value"],
                "why": rng["why"] + (f" (범위 안 {want['why']} {want['value']:,.0f}원)" if want["why"] and want["value"] > lo else "")}
    elif cands:
        pick = max(cands, key=lambda x: x["value"])
        prev = last_annual(ctx, fy, rec)
        if pick["value"] < prev:
            pick = {"value": prev, "floor": True, "why": f"정책 최소({pick['why']})가 작년보다 낮음 → 작년 DPS {prev:,.0f}원 유지"}
    else:
        return None
    e = from_annual(ctx, fy, "stated", "회사 정책", pick["value"], f"{pick['why']} → 연간 {pick['value']:,.0f}원")
    return e and {**e, "floor": pick["floor"]}

SIGNALS = [repeat_last, consensus_left, trend, payout_eps, no_cut, earn_up, step_up, stated]
LAW = ["stated"]   # 트레이더 순서: 회사가 밝힌 배당(단계 2·3) — 과거 적중순 경쟁 밖, 있으면 앞

def rank(xs):
    """신호 순서 — 과거 회차(xs: sig_dps·actual_dps)에 이 순서로 처음 있는 신호를 썼다면의 평균 오차(회차마다 100%에서 자름)가
    가장 작은 순서 — 중앙값은 가운데 몇 회차만 봐서 소수 회차로 순서가 우연히 뒤집힘. 튄 회차도 넣음(대부분 실제 인상).
    표본 MIN_SAMPLES 미만 신호는 검증 전이라 SIGNALS 순으로 뒤에"""
    tested = [s.__name__ for s in SIGNALS if s.__name__ not in LAW and sum(s.__name__ in x["sig_dps"] for x in xs) >= MIN_SAMPLES]
    errs = lambda order: [abs(x["sig_dps"][k] - x["actual_dps"]) / x["actual_dps"] for x in xs if (k := next((k for k in order if k in x["sig_dps"]), None))]
    best = min(itertools.permutations(tested), key=lambda o: statistics.mean(min(v, 1) for v in errs(o))) if tested else ()
    return list(best) + [s.__name__ for s in SIGNALS if s.__name__ not in best and s.__name__ not in LAW]

def estimate(ctx, t, fy, rec, med):
    """금액 하나(LAW → 신호 순서대로 처음 있는 것) + 표시 둘 — 특별배당 가능성(결산에서 특별배당 이력 M17과 올해 이익 튐 M15가 같이 켜지면,
    특별 몫 금액은 과거 검증에서 오차를 키워 더하지 않음), 실적 서프라이즈 가능성(트레이더 단계 1: 직전 2개 분기 순이익이 발표 전 컨센 상회).
    후보는 회사가 딱 떨어지게 주는 단위(직전 금액의 단위)로 맞추고(최소 요건에서 나온 회사 정책 값 floor 는 올림), 결산 몫은 바닥 아래로 안 줌 —
    그해 분기·중간 정규 금액(과거 분기 43회 중 0회, 중간 31회 중 3회), 회사가 밝힌 최소 연간 주당 금액 − 올해 이미 정한 배당"""
    st = style(ctx, fy)
    fin = [v for f in sorted(ctx["years"]) if f < fy and (v := final_of(ctx, f))]
    prev = [d["q3_eps"] for f, d in ctx["years"].items() if f < fy and d.get("q3_eps", 0) > 0]
    q3 = ctx["years"].get(fy, {}).get("q3_eps", 0)
    hist = any(r["special"] for r in ctx["rounds"]) or has_special(fin)
    beat = []
    for q in sorted({x["quarter"] for x in ctx["cons"] if "quarter" in x}):
        act = next((x["ni_act"] for x in ctx["cons"] if x.get("quarter") == q and "ni_act" in x), None)
        est = next((x["ni"] for x in reversed(ctx["cons"]) if x.get("quarter") == q and "ni" in x), None)
        if act is not None and est:
            beat.append((q, act, est))
    surprise = (f"실적 서프라이즈 가능성 — 직전 2개 분기 순이익이 컨센 상회 ("
                + ", ".join(f"{q[:4]}.{q[4:]} {a:,}억 vs 컨센 {e:,}억" for q, a, e in beat[-2:]) + ")"
                if len(beat) >= 2 and all(a > e for _, a, e in beat[-2:]) else "")
    special = (f"특별배당 가능성 — 특별배당 준 적 있음 + 올해 3분기 누적 EPS {q3:,.0f}원이 평소({statistics.median(prev):,.0f}원)의 {SPECIAL_MULT}배 넘음"
               if t == "결산배당" and hist and spiked(prev, q3) else "")
    cands = {c["signal"]: c for c in (s(ctx, t, fy, rec, med) for s in SIGNALS) if c}
    last = cands.get("repeat_last", {}).get("value") or next((int(r["dps"]) for r in reversed(ctx["rounds"]) if r["dps"]), 0)
    unit = next((u for u in (1000, 500, 100, 50, 10, 5) if last and last % u == 0 and last >= 2 * u), 1)
    fit = lambda v, floor: (math.ceil if floor else round)(v / unit) * unit
    cands = {k: {**c, "value": fit(c["value"], c.get("floor"))} for k, c in cands.items() if fit(c["value"], c.get("floor")) > 0}
    floors = []
    reg = max((r for r in ctx["rounds"] if t == "결산배당" and r["fy"] == fy and r["type"] != t and r["dps"] and not r["special"]), key=lambda r: r["q"], default=None)
    if reg:
        floors.append((int(reg["dps"]), f"올해 Q{reg['q']} {reg['type']} {int(reg['dps']):,}원 아래로 안 줌"))
    mn = policy(ctx, fy).get(("dps", "연간")) if t == "결산배당" else None
    if mn and mn["lo"] > paid(ctx, fy):
        floors.append((math.ceil(mn["lo"] - paid(ctx, fy)), f"{iso(mn['date'])} 회사가 밝힌 최소 주당 {mn['lo']:,.0f}원 − 올해 이미 정한 {paid(ctx, fy):,.0f}원 아래로 안 줌"))
    for low, why in floors:
        cands = {k: c if c["value"] >= low else {**c, "value": low, "basis": c["basis"] + " → " + why} for k, c in cands.items()}
    pick = next((cands[k] for k in LAW + med.get("priority", {}).get(f"{t}|{st}", [s.__name__ for s in SIGNALS]) if k in cands), None)
    empty = (f"작년({fy - 1}) 결산배당 안 함 — 올해 근거({evidence(ctx, fy) or '없음'})로는 금액을 못 정함" if t == "결산배당" and final_of(ctx, fy - 1) == 0
             else "금액 이력 없음")
    return {"per_share": str(pick["value"]) if pick else "", "amount_basis": pick["basis"] if pick else empty,
            "amount_style": st, "amount_special": special, "amount_surprise": surprise, "amount_signals": list(cands.values()),
            "amount_cands": " · ".join(f"{c['name']} {c['value']:,}원" for c in cands.values())}
