# -*- coding: utf-8 -*-
"""배당 금액 — 신호마다 후보 하나({signal, value 원, basis}) 또는 None, estimate 가 PRIORITY 순으로 고른다. 날짜는 date"""
from core.krx_cal import iso
from derive.dividend.facts import share_factor


def repeat_last(ctx, t, fy, rec):
    """직전 정기 회차 금액 반복 — 그 뒤 분할·병합은 예상 기준일 주식 기준으로 보정"""
    reg = next((r for r in reversed(ctx["rounds"]) if r["type"] == t and not r["special"] and r["dps"]), None)
    if not reg:
        return None
    adj = share_factor(ctx["splits"], reg["record"], rec)
    return {"signal": "repeat_last", "name": "직전 반복", "value": round(int(reg["dps"]) / adj),
            "basis": f"직전 정기 회차({reg['fy']} Q{reg['q']}) {int(reg['dps']):,}원 반복"
                     + (f" · 그 뒤 {'분할' if adj > 1 else '병합'} 1주→{adj:g}주 보정" if adj != 1 else "")}

def consensus_left(ctx, t, fy, rec):
    """연간 주당배당금 컨센서스 − 같은 사업연도에 이미 정한 배당, 남은 회차 수로 나눔. 12월 결산만(네이버 연도 = 사업연도)"""
    c = next((x for x in reversed(ctx["dps_cons"]) if x["fy"] == str(fy)), None)
    if not c or int(ctx["acc"]) != 12:
        return None
    done = [r for r in ctx["rounds"] if r["fy"] == fy and r["dps"]]
    paid = sum(int(r["dps"]) for r in done)
    left = max(sum(1 for r in ctx["rounds"] if r["fy"] == fy - 1) - len(done), 1)
    v = round((c["dps"] - paid) / left)
    return {"signal": "consensus_left", "name": "컨센", "value": v,
            "basis": f"{iso(c['date'])} 네이버 연간 주당배당금 컨센 {c['dps']:,}원" + (f" − 올해 이미 정한 {paid:,}원" if paid else "")
                     + (f" ÷ 남은 {left}회" if left > 1 else "")} if v > 0 else None

SIGNALS = [repeat_last, consensus_left]
PRIORITY = {t: ["repeat_last", "consensus_left"] for t in ("결산배당", "분기배당", "중간배당")}   # 배당종류별 — verify 신호별 적중률로 정함

def estimate(ctx, t, fy, rec):
    cands = {c["signal"]: c for c in (s(ctx, t, fy, rec) for s in SIGNALS) if c}
    pick = next((cands[k] for k in PRIORITY[t] if k in cands), None)
    return {"per_share": str(pick["value"]) if pick else "", "amount_basis": pick["basis"] if pick else "금액 이력 없음",
            "amount_signals": list(cands.values()), "amount_cands": " · ".join(f"{c['name']} {c['value']:,}원" for c in cands.values())}
