# -*- coding: utf-8 -*-
"""실적 상세 데이터 — 컨센서스 전망 + 최근 분기 추이 + 당기 실적
- 네이버 모바일 API: 시가총액·현재가, 분기 매출/영업이익/순이익(억원), 컨센서스(E) 컬럼
- DART 잠정실적 원문: 방금 공시된 당기 실적 (네이버 반영 전 공백 커버, 백만원→억 환산)
- data/facts/{code}.json 캐시(30분) — serve.py 의 /api/earnfacts 가 호출
사용: python -m derive.earn_detail 003570 [rcept_no]   # 단독 테스트
"""
import html as _html
import json, os, re, sys, time
from core.paths import DATA_DIR, save_json
from ingest.sources.naver import get as _get
from ingest.sources.dart import doc_text
from ingest.sources import kis as md_feed  # 시세(일별 price·trend)는 jhts 시세수집팀 위임

FACT_DIR = os.path.join(DATA_DIR, "facts")
os.makedirs(FACT_DIR, exist_ok=True)
TTL = 1800

def _qlabel(key):   # '202606' → '2026.2Q'
    return f"{key[:4]}.{(int(key[4:6]) + 2) // 3}Q"

def naver_facts(code):
    """시가총액·현재가 + 분기 추이(컨센서스 포함, 단위: 억원)"""
    out = {"marketCap": "", "price": "", "quarters": []}
    try:
        d = _get(f"https://m.stock.naver.com/api/stock/{code}/integration")
        for t in d.get("totalInfos", []):
            if t.get("code") == "marketValue":
                out["marketCap"] = t.get("value", "")
            elif t.get("code") == "lastClosePrice":
                out["price"] = t.get("value", "")
    except Exception:
        pass
    try:
        q = _get(f"https://m.stock.naver.com/api/stock/{code}/finance/quarter")["financeInfo"]
        cons = {t["key"]: t.get("isConsensus") == "Y" for t in q["trTitleList"]}
        rows = {r["title"]: r.get("columns", {}) for r in q["rowList"]}
        for key in sorted(cons):
            val = lambda name: (rows.get(name, {}).get(key) or {}).get("value", "-")
            out["quarters"].append({"key": key, "label": _qlabel(key), "consensus": cons[key],
                                    "sales": val("매출액"), "op": val("영업이익"), "np": val("당기순이익")})
    except Exception:
        pass
    return out

def _clean(s):
    return re.sub(r"<[^>]+>", "", _html.unescape(s or "")).strip()

def naver_news(code, n=10):
    """종목 관련 최신 뉴스 (제목·언론사·일자·링크) — 실적발표 이후 반응용"""
    try:
        d = _get(f"https://m.stock.naver.com/api/news/stock/{code}?pageSize={n}&page=1")
        out = []
        for grp in d:
            for it in grp.get("items", []):
                dt = it.get("datetime", "")
                out.append({"title": _clean(it.get("title", "")), "press": it.get("officeName", ""),
                            "date": f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}" if len(dt) >= 8 else "",
                            "url": f"https://n.news.naver.com/mnews/article/{it.get('officeId')}/{it.get('articleId')}"})
        return out[:n]
    except Exception:
        return []

def _ymd_dash(d):
    d = (d or "").replace("-", "")
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d


def naver_prices(code, n=30):
    """일별 시세 (최신순) — 발표 전 선반영 체크용. (jhts 시세수집팀 위임)"""
    return [{"date": _ymd_dash(r["date"]), "close": int(r["close"] or 0)}
            for r in md_feed.daily_price(code, n)]


def naver_trend(code, n=20):
    """투자자별 매매동향 (최신순) — 외국인/기관 순매수 수량 + 종가. (jhts 위임)"""
    return [{"date": _ymd_dash(r["date"]), "frgn": r["frgn"], "org": r["org"], "close": r["close"]}
            for r in md_feed.investor_trend(code, n)]

_UNIT = {"백만원": 0.01, "억원": 1.0, "천원": 1e-5, "원": 1e-8}

