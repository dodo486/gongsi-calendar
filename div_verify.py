# -*- coding: utf-8 -*-
"""배당 검증 — 과거 회차마다 공시 직전 사실(view)로 같은 추정을 돌려 날짜·구분·금액 적중률. 설계 정본 docs/배당예측-워크플로우.md §5

  python div_verify.py   → data/div_verify.json
"""
import os, json, datetime, statistics
from base import DATA_DIR, save_json
from krx_cal import t_minus
from div_collect import D, _d, _iso, _load
from div_facts import view, medians, contexts
from div_date import GRADES, GROUP, predict_round
import div_amount

VERIFY_PATH = os.path.join(DATA_DIR, "div_verify.json")
EXP_PATH = os.path.join(DATA_DIR, "expiries.json")


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
    today = D.today()
    ctxs = contexts()
    med = medians(ctxs)
    exps = sorted(_d(e["date"]) for e in _load(EXP_PATH, {}).get("events", []) if e.get("type") == "선·옵 동시만기")
    month = lambda ex: next((x for x in exps if x >= ex), None)
    rows = []
    for c in ctxs:
        for r in c["rounds"]:
            if r["src"] != "공시" or not r["decided"] or r["record"] < today - datetime.timedelta(days=400):
                continue
            cut = min([r["decided"]] + [n["date"] for n in c["rec_notices"] if n["record"] == r["record"]])
            v = view(c, cut)
            p, miss = predict_round(v, r["type"], r["fy"], r["q"], cut, [], med)
            base = {"stock": c["stock"], "corp": c["corp"], "type": r["type"], "quarter": f"{r['fy']} Q{r['q']}",
                    "actual_record": _iso(r["record"]), "actual_fixed": r["fixed"]}
            if p is None:
                rows.append({**base, "pred": "미실시 판정", "note": miss})
                continue
            prec = _d(p["record_date"])
            pex, aex = _d(t_minus(_iso(prec), 1)), _d(t_minus(_iso(r["record"]), 1))
            rows.append({**base, "pred": p["method"], "grade": p["grade"], "pred_record": p["record_date"], "alt": p["alt"],
                         "err": abs((prec - r["record"]).days),
                         "err_alt": {k: abs((_d(x) - r["record"]).days) for k, x in p["alt"].items()},
                         "month_ok": month(pex) == month(aex), "cond": p["cond"],
                         "pred_dps": int(p["per_share"]) if p["per_share"] else None, "actual_dps": int(r["dps"]) if r["dps"] else None,
                         "actual_special": r["special"], "sig_dps": {c["signal"]: c["value"] for c in p["amount_signals"]},
                         "charter": (c["charter"].get(GROUP[r["type"]]) or {}).get("v", ""),
                         "prev_fixed": next((x["fixed"] for x in reversed(v["rounds"]) if x["type"] == r["type"]), None)})
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
        "금액": {t or "전체": amount_hits([x for x in rows if x.get("pred_dps") and x.get("actual_dps") and (not t or x["type"] == t)])
                 for t in ("", "결산배당", "분기배당", "중간배당")},
        "신호별 금액": {sg.__name__: amount_hits([{**x, "pred_dps": x["sig_dps"][sg.__name__]} for x in rows
                                                 if sg.__name__ in x.get("sig_dps", {}) and x.get("actual_dps")]) for sg in div_amount.SIGNALS},
        "한계": "정관(정기보고서 표)은 최신값 사용 — 과거 시점 값이 아님. 실적발표 예정일 과거분 없음 → 백테스트에서 A2 이사회형 미사용",
    }
    save_json(VERIFY_PATH, {"generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "summary": summary, "rows": rows})
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    wrong = [x for x in judged if (x["pred"] == "고정") != x["actual_fixed"]]
    print(f"\n판정 틀린 회차 {len(wrong)}건 (앞 20):")
    for x in wrong[:20]:
        print(f"  {x['corp']} {x['quarter']} {x['type']} 예측 {x['pred']} · 실제 기준일 {x['actual_record']}")
    return summary


if __name__ == "__main__":
    verify()
