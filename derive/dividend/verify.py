# -*- coding: utf-8 -*-
"""배당 검증 — 과거 회차마다 공시 직전 사실(view)로 같은 추정을 돌려 날짜·구분·금액(분기·중간) 적중률.
결산배당은 사업연도마다 12월 초(결산기말 EVAL_DAYS 전) 시점 사실로 장부와 같은 예측(predict.upcoming)을 돌려 실제 결산 몫과 비교 — 예측 건이 안 나오면 놓침.
금액은 장부와 같은 고르기(amount.pick)로 신호 값에서 고른다. 설계 정본 docs/배당예측-워크플로우.md §5

  python -m derive.dividend.verify   → data/out/div_verify.json
"""
import json, datetime, statistics
from core.paths import save_json, load_json, stamp, DIV_VERIFY_PATH, EXPIRIES_PATH
from core.krx_cal import t_minus, to_date, iso
from derive.dividend.facts import quarter_end, final_of, spiked, GROUP
from derive.dividend.inputs import contexts, view
from derive.dividend import predict
from derive.dividend.date import GRADES
from derive.dividend import amount

EVAL_DAYS = 30

def amount_hits(xs):
    """예상 금액 적중 — 정확히 같음 · ±5% · ±20% · 오차 중앙값 (특별배당 섞인 실제 회차는 따로 셈)"""
    if not xs:
        return {}
    err = [abs(x["pred_dps"] - x["actual_dps"]) / x["actual_dps"] * 100 for x in xs]
    pct = lambda k: f"{k}/{len(xs)} ({k / len(xs) * 100:.0f}%)"
    return {"같음": pct(sum(x["pred_dps"] == x["actual_dps"] for x in xs)), "±5%": pct(sum(e <= 5 for e in err)),
            "±20%": pct(sum(e <= 20 for e in err)), "오차 중앙값(%)": round(statistics.median(err), 1),
            "특별배당 섞인 회차": sum(x["actual_special"] for x in xs)}