def parse_actual(rcept_no):
    """잠정실적(공정공시) 원문 → 당기 실적(억원) + QoQ/YoY 증감율 + 투자판단 중요사항
    원문 행 구조: <항목> 당해실적 당기 전기 전기대비% 흑전여부 전년동기 전년동기대비% 흑전여부
    """
    try:
        t = doc_text(rcept_no)
    except Exception:
        return {}
    um = re.search(r"단위\s*:\s*(백만원|억원|천원|원)", t)
    mul = _UNIT.get(um.group(1) if um else "백만원", 0.01)
    # 분기 라벨 — 표기 변형 대응: (2026.2Q) / 2026년 2분기 / 실패 시 당기 누적기간 종료월로 추론
    label = ""
    m = re.search(r"(\d{4})\s*[.년]\s*([1-4])\s*(?:Q|분기)", t)
    if m:
        label = f"{m.group(1)}.{m.group(2)}Q"
    else:
        pe = re.search(r"~\s*(\d{4})-(\d{2})-\d{2}", t)   # 당기 누적기간 종료월 (03/06/09/12)
        if pe:
            q = {"03": "1", "06": "2", "09": "3", "12": "4"}.get(pe.group(2))
            if q:
                label = f"{pe.group(1)}.{q}Q"

    def _f(s):
        try:
            return float(s.replace(",", ""))
        except Exception:
            return None

    def row(name):
        m = re.search(name + r"\s*당해실적((?:\s+\S+){7})", t)
        if not m:
            return None
        tok = m.group(1).split()
        amt = lambda v: (None if _f(v) is None else round(_f(v) * mul))
        flag = lambda v: ("" if v in ("-", "") else v)
        return {"cur": amt(tok[0]), "prev": amt(tok[1]), "prev_yr": amt(tok[4]),
                "qoq": _f(tok[2]), "qoq_flag": flag(tok[3]),
                "yoy": _f(tok[5]), "yoy_flag": flag(tok[6])}

    rs, ro, rn = row("매출액"), row("영업이익"), row("당기순이익")
    if rs is None and ro is None:
        return {}
    fmt = lambda v: "-" if v is None else f"{v:,}"
    nm = re.search(r"투자판단과\s*관련한\s*중요사항\s*[-:.\s]*(.+)$", t)
    notes = (nm.group(1).strip()[:400] if nm else "")
    out = {"label": label,
           "sales": fmt(rs["cur"] if rs else None),
           "op": fmt(ro["cur"] if ro else None),
           "np": fmt(rn["cur"] if rn else None),
           "growth": {"sales": rs, "op": ro, "np": rn},
           "notes": notes}
    return out

def facts(code, rcept_no=""):
    """종목 실적 팩트 (캐시 30분, rcept가 달라지면 재생성)"""
    path = os.path.join(FACT_DIR, f"{code}.json")
    if os.path.exists(path):
        try:
            c = json.load(open(path, encoding="utf-8"))
            if time.time() - c.get("_ts", 0) < TTL and c.get("_rcept", "") == rcept_no:
                return c
        except Exception:
            pass
    out = naver_facts(code)
    out["actual"] = parse_actual(rcept_no) if rcept_no else {}
    out["news"] = naver_news(code)         # 실적발표 이후 반응 (뉴스 헤드라인)
    out["prices"] = naver_prices(code)     # 일별 시세 — 선반영(발표 전 급등) 체크
    out["trend"] = naver_trend(code)       # 외국인/기관 순매수 — 수급 판독
    out["_ts"] = time.time()
    out["_rcept"] = rcept_no
    save_json(path, out)
    return out


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

def _num(s):
    try:
        return float(str(s if s is not None else "").replace(",", ""))
    except ValueError:
        return None

def _r1(x):
    return round(x * 10) / 10

def _pct(v):
    if v is None:
        return "-"
    t = f"{v:.1f}".rstrip("0").rstrip(".")
    return f"{'+' if v > 0 else ''}{t}%"

def _eok(v):
    return "-" if v in (None, "", "-") else f"{v}억"

def _ymd(s):
    return re.sub(r"\D", "", str(s or ""))

def _rate(c, b):
    return _r1((c - b) / abs(b) * 100) if c is not None and b else None

def _actual(f):
    """당기 실적 — 원문 숫자를 못 뽑았을 때만 네이버 최근 분기로 대체(숫자는 있는데 라벨만 없으면 바꿔치기 금지)"""
    a, note = dict(f.get("actual") or {}), ""
    if not a.get("label") and _num(a.get("op")) is None and _num(a.get("sales")) is None:
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
            cur = _num(a.get(k))
            prev = _num(qs[idx - 1][k]) if idx > 0 else None
            prev_yr = _num(qs[py][k]) if py >= 0 else None
            return {"cur": cur, "prev": prev, "prev_yr": prev_yr, "qoq": _rate(cur, prev), "yoy": _rate(cur, prev_yr), "qoq_flag": "", "yoy_flag": ""}
        a["growth"] = {k: mk(k) for k in ("sales", "op", "np")}
    return a, note

def _verdict(a, est):
    ao, eo = _num(a.get("op")), _num(est.get("op"))
    g = (a.get("growth") or {}).get("op") or {}
    if eo and ao is not None:
        sp = _r1((ao - eo) / abs(eo) * 100)
        base = f"영업익 컨센({_eok(est.get('op'))}) 대비 {_pct(sp)}"
        if sp >= SURPRISE:
            return {"title": "🔥 어닝 서프라이즈", "tone": "up", "sub": base}
        if sp <= -SURPRISE:
            return {"title": "❄️ 어닝 쇼크", "tone": "down", "sub": base}
        return {"title": "✅ 컨센 부합", "tone": "ok", "sub": f"{base} (±{SURPRISE}% 이내)"}
    if g.get("yoy_flag"):
        return ({"title": "🔄 흑자전환", "tone": "up", "sub": f"영업익 {g['yoy_flag']} (전년동기 대비)"} if "흑자" in g["yoy_flag"]
                else {"title": f"⚠️ {g['yoy_flag']}", "tone": "down", "sub": "영업익 전년동기 대비"})
    if g.get("yoy") is not None:
        qq = f" · QoQ {_pct(g['qoq'])}" if g.get("qoq") is not None else ""
        sub = f"컨센 없음 → 영업익 YoY {_pct(g['yoy'])}{qq} 기준"
        if g["yoy"] >= HIGH_GROWTH:
            return {"title": "🔥 고성장", "tone": "up", "sub": sub}
        if g["yoy"] <= -HIGH_GROWTH:
            return {"title": "❄️ 급감", "tone": "down", "sub": sub}
        return {"title": "➖ 무난", "tone": "muted", "sub": sub}
    return {"title": "판정 불가", "tone": "muted", "sub": "컨센·비교 데이터 없음"}

