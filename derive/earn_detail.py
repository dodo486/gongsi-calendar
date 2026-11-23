# -*- coding: utf-8 -*-
"""실적 상세 판정 — 원자료(ingest.earn_facts: 네이버 시총·분기 추이·컨센·뉴스·시세·수급 + DART 잠정실적)를 받아 화면 모델로. 접속 없음"""
import re
from core.krx_cal import iso
from core.fmt import num, pct, signed_pct

# ── 실적 상세 판정 — 화면은 view() 결과를 그대로 그린다 ──
SURPRISE = 10        # 영업익 컨센 대비 ±% (FnGuide 컨벤션)
HIGH_GROWTH = 25     # 컨센 없을 때 영업익 YoY ±%
PRE_RUN = 10         # 발표 전 5거래일 ±%
ONETIME_GAP = 30     # 순이익·영업익 YoY 괴리 %p
MOMENTUM = -10       # 영업익 QoQ 이하면 모멘텀 둔화
ONETIME_WORDS = re.compile(r"일회성|소송|매각|손상|충당금|중단사업|영업외")
FLOW_DAYS = 5
NEWS_MAX = 6
TREND_ACTUAL = 5


def _eok(v):
    return "-" if v in (None, "", "-") else f"{v}억"


def _actual(f):
    """당기 실적 — 원문 숫자를 못 뽑았을 때만 네이버 최근 분기로 대체(숫자는 있는데 라벨만 없으면 바꿔치기 금지)"""
    a, note = dict(f.get("actual") or {}), ""
    if not a.get("label") and num(a.get("op")) is None and num(a.get("sales")) is None:
        qs = [q for q in f.get("quarters", []) if not q["consensus"] and q["sales"] != "-"]
        a = {k: qs[-1][k] for k in ("label", "sales", "op", "np")} if qs else {}
        note = f"※ 원문 자동추출 불가 — 네이버 최근 분기({a['label']}) 기준" if a else ""
    elif not a.get("label"):
        a["label"] = "최근 공시분"
    if a.get("label") and not a.get("growth"):
        qs = f.get("quarters", [])
        idx = next((i for i, q in enumerate(qs) if q["label"] == a["label"]), -1)
        key = qs[idx]["key"] if idx >= 0 else None
        py = next((i for i, q in enumerate(qs) if key and q["key"] == str(int(key) - 100)), -1)
        def mk(k):
            cur = num(a.get(k))
            prev = num(qs[idx - 1][k]) if idx > 0 else None
            prev_yr = num(qs[py][k]) if py >= 0 else None
            return {"cur": cur, "prev": prev, "prev_yr": prev_yr, "qoq": pct(cur, prev, 1), "yoy": pct(cur, prev_yr, 1), "qoq_flag": "", "yoy_flag": ""}
        a["growth"] = {k: mk(k) for k in ("sales", "op", "np")}
    return a, note

def _verdict(a, est):
    ao, eo = num(a.get("op")), num(est.get("op"))
    g = (a.get("growth") or {}).get("op") or {}
    if eo and ao is not None:
        sp = pct(ao, eo, 1)
        base = f"영업익 컨센({_eok(est.get('op'))}) 대비 {signed_pct(sp, 1)}"
        if sp >= SURPRISE:
            return {"title": "🔥 어닝 서프라이즈", "tone": "up", "sub": base}
        if sp <= -SURPRISE:
            return {"title": "❄️ 어닝 쇼크", "tone": "down", "sub": base}
        return {"title": "✅ 컨센 부합", "tone": "ok", "sub": f"{base} (±{SURPRISE}% 이내)"}
    if g.get("yoy_flag"):
        return ({"title": "🔄 흑자전환", "tone": "up", "sub": f"영업익 {g['yoy_flag']} (전년동기 대비)"} if "흑자" in g["yoy_flag"]
                else {"title": f"⚠️ {g['yoy_flag']}", "tone": "down", "sub": "영업익 전년동기 대비"})
    if g.get("yoy") is not None:
        qq = f" · QoQ {signed_pct(g['qoq'], 1)}" if g.get("qoq") is not None else ""
        sub = f"컨센 없음 → 영업익 YoY {signed_pct(g['yoy'], 1)}{qq} 기준"
        if g["yoy"] >= HIGH_GROWTH:
            return {"title": "🔥 고성장", "tone": "up", "sub": sub}
        if g["yoy"] <= -HIGH_GROWTH:
            return {"title": "❄️ 급감", "tone": "down", "sub": sub}
        return {"title": "➖ 무난", "tone": "muted", "sub": sub}
    return {"title": "판정 불가", "tone": "muted", "sub": "컨센·비교 데이터 없음"}

def _num_line(name, val, est_val, g):
    bits = []
    ev, av = num(est_val), num(val)
    if ev and av is not None:
        bits.append(f"컨센 {signed_pct(pct(av, ev, 1), 1)}")
    g = g or {}
    bits += [f"YoY {g.get('yoy_flag') or signed_pct(g.get('yoy'), 1)}", f"QoQ {g.get('qoq_flag') or signed_pct(g.get('qoq'), 1)}"]
    return {"name": name, "value": _eok(val), "detail": " · ".join(bits)}

