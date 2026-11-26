# -*- coding: utf-8 -*-
"""배당금 재료표 — 회사 × 사업연도마다 DPS 를 구하는 재료를 한 번 모은다. 금액(amount)은 이 표만 읽고, 사람·하네스는 같은 표로 확인한다.

build(ctx, fy, rec) → {"v": 계산용 값, "table": 사람용 줄}
  table 한 줄 = {item 재료 이름, value 값 글, status 있음·없음·모름·의심·안 함, check 자동·판독자 확인·판독자 다름·사람 확인, src 출처, sig 판정 열쇠}
  상태가 있음이 아니거나 회사 정책이 아직 사람 확인 전이면 확인 목록(review)에 오른다.
USES = 금액 신호마다 읽는 재료(확인 목록이 "이 금액에 쓰인 재료"를 고를 때)"""
import datetime
from core.krx_cal import iso
from derive.dividend.facts import (share_factor, paid, annual_dps, final_of, last_annual, shares, cash_share, cons_of, eps_of, policy,
                                   evidence, quarter_end, year_steps, seen)


USES = {"repeat_last": ("작년 결산 몫", "올해 이미 정한 배당"), "consensus_left": ("연간 컨센 DPS", "올해 이미 정한 배당"),
        "trend": ("작년 연간 DPS", "올해 이미 정한 배당"), "no_cut": ("작년 연간 DPS", "과거 배당성향(3년)", "올해 순이익(EPS)", "올해 이미 정한 배당"),
        "payout_eps": ("과거 배당성향(3년)", "올해 순이익(EPS)", "올해 이미 정한 배당"), "earn_up": ("작년 결산 몫",),
        "step_up": (), "stated": ("회사 정책", "올해 순이익(EPS)", "주식 수", "현금 몫(총환원 중 배당)", "작년 연간 DPS", "올해 이미 정한 배당")}

OVERRIDE = {"올해 순이익(EPS)": ("eps",), "작년 연간 DPS": ("prev", "prev_adj"), "작년 결산 몫": ("last_final",), "올해 이미 정한 배당": ("paid",),
            "주식 수": ("shares",), "연간 컨센 DPS": ("cons_dps",), "현금 몫(총환원 중 배당)": ("share",)}   # 사람이 고칠 수 있는 재료 → 계산용 값 칸

def policy_text(rows):
    """정책 줄 → 사람이 읽는 한 줄 (재료표·확인 목록·참고 공시 탭·화면)"""
    unit = {"dps": "원", "cash": "원", "budget": "원", "buyback": "원", "payout": "%", "return": "%"}
    name = {"dps": "주당", "cash": "현금배당 총액", "budget": "총주주환원(자사주 포함)", "buyback": "자사주 매입",
            "payout": "배당성향", "return": "총주주환원율(자사주 포함)"}
    out = []
    for r in rows or []:
        if r["kind"] == "high_div":
            out.append(f"고배당기업 {'해당' if r['on'] else '미해당'}")
            continue
        v = lambda x: (f"{x:,g}" if unit[r["kind"]] == "%" or r["kind"] == "dps" else f"{x / 1e12:,g}조" if x >= 1e12 else f"{x / 1e8:,.0f}억") + unit[r["kind"]]
        rng = (f"{v(r['lo'])}" if r["lo"] == r["hi"] else f"{v(r['lo'])} 이상" if r["hi"] is None else f"{v(r['hi'])} 이하" if r["lo"] is None
               else f"{v(r['lo'])}~{v(r['hi'])}")
        fy = r["fy"] if isinstance(r["fy"], list) else [None, None]
        out.append(f"{'' if r['slot'] == '연간' else r['slot'] + ' '}{name[r['kind']]} {rng}"
                   + ("" if not any(fy) else f" ({fy[0]})" if fy[0] == fy[1] else f" ({fy[0] or ''}~{fy[1] or ''})"))
    return " · ".join(out)

def run_dps(ctx, fy):
    """직전 사업연도부터 거슬러 끊김 없이 이어진 연간 DPS (오래된 것부터)"""
    ys = []
    while annual_dps(ctx, fy - 1 - len(ys)):
        ys.insert(0, annual_dps(ctx, fy - 1 - len(ys)))
    return ys