def _num_line(name, val, est_val, g):
    bits = []
    ev, av = _num(est_val), _num(val)
    if ev and av is not None:
        bits.append(f"컨센 {_pct(_r1((av - ev) / abs(ev) * 100))}")
    g = g or {}
    bits += [f"YoY {g.get('yoy_flag') or _pct(g.get('yoy'))}", f"QoQ {g.get('qoq_flag') or _pct(g.get('qoq'))}"]
    return {"name": name, "value": _eok(val), "detail": " · ".join(bits)}

def _pre_run(f, date):
    ps = f.get("prices") or []
    d = f"{_ymd(date)[:4]}-{_ymd(date)[4:6]}-{_ymd(date)[6:8]}" if date else ""
    idx = next((i for i, p in enumerate(ps) if p["date"] < d), -1) if d else -1
    if idx < 0 or idx + 5 >= len(ps):
        return None
    c1, c0 = _num(ps[idx]["close"]), _num(ps[idx + 5]["close"])
    if not c1 or not c0:
        return None
    r = _r1((c1 - c0) / c0 * 100)
    if r >= PRE_RUN:
        return f"⚠️ 선반영 주의 — 발표 전 5거래일 {_pct(r)} 급등, 재료소멸 셀온 가능성"
    if r <= -PRE_RUN:
        return f"ℹ️ 발표 전 5거래일 {_pct(r)} 조정 — 선반영 부담 낮음"
    return None

def _onetime(a):
    g = a.get("growth") or {}
    op, np_ = g.get("op") or {}, g.get("np") or {}
    oy, ny = op.get("yoy"), np_.get("yoy")
    kw = ONETIME_WORDS.search(a.get("notes") or "")
    if (np_.get("yoy_flag") or "") != (op.get("yoy_flag") or "") or (oy is not None and ny is not None and abs(ny - oy) > ONETIME_GAP):
        return f"⚠️ 일회성 의심 — 순이익 YoY({_pct(ny)}) vs 영업익 YoY({_pct(oy)}) 괴리" + (f" · 원문 '{kw.group(0)}' 언급" if kw else "") + " → 영업이익 우선 판단"
    if kw:
        return f"ℹ️ 원문에 '{kw.group(0)}' 언급 — 일회성 여부 원문 확인"
    return None

def _momentum(a):
    q = ((a.get("growth") or {}).get("op") or {}).get("qoq")
    return f"⚠️ 모멘텀 둔화 — 영업익 QoQ {_pct(q)} (YoY 판정과 별개로 직전 분기 대비 감소)" if q is not None and q <= MOMENTUM else None

def _flow(f, date):
    tr = f.get("trend") or []
    if not tr:
        return None
    if date and any(_ymd(x["date"]) >= _ymd(date) for x in tr):
        tr = sorted((x for x in tr if _ymd(x["date"]) >= _ymd(date)), key=lambda x: x["date"])[:FLOW_DAYS]
        label = f"발표 후 {len(tr)}거래일"
    else:
        tr, label = tr[:FLOW_DAYS], f"최근 {FLOW_DAYS}거래일"
    def total(k):
        vals = [_num(x[k]) * _num(x["close"]) for x in tr if _num(x.get(k)) is not None and _num(x.get("close"))]
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
        s, o = _num(r["sales"]), _num(r["op"])
        r.update(sales=_eok(r["sales"]), op=_eok(r["op"]), np=_eok(r["np"]),
                 opm=f"{round(o / s * 1000) / 10:g}%" if s and o is not None else "", consensus=r.get("consensus", False))
    return rows

def _news(f, date):
    news = f.get("news") or []
    after = [n for n in news if date and _ymd(n["date"]) >= _ymd(date)]
    pick = after or news
    return {"label": "공시일 이후 뉴스" if after else "최신 뉴스", "items": pick[:NEWS_MAX]}

def view(code, rcept_no="", ev=None):
    """실적 상세 화면 모델 — 판정 배지·핵심 수치·경고·수급·추이·뉴스. ev = 그 실적공시(earnings.json 행) 또는 None(예정 발표)"""
    f = facts(code, rcept_no)
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


if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "005930"
    rcept = sys.argv[2] if len(sys.argv) > 2 else ""
    print(json.dumps(view(code, rcept, {"date": ""} if rcept else None), ensure_ascii=False, indent=1))