def _pre_run(f, date):
    ps = f.get("prices") or []
    d = iso(date)
    idx = next((i for i, p in enumerate(ps) if p["date"] < d), -1) if d else -1
    if idx < 0 or idx + 5 >= len(ps):
        return None
    c1, c0 = num(ps[idx]["close"]), num(ps[idx + 5]["close"])
    if not c1 or not c0:
        return None
    r = pct(c1, c0, 1)
    if r >= PRE_RUN:
        return f"⚠️ 선반영 주의 — 발표 전 5거래일 {signed_pct(r, 1)} 급등, 재료소멸 셀온 가능성"
    if r <= -PRE_RUN:
        return f"ℹ️ 발표 전 5거래일 {signed_pct(r, 1)} 조정 — 선반영 부담 낮음"
    return None

def _onetime(a):
    g = a.get("growth") or {}
    op, np_ = g.get("op") or {}, g.get("np") or {}
    oy, ny = op.get("yoy"), np_.get("yoy")
    kw = ONETIME_WORDS.search(a.get("notes") or "")
    if (np_.get("yoy_flag") or "") != (op.get("yoy_flag") or "") or (oy is not None and ny is not None and abs(ny - oy) > ONETIME_GAP):
        return f"⚠️ 일회성 의심 — 순이익 YoY({signed_pct(ny, 1)}) vs 영업익 YoY({signed_pct(oy, 1)}) 괴리" + (f" · 원문 '{kw.group(0)}' 언급" if kw else "") + " → 영업이익 우선 판단"
    if kw:
        return f"ℹ️ 원문에 '{kw.group(0)}' 언급 — 일회성 여부 원문 확인"
    return None

def _momentum(a):
    q = ((a.get("growth") or {}).get("op") or {}).get("qoq")
    return f"⚠️ 모멘텀 둔화 — 영업익 QoQ {signed_pct(q, 1)} (YoY 판정과 별개로 직전 분기 대비 감소)" if q is not None and q <= MOMENTUM else None

def _flow(f, date):
    tr = f.get("trend") or []
    if not tr:
        return None
    if date and any(x["date"] >= date for x in tr):
        tr = sorted((x for x in tr if x["date"] >= date), key=lambda x: x["date"])[:FLOW_DAYS]
        label = f"발표 후 {len(tr)}거래일"
    else:
        tr, label = tr[:FLOW_DAYS], f"최근 {FLOW_DAYS}거래일"
    def total(k):
        vals = [num(x[k]) * num(x["close"]) for x in tr if num(x.get(k)) is not None and num(x.get("close"))]
        return round(sum(vals) / 1e8) if vals else None
    fr, og = total("frgn"), total("org")
    if fr is None and og is None:
        return None
    note = ("쌍끌이 순매수", "up") if (fr or 0) > 0 and (og or 0) > 0 else ("동반 순매도", "down") if (fr or 0) < 0 and (og or 0) < 0 else ("", "")
    won = lambda v: "-" if v is None else f"{'+' if v > 0 else ''}{v:,}억"
    return {"label": label, "frgn": won(fr), "org": won(og), "note": note[0], "tone": note[1]}

def _trend(f, a):
    act = [{k: q[k] for k in ("label", "sales", "op", "np")} for q in f.get("quarters", []) if not q["consensus"] and q["sales"] != "-"]
    if a and a.get("label") and not any(r["label"] == a["label"] for r in act):
        act.append({k: a.get(k) for k in ("label", "sales", "op", "np")})
    act = sorted(act, key=lambda r: r["label"], reverse=True)[:TREND_ACTUAL]
    cons = [{**{k: q[k] for k in ("label", "sales", "op", "np")}, "consensus": True} for q in f.get("quarters", []) if q["consensus"]]
    rows = sorted(act + cons, key=lambda r: r["label"], reverse=True)
    for r in rows:
        s, o = num(r["sales"]), num(r["op"])
        r.update(sales=_eok(r["sales"]), op=_eok(r["op"]), np=_eok(r["np"]),
                 opm=f"{round(o / s * 1000) / 10:g}%" if s and o is not None else "", consensus=r.get("consensus", False))
    return rows

def _news(f, date):
    news = f.get("news") or []
    after = [n for n in news if date and n["date"] >= date]
    pick = after or news
    return {"label": "공시일 이후 뉴스" if after else "최신 뉴스", "items": pick[:NEWS_MAX]}

def view(f, ev=None):
    """실적 상세 화면 모델 — 판정 배지·핵심 수치·경고·수급·추이·뉴스. f = ingest.earn_facts.facts, ev = 그 실적공시(earnings.json 행) 또는 None(예정 발표)"""
    date = (ev or {}).get("date", "")
    out = {"marketCap": f.get("marketCap", ""), "trend": [], "news": _news(f, date)}
    if ev is None:
        out["trend"] = _trend(f, None)
        out["has_consensus"] = any(r["consensus"] for r in out["trend"])
        return out
    a, note = _actual(f)
    est = next((q for q in f.get("quarters", []) if q["consensus"] and q["label"] == a.get("label")), {})
    g = a.get("growth") or {}
    out.update(label=a.get("label", ""), fallback_note=note,
               verdict=_verdict(a, est) if a.get("label") else None,
               nums=[_num_line(n, a.get(k), est.get(k), g.get(k)) for n, k in (("매출액", "sales"), ("영업익", "op"), ("순이익", "np"))] if a.get("label") else [],
               flags=[x for x in (_pre_run(f, date), _onetime(a), _momentum(a)) if x],
               flow=_flow(f, date), trend=_trend(f, a))
    return out

