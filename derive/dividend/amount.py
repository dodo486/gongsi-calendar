# -*- coding: utf-8 -*-
"""배당 금액 — 신호마다 후보 하나({signal, name, value 원, basis}) 또는 None, estimate 가 배당종류·회사 성향(style)별 신호 순서(verify 가 과거 적중률로 rank, med["priority"])대로 고른다.
결산배당 연간 신호(추세·성향×이익·안 깎음·정책)·이익 반영는 연간 DPS 를 추정해 같은 사업연도에 이미 정한 배당을 뺀다. 날짜는 date"""
import itertools, math, statistics
from core.krx_cal import iso
from derive.dividend.facts import share_factor, EARN_UP, MIN_SAMPLES, SPECIAL_MULT


def run_dps(ctx, fy):
    """직전 사업연도부터 거슬러 끊김 없이 이어진 연간 DPS (오래된 것부터)"""
    ys = []
    while ctx["years"].get(fy - 1 - len(ys), {}).get("dps"):
        ys.insert(0, ctx["years"][fy - 1 - len(ys)]["dps"])
    return ys

def has_special(vals):
    """금액 이력(오래된 것부터)에 튄 값이 있나 — 그 전 값들 중앙의 SPECIAL_MULT 배 넘음. 추세·안 깎음은 튄 해를 이어 그리면 폭주해 안 씀"""
    return any(v > SPECIAL_MULT * statistics.median(vals[:i]) for i, v in enumerate(vals) if i)

def style(ctx, fy):
    """연간 DPS 3년 이상 이어졌고 줄인 적 없으면 안정, 줄인 적 있으면 연동"""
    ys = run_dps(ctx, fy)
    return "이력 짧음" if len(ys) < 3 else "안정" if all(b >= a for a, b in zip(ys, ys[1:])) else "연동"

def paid(ctx, t, fy):
    """같은 사업연도에 이 종류 말고 이미 정한 배당 — 3분기보고서 누적, 아직 없으면 공시 회차"""
    y = ctx["years"].get(fy, {})
    return y.get("q3_dps", 0) if "q3_eps" in y else sum(int(r["dps"]) for r in ctx["rounds"] if r["fy"] == fy and r["type"] != t and r["dps"])

def from_annual(ctx, t, fy, signal, name, annual, basis):
    """연간 DPS 추정 → 이번 결산 몫 = 연간 − 올해 이미 정한 배당"""
    p = paid(ctx, t, fy)
    v = round(annual - p)
    return {"signal": signal, "name": name, "value": v, "basis": basis + (f" − 올해 이미 정한 {p:,.0f}원" if p else "")} if v > 0 else None

def repeat_last(ctx, t, fy, rec, med):
    """직전 같은 종류 회차 금액 반복 — 그 뒤 분할·병합은 예상 기준일 주식 기준으로 보정. 결산 공시 회차가 없으면 작년 연간 − 3분기 누적.
    2배 넘게 뛴 회차도 건너뛰지 않음 — 6년 이력에서 그런 해 40번 중 26번은 다음 해도 유지(실제 인상), 뛴 금액 반복이 오차 37% vs 63%"""
    reg = next((r for r in reversed(ctx["rounds"]) if r["type"] == t and r["dps"]), None)
    if not reg:
        y = ctx["years"].get(fy - 1, {})
        if t != "결산배당" or not y.get("dps") or "q3_eps" not in y:
            return None
        v = round(y["dps"] - y.get("q3_dps", 0))
        return {"signal": "repeat_last", "name": "직전 반복", "value": v,
                "basis": f"작년({fy - 1}) 연간 {y['dps']:,.0f}원" + (f" − 3분기까지 {y['q3_dps']:,.0f}원" if y.get("q3_dps") else "") + " 결산 몫 반복"} if v > 0 else None
    adj = share_factor(ctx["splits"], reg["record"], rec)
    return {"signal": "repeat_last", "name": "직전 반복", "value": round(int(reg["dps"]) / adj),
            "basis": f"직전 회차({reg['fy']} Q{reg['q']}) {int(reg['dps']):,}원 반복"
                     + (f" · 그 뒤 {'분할' if adj > 1 else '병합'} 1주→{adj:g}주 보정" if adj != 1 else "")}

def consensus_left(ctx, t, fy, rec, med):
    """연간 주당배당금 컨센서스 − 같은 사업연도에 이미 정한 배당, 남은 회차 수로 나눔. 12월 결산만(네이버 연도 = 사업연도)"""
    c = next((x for x in reversed(ctx["cons"]) if x.get("fy") == str(fy) and "dps" in x), None)
    if not c or int(ctx["acc"]) != 12:
        return None
    done = [r for r in ctx["rounds"] if r["fy"] == fy and r["dps"]]
    paid_ = sum(int(r["dps"]) for r in done)
    left = max(sum(1 for r in ctx["rounds"] if r["fy"] == fy - 1) - len(done), 1)
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
    return from_annual(ctx, t, fy, "trend", "추세", annual, f"연간 DPS {fy - 3}~{fy - 1} {'→'.join(f'{y:,.0f}' for y in ys)} 추세 → 올해 {annual:,.0f}원")