def build(ctx, fy, rec):
    y, yp = ctx["years"].get(fy, {}), ctx["years"].get(fy - 1, {})
    sh = shares(ctx, rec)
    eps, eps_txt = eps_of(ctx, fy)
    pol = policy(ctx, fy)
    share, share_txt = cash_share(ctx, fy)
    pays = [ctx["years"][fy - i]["payout"] for i in (1, 2, 3) if ctx["years"].get(fy - i, {}).get("payout", 0) > 0]
    c_dps, c_eps = cons_of(ctx, fy, "dps"), cons_of(ctx, fy, "eps")
    last = final_of(ctx, fy - 1)
    p = paid(ctx, fy)
    rounds = [{**r, "adj": share_factor(ctx["splits"], r["record"], rec)} for r in ctx["rounds"]]
    v = {"fy": fy, "rounds": rounds, "acc": int(ctx["acc"]),
         "eps": eps, "eps_txt": eps_txt, "sep_eps": y["q3_ni_sep"] * 1e6 / sh if y.get("q3_ni_sep", 0) > 0 and sh else 0,
         "pays": pays, "pay_last": yp.get("payout", 0),
         "q3": y.get("q3_eps", 0), "q3_prev": yp.get("q3_eps", 0),
         "q3_hist": [d["q3_eps"] for f, d in ctx["years"].items() if f < fy and d.get("q3_eps", 0) > 0],
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
    q3_paid = y.get("q3_dps") if "q3_eps" in y and seen(ctx, fy, 1) else None
    rounds_paid = sum(int(r["dps"]) for r in ctx["rounds"] if r["fy"] == fy and r["type"] != "결산배당" and r["dps"])
    def row(item, value, status, src="", check="자동", sig=""):
        sig = sig or f"{item}|{value}"   # 값이 바뀌면 열쇠도 바뀌어 다시 확인
        man = item in fixed or sig in ctx["human_sigs"]
        return {"item": item, "value": value, "status": "있음" if item in fixed else status, "check": "사람 확인" if man else check,
                "src": "사람(확인 목록)" if item in fixed else src, "sig": sig}
    c_dps, eps, sh, share = v["cons_dps"], v["eps"], v["shares"], v["share"]
    table = [
        row("올해 순이익(EPS)", f"{eps:,.0f}원" if eps > 0 else "", "있음" if eps > 0 else "없음", v["eps_txt"]),
        row("과거 배당성향(3년)", " · ".join(f"{x:g}%" for x in pays), "있음" if len(pays) == 3 else "모름" if pays else "없음", "사업보고서"),
        row("작년 연간 DPS", f"{v['prev']:,.0f}원" if v["prev"] else "", "있음" if v["prev"] else "없음", "사업보고서·공시 회차"),
        row("작년 결산 몫", f"{v['last_final']:,.0f}원" if v["last_final"] else "", "있음" if v["last_final"] else "안 함" if v["last_final"] == 0 else "모름",
            "결산 공시 회차·사업보고서"),
        row("올해 이미 정한 배당", f"{v['paid']:,.0f}원", "의심" if q3_paid is not None and abs(q3_paid - rounds_paid) >= 1 else "있음",
            f"공시 회차 {rounds_paid:,}원" + (f" · 3분기보고서 {q3_paid:,.0f}원" if q3_paid is not None else "")),
        row("주식 수", f"{sh / 1e8:,.2f}억주" if sh else "", "있음" if sh else "없음", "배당결정 총액÷주당·반기/사업보고서·자사주 변동"),
        row("연간 컨센 DPS", f"{c_dps['dps']:,.0f}원" if c_dps else "", "있음" if c_dps else "없음", f"네이버 {iso(c_dps['date'])}" if c_dps else "네이버"),
    ]
    if any(k[0] in ("return", "budget") for k in pol):
        table.append(row("현금 몫(총환원 중 배당)", f"{share:.0%}" if share is not None else "", "있음" if share is not None else "없음", v["share_txt"]))
    for r in pol.values():
        table.append({**row("회사 정책", policy_text([r]), "의심" if r["check"] == "판독자 다름" else "있음",
                            f"{iso(r['date'])} {r.get('title', '')} — {r.get('text', '')[:80]}", r["check"], r["sig"]),
                      **({"fix": {k: r["fix"][k] for k in ("kind", "slot", "lo", "hi", "fy")}} if r.get("fix") else {})})   # 판독자가 고쳐 읽은 값
    return {"v": v, "table": table}
