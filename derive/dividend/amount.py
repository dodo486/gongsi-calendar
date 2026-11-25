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
    c = next((x for x in reversed(ctx["dps_cons"]) if x["fy"] == str(fy)), None)
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

def policy(ctx, t, fy, rec, med):
    """밸류업·지배구조보고서의 배당정책 — 직전 반복을 바닥으로, 연간 최소 주당배당금 · 목표 배당성향×올해 EPS(3분기 누적×4/3, 별도 기준이면 별도÷연결 순이익 비율로) 중 큰 값"""
    mn = next((p for p in reversed(ctx["plans"]) if p["min_dps"]), None)
    po = next((p for p in reversed(ctx["plans"]) if p["payout"]), None)
    y = ctx["years"].get(fy, {})
    sep = po and po["payout_sep"] and y.get("q3_ni", 0) > 0 and y.get("q3_ni_sep", 0) > 0
    eps = y.get("q3_eps", 0) * 4 / 3 * (y["q3_ni_sep"] / y["q3_ni"] if sep else 1)
    if t != "결산배당" or not (mn or po):
        return None
    cands = [c for c in (repeat_last(ctx, t, fy, rec, med),
                         mn and from_annual(ctx, t, fy, "policy", "정책", mn["min_dps"], f"{iso(mn['date'])} {mn['title']} 연간 최소 {mn['min_dps']:,}원"),
                         po and eps > 0 and from_annual(ctx, t, fy, "policy", "정책", po["payout"] / 100 * eps,
                                                        f"{iso(po['date'])} {po['title']} 목표 배당성향 {po['payout']:g}% × 올해 {'별도 ' if sep else ''}EPS {eps:,.0f}원")) if c]
    return {**max(cands, key=lambda c: c["value"]), "signal": "policy", "name": "정책"} if cands else None

TAX_PAYOUT_HIGH, TAX_PAYOUT_LOW, TAX_GROWTH = 0.40, 0.25, 0.10   # 조특법 §104의27 고배당기업: 배당성향 40% 이상, 또는 25% 이상 + 배당 전년 대비 10% 증가
TAX_MAX_JUMP = 2   # 요건 맞추는 데 작년의 이 배수 넘게 필요하면(일회성 이익) 포기한다고 봄 — 2025 요건 충족 회사들의 필요 인상은 최대 1.48배

def tax_rule(ctx, t, fy, rec, med):
    """고배당기업 과세특례 요건을 맞추는 최소 연간 DPS — 40% 경로와 25%+10% 경로 중 싼 쪽, 작년보다 적게는 안 줌, 딱 떨어지는 단위로 올림.
    결산 공시에서 요건 충족을 밝힌 회사만(그 공시 회차 이후). 연간 EPS 모르면 3분기 누적×4/3. 작년의 TAX_MAX_JUMP 배 넘게 필요하면 없음"""
    last = next((r for r in reversed(ctx["rounds"]) if r["type"] == "결산배당" and r["tax_rule"]), None)
    y = ctx["years"].get(fy, {})
    prev = int(ctx["years"].get(fy - 1, {}).get("dps") or sum(int(r["dps"]) for r in ctx["rounds"] if r["fy"] == fy - 1 and r["dps"]))
    eps = y.get("eps") or y.get("q3_eps", 0) * 4 / 3
    if t != "결산배당" or not last or last["fy"] >= fy or not prev or eps <= 0:
        return None
    unit = next((u for u in (1000, 500, 100, 50, 10, 5) if prev % u == 0 and prev >= 2 * u), 1)
    annual = math.ceil(max(prev, min(TAX_PAYOUT_HIGH * eps, max(TAX_PAYOUT_LOW * eps, (1 + TAX_GROWTH) * prev))) / unit) * unit
    if annual > TAX_MAX_JUMP * prev:
        return None
    return from_annual(ctx, t, fy, "tax_rule", "고배당 요건", annual,
                       f"{last['fy']} 결산 공시에서 고배당기업 과세특례 요건 충족 밝힘 → 배당성향 40%, 또는 25% + 전년 대비 10% 증가의 최소"
                       f" (작년 연간 {prev:,}원, 올해 EPS {eps:,.0f}원) → 연간 {annual:,}원")

