# -*- coding: utf-8 -*-
"""종목 '리서치 요약' — 왜 올랐나(테마 강세 + 수급 + 최근 뉴스) 한 화면 요약.
섹터로테이션 종목 클릭 시 수급 패널 위에 표시(serve.py /api/why).

- 테마: sectors_flow.json 의 태그 테마 + 오늘 테마 등락률/유입(강세 판별)
- 수급/모멘텀: 해당 종목의 등락률·상대거래대금·외국인+기관 순매수액(이미 집계됨)
- 뉴스: 네이버 종목뉴스 API(m.stock.naver.com) 최근 헤드라인 (종목명 포함 우선)
- 요약문: 위 수치를 조합한 데이터 기반 한 줄(생성형 아님 — 사실만)
"""
import json, os, re, time, urllib.request
from fetch import DATA_DIR, build_ssl_context

FLOW_PATH = os.path.join(DATA_DIR, "sectors_flow.json")
_SSL = None
_NEWS_CACHE = {}          # code -> (ts, [items])
NEWS_TTL = 600            # 뉴스 캐시 10분

def _ssl():
    global _SSL
    if _SSL is None:
        _SSL = build_ssl_context()
    return _SSL

def _load_flow():
    try:
        return json.load(open(FLOW_PATH, encoding="utf-8"))
    except Exception:
        return {}

def _news(code, name=""):
    """네이버 종목뉴스 최근 헤드라인 → [{title, office, dt, url}] (종목명 포함 우선, 최대 5)."""
    now = time.time()
    hit = _NEWS_CACHE.get(code)
    if hit and now - hit[0] < NEWS_TTL:
        return hit[1]
    items = []
    try:
        url = f"https://m.stock.naver.com/api/news/stock/{code}?pageSize=12&page=1"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15"})
        raw = urllib.request.urlopen(req, timeout=10, context=_ssl()).read().decode("utf-8", "replace")
        groups = json.loads(raw)
        seen = set()
        flat = []
        for g in groups:
            for it in (g.get("items") or []):
                t = (it.get("titleFull") or it.get("title") or "").strip()
                t = re.sub(r"<[^>]+>", "", t)
                if not t or t in seen:
                    continue
                seen.add(t)
                dt = it.get("datetime") or ""
                dts = f"{dt[4:6]}-{dt[6:8]} {dt[8:10]}:{dt[10:12]}" if len(dt) >= 12 else ""
                oid, aid = it.get("officeId"), it.get("articleId")
                link = it.get("mobileNewsUrl") or (
                    f"https://n.news.naver.com/mnews/article/{oid}/{aid}" if oid and aid else "")
                flat.append({"title": t, "office": it.get("officeName", ""), "dt": dts,
                             "url": link, "_named": name and name in t})
        # 종목명 포함 기사 우선, 그다음 최신순(입력이 이미 최신순)
        flat.sort(key=lambda x: (0 if x["_named"] else 1))
        for x in flat[:5]:
            x.pop("_named", None)
            items.append(x)
    except Exception:
        items = []
    _NEWS_CACHE[code] = (now, items)
    return items

def _summary(ctx, themes):
    """데이터 기반 한 줄 요약 — 오늘 등락 · 대표테마 강세 · 상대대금 · 수급."""
    parts = []
    r = ctx.get("rate")
    if r is not None:
        parts.append(f"오늘 {'+' if r > 0 else ''}{r}%")
    if themes and themes[0].get("rate") is not None:
        t0 = themes[0]
        tag = " 강세" if (t0["rate"] or 0) >= 1.5 else (" 약세" if (t0["rate"] or 0) <= -1.5 else "")
        parts.append(f"'{t0['name']}' 테마 {'+' if t0['rate'] > 0 else ''}{t0['rate']}%{tag}")
    rv = ctx.get("relvol")
    if rv:
        parts.append(f"상대대금 {rv}x" + (" 급증" if rv >= 2 else ""))
    nb = ctx.get("netbuy_val")
    if nb:
        parts.append(f"외국인+기관 {'순매수' if nb > 0 else '순매도'} {abs(nb)/1e8:.0f}억")
    return " · ".join(parts)

def build(code):
    sf = _load_flow()
    stocks = sf.get("stocks", {})
    st = stocks.get(code, {})
    tmap = {t.get("name"): t for t in sf.get("themes", [])}
    themes = []
    for nm in st.get("themes", []):
        t = tmap.get(nm, {})
        themes.append({"name": nm, "rate": t.get("rate"), "relvol": t.get("relvol"),
                       "inflow": bool(t.get("inflow"))})
    themes.sort(key=lambda x: (x["rate"] if x["rate"] is not None else -99), reverse=True)
    name = st.get("name", "")
    ctx = {"name": name, "market": st.get("market", ""), "rate": st.get("rate"),
           "relvol": st.get("relvol"), "netbuy_val": st.get("netbuy_val"),
           "mktcap": st.get("mktcap")}
    return {
        "code": code, "name": name, "themes": themes, "context": ctx,
        "summary": _summary(ctx, themes),
        "news": _news(code, name),
        "in_universe": bool(st),
        "flow_at": sf.get("generated_at", ""),
    }