def payout_eps(ctx, t, fy, rec, med):
    """최근 3년 배당성향 중앙값 × 올해 EPS(3분기 누적 × 4/3)"""
    pays = [ctx["years"][fy - i]["payout"] for i in (1, 2, 3) if ctx["years"].get(fy - i, {}).get("payout", 0) > 0]
    q3 = ctx["years"].get(fy, {}).get("q3_eps", 0)
    if t != "결산배당" or not pays or q3 <= 0:
        return None
    po = statistics.median(pays)
    return from_annual(ctx, t, fy, "payout_eps", "성향×이익", po / 100 * q3 * 4 / 3,
                       f"배당성향 {po:.1f}%(최근 {len(pays)}년 중앙) × 올해 EPS {q3 * 4 / 3:,.0f}원(3분기 누적 {q3:,.0f}원×4/3)")

def no_cut(ctx, t, fy, rec, med):
    """연간 DPS 4년 넘게 줄인 적 없는 회사 — 작년 연간을 바닥으로, 성향×이익이 더 크면 그만큼(작년의 SPECIAL_MULT 배 넘으면 일회성 이익이라 안 씀)"""
    ys = run_dps(ctx, fy)
    if t != "결산배당" or len(ys) < 4 or any(b < a for a, b in zip(ys, ys[1:])) or has_special(ys):
        return None
    pe = payout_eps(ctx, t, fy, rec, med)
    if pe and ys[-1] < pe["value"] + paid(ctx, t, fy) <= SPECIAL_MULT * ys[-1]:
        return {**pe, "signal": "no_cut", "name": "안 깎음", "basis": f"작년 연간 {ys[-1]:,.0f}원 넘음 · " + pe["basis"]}
    return from_annual(ctx, t, fy, "no_cut", "안 깎음", ys[-1], f"연간 DPS {len(ys)}년 줄인 적 없음 — 작년 {ys[-1]:,.0f}원 바닥")

def earn_up(ctx, t, fy, rec, med):
    """올해 3분기 누적 EPS 가 작년보다 EARN_UP 배 이상이면 직전 반복 × 과거 그런 회사들의 결산 배당 증가율, 아니면 직전 반복"""
    rl, up = repeat_last(ctx, t, fy, rec, med), med["dps_up"].get(fy)
    q3, lq3 = ctx["years"].get(fy, {}).get("q3_eps", 0), ctx["years"].get(fy - 1, {}).get("q3_eps", 0)
    if t != "결산배당" or not rl or not up or q3 <= 0 or lq3 <= 0:
        return None
    k = up if q3 >= EARN_UP * lq3 else 1
    return {"signal": "earn_up", "name": "이익 반영", "value": round(rl["value"] * k),
            "basis": f"3분기 누적 EPS 작년 {lq3:,.0f}원 → 올해 {q3:,.0f}원" + (f", 이렇게 늘었던 회사들 배당 {k:.2f}배 × " if k != 1 else " (크게 늘지 않음) · ") + rl["basis"]}

TAX_PAYOUT_HIGH, TAX_PAYOUT_LOW, TAX_GROWTH = 0.40, 0.25, 0.10   # 조특법 §104의27 고배당기업: 배당성향 40% 이상, 또는 25% 이상 + 배당 전년 대비 10% 증가
TAX_MAX_JUMP = 2   # 요건 맞추는 데 작년의 이 배수 넘게 필요하면(일회성 이익) 포기한다고 봄 — 2025 요건 충족 회사들의 필요 인상은 최대 1.48배

