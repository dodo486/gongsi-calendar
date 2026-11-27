# -*- coding: utf-8 -*-
"""배당 예측 — 재료(inputs)로 날짜(date)·금액(amount)을 따로 구해 회차 한 줄로 붙인다. 날짜와 금액은 서로 모르고 여기서만 만난다. 장부·검증이 같이 씀"""
import datetime
from core.krx_cal import to_date
from derive.dividend import inputs, date, amount
from derive.dividend.facts import quarter_end, quarter_owner

HORIZON_DAYS = 270


def learn(ctxs, cut):
    """수집대상 전체 통계 — 날짜 기본 간격(date.learn) · 금액 증가율(amount.learn), cut 전에 알던 것만"""
    s = inputs.samples([inputs.view(c, cut) for c in ctxs])
    return {"date": date.learn(s), "amount": amount.learn(s)}

def one(ctx, t, fy, q, today, med):
    """한 회차 → (날짜 예측 + 금액 + 두 재료표, None) 또는 미실시면 (None, 사유). 금액 재료의 주식 수·분할 보정은 예상 기준일 기준"""
    d = inputs.for_date(ctx, t, fy, q)
    p, miss = date.predict(d["v"], today, med["date"])
    if miss:
        return None, miss
    a = inputs.for_amount(ctx, fy, q, to_date(p["record_date"]), today)
    return {**p, "date_inputs": d["table"], **amount.estimate(a["v"], t, med["amount"]), "amount_inputs": a["table"]}, None

def upcoming(ctx, today, med):
    """앞으로 올 배당 건 — 과거 회차가 맡은 분기마다, 그리고 결산 회차가 없어도 그 사업연도 결산 근거(작년 결산·회사 정책·컨센 DPS)가 있으면 결산.
    결산기말·근거 날짜가 지났는데 공시가 없으면 미실시(missed)"""
    by_type, owner = quarter_owner(ctx["rounds"])
    observed = {(r["type"], r["fy"], r["q"]) for r in ctx["rounds"]}
    out, missed = [], []
    for q, t in {**owner, 4: owner.get(4, "결산배당")}.items():
        last_qe = max((r["qe"] for r in by_type.get(t, [])), default=datetime.date.min)
        for fy in (today.year - 1, today.year, today.year + 1):
            qe = quarter_end(fy, q, ctx["acc"])
            if qe <= last_qe or (t, fy, q) in observed or (t not in by_type and not inputs.evidence(ctx, fy)):
                continue
            p, miss = one(ctx, t, fy, q, today, med)
            if miss:
                missed.append(miss)
            elif today < to_date(p["record_date"]) <= today + datetime.timedelta(days=HORIZON_DAYS):
                out.append(p)
    return out, missed