def verify():
    today = datetime.date.today()
    ctxs = contexts()
    med = predict.learn(ctxs)
    exps = sorted(to_date(e["date"]) for e in load_json(EXPIRIES_PATH, {}).get("events", []) if e.get("type") == "선·옵 동시만기")
    month = lambda ex: next((x for x in exps if x >= ex), None)
    rows = []
    for c in ctxs:
        for r in c["rounds"]:
            if r["src"] != "공시" or not r["decided"] or r["record"] < today - datetime.timedelta(days=400):
                continue
            cut = min([r["decided"]] + [n["date"] for n in c["rec_notices"] if n["record"] == r["record"]])
            v = view(c, cut)
            p, miss = predict.one(v, r["type"], r["fy"], r["q"], cut, med)
            base = {"code": c["code"], "name": c["name"], "type": r["type"], "quarter": f"{r['fy']} Q{r['q']}",
                    "actual_record": iso(r["record"]), "actual_fixed": r["fixed"]}
            if p is None:
                rows.append({**base, "pred": "미실시 판정", "note": miss})
                continue
            prec = to_date(p["record_date"])
            pex, aex = to_date(t_minus(iso(prec), 1)), to_date(t_minus(iso(r["record"]), 1))
            rows.append({**base, "pred": p["method"], "grade": p["grade"], "pred_record": p["record_date"], "alt": p["alt"],
                         "err": abs((prec - r["record"]).days),
                         "err_alt": {k: abs((to_date(x) - r["record"]).days) for k, x in p["alt"].items()},
                         "month_ok": month(pex) == month(aex), "cond": p["cond"],
                         "style": p["amount_style"], "actual_dps": int(r["dps"]) if r["dps"] else None,
                         "actual_special": r["special"], "sig_dps": {c["signal"]: c["value"] for c in p["amount_signals"]},
                         "charter": (c["charter"].get(GROUP[r["type"]]) or {}).get("v", ""),
                         "prev_fixed": next((x["fixed"] for x in reversed(v["rounds"]) if x["type"] == r["type"]), None)})
    finals, missed = [], []
    for c in ctxs:
        done = {fy: v for fy in c["years"] if (v := final_of(c, fy))}
        for fy, act in sorted(done.items()):
            cut = quarter_end(fy, 4, c["acc"]) - datetime.timedelta(days=EVAL_DAYS)
            known = any(r["type"] == "결산배당" and r["fy"] == fy and r["decided"] and r["decided"] < cut for r in c["rounds"])
            if cut + datetime.timedelta(days=EVAL_DAYS) >= today or known or fy - 1 not in c["years"]:
                continue   # 아직 안 끝난 해, 12월 초 전에 이미 공시한 결산, 작년 사업보고서가 없는 첫 해는 예측 대상이 아님
            e = next((p for p in predict.upcoming(view(c, cut), cut, med)[0] if p["div_type"] == "결산배당" and p["fy"] == fy), None)
            if not e:
                missed.append({"code": c["code"], "name": c["name"], "fy": fy, "actual_dps": act})
                continue
            prior = [v for y, v in done.items() if y < fy]
            finals.append({"code": c["code"], "name": c["name"], "type": "결산배당", "fy": fy, "style": e["amount_style"], "actual_dps": act,
                           "actual_special": spiked(prior, act), "special_hint": bool(e["amount_special"]),
                           "sig_dps": {s["signal"]: s["value"] for s in e["amount_signals"]}})
    styles = ("", "안정", "연동", "이력 짧음")
    for x in rows + finals:
        if "sig_dps" in x:
            k = amount.pick(x["sig_dps"])
            x["pred_dps"] = x["sig_dps"][k] if k else None
    judged = [x for x in rows if x["pred"] in ("고정", "유동")]
    ok = [x for x in judged if (x["pred"] == "고정") == x["actual_fixed"]]
    flo = [x for x in rows if x["pred"] == "유동" and not x["actual_fixed"]]
    pct = lambda a, b: f"{a}/{b} ({a / b * 100:.0f}%)" if b else "0/0"
    medv = lambda xs: int(statistics.median(xs)) if xs else None
    cond = {}
    for k in ("D13", "D14"):
        base = [x for x in rows if (x.get("cond") or {}).get(k) is not None and x["charter"] == "O" and x["prev_fixed"]]
        on = [x for x in base if x["cond"][k]]
        cond[k] = {"대상": len(base), "켜짐": len(on), "켜짐 중 실제 유동": sum(1 for x in on if not x["actual_fixed"]),
                   "꺼짐 중 실제 유동": sum(1 for x in base if not x["cond"][k] and not x["actual_fixed"])}
    summary = {
        "회차": len(rows),
        "판정 적중(고정/유동)": pct(len(ok), len(judged)),
        "판정 내역": {m: sum(1 for x in rows if x["pred"] == m) for m in ("고정", "유동", "유동 가능", "판별 불가", "확정 기준일", "미실시 판정")},
        "구분 내역": {k: sum(1 for x in rows if x.get("grade") == k) for k in GRADES},
        "유동 기준일 오차 중앙값(일)": medv([x["err"] for x in flo]),
        "A1 오차 중앙값(일)": medv([x["err_alt"]["A1"] for x in flo if "A1" in x["err_alt"]]),
        "A2 오차 중앙값(일)": medv([x["err_alt"]["A2"] for x in flo if "A2" in x["err_alt"]]),
        "유동 기준일 ±7일 이내": pct(sum(1 for x in flo if x["err"] <= 7), len(flo)),
        "월물 적중": pct(sum(1 for x in rows if x.get("month_ok")), sum(1 for x in rows if "month_ok" in x)),
        "조건부 신호(정관 O · 직전 고정)": cond,
        "금액(분기·중간, 공시 직전)": {t: amount_hits([x for x in rows if x.get("pred_dps") and x.get("actual_dps") and x["type"] == t])
                                    for t in ("분기배당", "중간배당")},
        "결산 놓침(12월 초 예측 건 없음)": f"{len(missed)}/{len(missed) + len(finals)}",
        "결산 금액(12월 초)": {s or "전체": amount_hits([x for x in finals if x["pred_dps"] and (not s or x["style"] == s)]) for s in styles},
        "결산 신호별 금액(12월 초)": {s or "전체": {n: amount_hits([{**x, "pred_dps": x["sig_dps"][n]} for x in finals if n in x["sig_dps"] and (not s or x["style"] == s)])
                                                for n in amount.NAMES} for s in styles},
        "특별배당 가능성 표시(12월 초)": {"켜진 회차": sum(x["special_hint"] for x in finals),
                                     "그중 실제 특별": sum(x["special_hint"] and x["actual_special"] for x in finals),
                                     "꺼진 회차 중 실제 특별": sum(not x["special_hint"] and x["actual_special"] for x in finals)},
        "결산 표본 사업연도": {fy: sum(x["fy"] == fy for x in finals) for fy in sorted({x["fy"] for x in finals})},
        "한계": "정관(정기보고서 표)은 최신값 사용 — 과거 시점 값이 아님. 실적발표 예정일은 최근 수집분만 → 백테스트에서 A2 이사회형은 거의 안 쓰임",
    }
    save_json(DIV_VERIFY_PATH, {"generated_at": stamp(), "summary": summary, "rows": rows, "finals": finals, "missed": missed})
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    wrong = [x for x in judged if (x["pred"] == "고정") != x["actual_fixed"]]
    print(f"\n판정 틀린 회차 {len(wrong)}건 (앞 20):")
    for x in wrong[:20]:
        print(f"  {x['name']} {x['quarter']} {x['type']} 예측 {x['pred']} · 실제 기준일 {x['actual_record']}")
    return summary


if __name__ == "__main__":
    verify()