def valueup(ctx, t, fy, rec, med):
    """밸류업 정책(트레이더 단계 2). 결산: 다음 중 큰 연간 배당(작년 DPS 바닥) − 올해 이미 정한 배당 —
    고배당기업이면 요건 최소(배당성향 40%, 또는 25% + 배당총액 전년 대비 10% 증가 중 싼 쪽, 작년 이상, 작년의 TAX_MAX_JUMP 배 넘게 필요하면 포기 —
    그해 환원 규모를 공시한 회사는 빼고), 목표 배당성향 × 올해 순이익(작년의 SPECIAL_MULT 배 넘으면 일회성 이익이라 뺌), 최소 DPS.
    배당결정으로 주식 수를 알면 총액(원)으로 — 요건이 배당총액 기준이라 자사주 소각으로 주식이 줄면 DPS 는 10% 넘게 올라야 함. 모르면 주당으로.
    분기·중간: 정책의 분기 정규 DPS. 올해 EPS = 컨센, 없으면 3분기 누적×4/3.
    고배당기업 = 최신 밸류업 표 '해당'(없으면 결산 배당결정에서 요건 충족을 밝힘)"""
    plans = ctx["plans"]
    if t != "결산배당":
        q = next((p for p in reversed(plans) if p["q_dps"]), None)
        return q and {"signal": "valueup", "name": "밸류업 정책", "value": q["q_dps"], "basis": f"{iso(q['date'])} {q['title']} 분기 정규 {q['q_dps']:,}원"}
    hd = next((p for p in reversed(plans) if p["high_div"] is not None), None)
    tax = hd["high_div"] if hd else any(r["tax_rule"] for r in ctx["rounds"] if r["type"] == t and r["fy"] < fy)
    po = next((p for p in reversed(plans) if p["payout"]), None)
    mn = next((p for p in reversed(plans) if p["min_dps"]), None)
    y = ctx["years"].get(fy, {})
    c = next((x for x in reversed(ctx["cons"]) if x.get("fy") == str(fy) and x.get("eps")), None)
    eps, eps_txt = (c["eps"], f"컨센 EPS {c['eps']:,}원") if c else (y.get("q3_eps", 0) * 4 / 3, f"3분기 누적 EPS×4/3 {y.get('q3_eps', 0) * 4 / 3:,.0f}원")
    prev = int(ctx["years"].get(fy - 1, {}).get("dps") or sum(int(r["dps"]) for r in ctx["rounds"] if r["fy"] == fy - 1 and r["dps"]))
    if not (tax or po or mn) or not prev:
        return None
    last = next((r for r in reversed(ctx["rounds"]) if r["total"] and r["dps"] and int(r["dps"])), None)
    shares = last["total"] / int(last["dps"]) if last else 0
    scale = shares or 1   # 총액(원) 또는 주당
    paid_total = sum(r["total"] for r in ctx["rounds"] if r["fy"] == fy and r["type"] != t) if shares else paid(ctx, t, fy)
    prev_total = hd["hd_total"] if shares and hd and hd["hd_fy"] == fy - 1 and hd["hd_total"] else prev * scale
    money = (lambda x: f"{x / 1e12:,.2f}조원") if shares else (lambda x: f"{x:,.0f}원")
    ni = eps * scale
    cands = []
    if tax and eps > 0:
        need = max(prev * scale, min(TAX_PAYOUT_HIGH * ni, max(TAX_PAYOUT_LOW * ni, (1 + TAX_GROWTH) * prev_total)))
        if need <= TAX_MAX_JUMP * prev * scale or any(p["fy"] == fy for p in ctx["policies"]):   # 그해 환원 규모를 공시한 회사는 공시 범위가 상한
            cands.append((need, f"고배당기업({iso(hd['date']) + ' 밸류업 표' if hd else '배당결정 공시'}) 요건 최소 — 작년 배당총액 {money(prev_total)}의 1.1배·"
                                f"순이익({eps_txt}) 25% 중 큰 값, 40% 넘지 않게"))
    if po and eps > 0:
        sep = po["payout_sep"] and y.get("q3_ni", 0) > 0 and y.get("q3_ni_sep", 0) > 0
        v = po["payout"] / 100 * ni * (y["q3_ni_sep"] / y["q3_ni"] if sep else 1)
        if v <= SPECIAL_MULT * prev * scale:
            cands.append((v, f"{iso(po['date'])} 목표 배당성향 {po['payout']:g}% × {'별도 ' if sep else ''}순이익({eps_txt})"))
    if mn:
        cands.append((mn["min_dps"] * scale, f"{iso(mn['date'])} 최소 DPS {mn['min_dps']:,}원"))
    if not cands:
        return None
    annual, why = max(cands)
    if annual < prev * scale:   # 정책 숫자는 최소치 — 회사는 그보다 많이 줌(2025: 최소치 그대로면 오차 28%, 작년 바닥이면 14.5%)
        annual, why = prev * scale, f"정책 최소({why})가 작년보다 낮음 → 작년 DPS {prev:,}원 유지"
    rest = annual - paid_total
    unit = next((u for u in (1000, 500, 100, 50, 10, 5) if prev % u == 0 and prev >= 2 * u), 1)
    v = math.ceil(rest / scale / unit) * unit
    return {"signal": "valueup", "name": "밸류업 정책", "value": v,
            "basis": f"{why} → 연간 {money(annual)}" + (f" − 올해 이미 준 {money(paid_total)}" if paid_total else "")
                     + (f", {shares / 1e8:,.2f}억주로 나눔" if shares else "")} if v > 0 else None

