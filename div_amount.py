# -*- coding: utf-8 -*-
"""배당 3단계 금액 — 신호마다 후보 하나({signal, value 원, basis}) 또는 None, estimate 가 PRIORITY 순으로 고른다. 날짜는 div_date"""
from div_facts import share_factor


def repeat_last(ctx, t, rec):
    """직전 정기 회차 금액 반복 — 그 뒤 분할·병합은 예상 기준일 주식 기준으로 보정"""
    reg = next((r for r in reversed(ctx["rounds"]) if r["type"] == t and not r["special"] and r["dps"]), None)
    if not reg:
        return None
    adj = share_factor(ctx["splits"], reg["record"], rec)
    return {"signal": "repeat_last", "value": round(int(reg["dps"]) / adj),
            "basis": f"직전 정기 회차({reg['fy']} Q{reg['q']}) {int(reg['dps']):,}원 반복"
                     + (f" · 그 뒤 {'분할' if adj > 1 else '병합'} 1주→{adj:g}주 보정" if adj != 1 else "")}

SIGNALS = [repeat_last]
PRIORITY = {t: ["repeat_last"] for t in ("결산배당", "분기배당", "중간배당")}   # 배당종류별 — div_verify 신호별 적중률로 정함

def estimate(ctx, t, rec):
    cands = {c["signal"]: c for c in (s(ctx, t, rec) for s in SIGNALS) if c}
    pick = next((cands[k] for k in PRIORITY[t] if k in cands), None)
    return {"per_share": str(pick["value"]) if pick else "", "amount_basis": pick["basis"] if pick else "금액 이력 없음",
            "amount_signals": list(cands.values())}