def announced(ctx, t, fy, rec, med):
    """회사가 공시로 밝힌 이 사업연도 현금배당 총액(정책 장부 div_policy.json) — 하한 = max(공시 하한, 올해 이미 준 총액 + 분기 정규), 상한 = 공시 상한.
    그 안에서 컨센(연간 컨센 × 주식 수)을 쓰고 벗어나면 경계, 컨센 없으면 하한. 이미 준 총액을 빼고 최근 배당결정의 주식 수(총액 ÷ 주당)로 나눔"""
    pol = next((p for p in reversed(ctx["policies"]) if p["fy"] == fy), None)
    last = next((r for r in reversed(ctx["rounds"]) if r["total"] and r["dps"] and int(r["dps"])), None)
    if t != "결산배당" or not pol or not last:
        return None
    shares = last["total"] / int(last["dps"])
    paid_total = sum(r["total"] for r in ctx["rounds"] if r["fy"] == fy and r["type"] != t)
    lo = max(pol["cash_total"][0] or 0, paid_total + (pol["regular_q"] or 0))
    hi = pol["cash_total"][1] or math.inf
    c = next((x for x in reversed(ctx["dps_cons"]) if x["fy"] == str(fy)), None)
    target = min(max(c["dps"] * shares, lo), hi) if c else lo
    jo = lambda x: f"{x / 1e12:,.1f}조원"
    return {"signal": "announced", "name": "회사 공시 총액", "value": round((target - paid_total) / shares),
            "basis": f"{iso(pol['date'])} 회사 공시 — {fy} 현금배당 총액 {jo(lo)}~{jo(hi) if hi < math.inf else '상한 없음'} 중 "
                     + (f"컨센 {c['dps']:,}원×{shares / 1e8:,.2f}억주 = {jo(c['dps'] * shares)}" + (" (범위 밖 → 경계)" if target != c["dps"] * shares else "") if c else "하한")
                     + f" → 올해 이미 준 {jo(paid_total)} 빼고 {shares / 1e8:,.2f}억주로 나눔"}

SIGNALS = [repeat_last, consensus_left, trend, payout_eps, no_cut, earn_up, policy, tax_rule, announced]
LAW = ["announced", "tax_rule"]   # 회사가 공시로 밝힌 총액 · 법이 정한 공식(대상은 회사가 공시로 밝힘) — 과거 적중순 경쟁 밖, 있으면 맨 앞

def rank(xs):
    """신호 순서 — 과거 회차(xs: sig_dps·actual_dps)에 이 순서로 처음 있는 신호를 썼다면의 평균 오차(회차마다 100%에서 자름)가
    가장 작은 순서 — 중앙값은 가운데 몇 회차만 봐서 소수 회차로 순서가 우연히 뒤집힘. 튄 회차도 넣음(대부분 실제 인상).
    표본 MIN_SAMPLES 미만 신호는 검증 전이라 SIGNALS 순으로 뒤에"""
    tested = [s.__name__ for s in SIGNALS if s.__name__ not in LAW and sum(s.__name__ in x["sig_dps"] for x in xs) >= MIN_SAMPLES]
    errs = lambda order: [abs(x["sig_dps"][k] - x["actual_dps"]) / x["actual_dps"] for x in xs if (k := next((k for k in order if k in x["sig_dps"]), None))]
    best = min(itertools.permutations(tested), key=lambda o: statistics.mean(min(v, 1) for v in errs(o))) if tested else ()
    return list(best) + [s.__name__ for s in SIGNALS if s.__name__ not in best and s.__name__ not in LAW]

def estimate(ctx, t, fy, rec, med):
    """금액 하나(신호 순서대로 처음 있는 것) + 특별배당 가능성 표시 — 결산에서 특별배당 이력(M17)과 올해 이익 튐(M15)이 같이 켜지면.
    특별 몫 금액은 과거 검증에서 오차를 키워 금액에 더하지 않음"""
    st = style(ctx, fy)
    y = ctx["years"]
    fin = [round(d["dps"] - d.get("q3_dps", 0)) for f, d in sorted(y.items()) if f < fy and d.get("dps") and "q3_eps" in d]
    prev = [d["q3_eps"] for f, d in y.items() if f < fy and d.get("q3_eps", 0) > 0]
    q3 = y.get(fy, {}).get("q3_eps", 0)
    hist = any(r["special"] for r in ctx["rounds"]) or has_special(fin)
    spike = bool(prev) and q3 > SPECIAL_MULT * statistics.median(prev)
    special = (f"특별배당 가능성 — 특별배당 준 적 있음 + 올해 3분기 누적 EPS {q3:,.0f}원이 평소({statistics.median(prev):,.0f}원)의 {SPECIAL_MULT}배 넘음"
               if t == "결산배당" and hist and spike else "")
    cands = {c["signal"]: c for c in (s(ctx, t, fy, rec, med) for s in SIGNALS) if c}
    last = cands.get("repeat_last", {}).get("value", 0)   # 회사는 딱 떨어지게 줌 — 후보를 직전 금액의 단위로
    unit = next((u for u in (1000, 500, 100, 50, 10, 5) if last and last % u == 0 and last >= 2 * u), 1)
    cands = {k: {**c, "value": round(c["value"] / unit) * unit} for k, c in cands.items() if round(c["value"] / unit) * unit > 0}
    pick = next((cands[k] for k in LAW + med.get("priority", {}).get(f"{t}|{st}", [s.__name__ for s in SIGNALS]) if k in cands), None)
    return {"per_share": str(pick["value"]) if pick else "", "amount_basis": pick["basis"] if pick else "금액 이력 없음",
            "amount_style": st, "amount_special": special, "amount_signals": list(cands.values()),
            "amount_cands": " · ".join(f"{c['name']} {c['value']:,}원" for c in cands.values())}