def announced(ctx, t, fy, rec, med):
    """회사가 공시로 밝힌 이 사업연도 현금배당 총액(정책 장부 div_policy.json) — 하한 = max(공시 하한, 올해 이미 준 총액 + 분기 정규), 상한 = 공시 상한.
    그 안에서 컨센(연간 컨센 × 주식 수)·밸류업 정책(고배당 요건 등) 중 큰 값을 쓰고 벗어나면 경계, 둘 다 없으면 하한.
    이미 준 총액을 빼고 최근 배당결정의 주식 수(총액 ÷ 주당)로 나눔"""
    pol = next((p for p in reversed(ctx["policies"]) if p["fy"] == fy), None)
    last = next((r for r in reversed(ctx["rounds"]) if r["total"] and r["dps"] and int(r["dps"])), None)
    if t != "결산배당" or not pol or not last:
        return None
    shares = last["total"] / int(last["dps"])
    paid_total = sum(r["total"] for r in ctx["rounds"] if r["fy"] == fy and r["type"] != t)
    lo = max(pol["cash_total"][0] or 0, paid_total + (pol["regular_q"] or 0))
    hi = pol["cash_total"][1] or math.inf
    c = next((x for x in reversed(ctx["cons"]) if x.get("fy") == str(fy) and "dps" in x), None)
    vu = valueup(ctx, t, fy, rec, med)
    want = {k: v for k, v in (("컨센", c and c["dps"] * shares), ("밸류업 정책", vu and vu["value"] * shares + paid_total)) if v}
    pick = max(want, key=want.get) if want else None
    target = min(max(want[pick], lo), hi) if pick else lo
    jo = lambda x: f"{x / 1e12:,.1f}조원"
    return {"signal": "announced", "name": "회사 공시 총액", "value": round((target - paid_total) / shares),
            "basis": f"{iso(pol['date'])} 회사 공시 — {fy} 현금배당 총액 {jo(lo)}~{jo(hi) if hi < math.inf else '상한 없음'} 중 "
                     + (" · ".join(f"{k} {jo(v)}" for k, v in want.items()) + f" 중 큰 {pick}" + (" (범위 밖 → 경계)" if target != want[pick] else "") if pick else "하한")
                     + f" → 올해 이미 준 {jo(paid_total)} 빼고 {shares / 1e8:,.2f}억주로 나눔"}

SIGNALS = [repeat_last, consensus_left, trend, payout_eps, no_cut, earn_up, valueup, announced]
LAW = ["announced", "valueup"]   # 트레이더 순서: 회사가 밝힌 배당 총액(단계 3) > 밸류업 정책(단계 2) — 과거 적중순 경쟁 밖, 있으면 앞

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
    특별 몫 금액은 과거 검증에서 오차를 키워 더하지 않음), 실적 서프라이즈 가능성(트레이더 단계 1: 직전 2개 분기 순이익이 발표 전 컨센 상회)"""
    st = style(ctx, fy)
    y = ctx["years"]
    fin = [round(d["dps"] - d.get("q3_dps", 0)) for f, d in sorted(y.items()) if f < fy and d.get("dps") and "q3_eps" in d]
    prev = [d["q3_eps"] for f, d in y.items() if f < fy and d.get("q3_eps", 0) > 0]
    q3 = y.get(fy, {}).get("q3_eps", 0)
    hist = any(r["special"] for r in ctx["rounds"]) or has_special(fin)
    spike = bool(prev) and q3 > SPECIAL_MULT * statistics.median(prev)
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
               if t == "결산배당" and hist and spike else "")
    cands = {c["signal"]: c for c in (s(ctx, t, fy, rec, med) for s in SIGNALS) if c}
    last = cands.get("repeat_last", {}).get("value", 0)   # 회사는 딱 떨어지게 줌 — 후보를 직전 금액의 단위로
    unit = next((u for u in (1000, 500, 100, 50, 10, 5) if last and last % u == 0 and last >= 2 * u), 1)
    cands = {k: {**c, "value": round(c["value"] / unit) * unit} for k, c in cands.items() if round(c["value"] / unit) * unit > 0}
    pick = next((cands[k] for k in LAW + med.get("priority", {}).get(f"{t}|{st}", [s.__name__ for s in SIGNALS]) if k in cands), None)
    return {"per_share": str(pick["value"]) if pick else "", "amount_basis": pick["basis"] if pick else "금액 이력 없음",
            "amount_style": st, "amount_special": special, "amount_surprise": surprise, "amount_signals": list(cands.values()),
            "amount_cands": " · ".join(f"{c['name']} {c['value']:,}원" for c in cands.values())}
