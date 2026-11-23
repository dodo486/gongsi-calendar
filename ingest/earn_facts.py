# -*- coding: utf-8 -*-
"""실적 상세 원자료(화면에서 누를 때 받음) — 컨센서스 전망 + 최근 분기 추이 + 당기 실적. 판정은 derive.earn_detail
- 네이버 모바일 API: 시가총액·현재가, 분기 매출/영업이익/순이익(억원), 컨센서스(E) 컬럼
- DART 잠정실적 원문: 방금 공시된 당기 실적 (네이버 반영 전 공백 커버, 백만원→억 환산)
- data/facts/{code}.json 캐시(30분) — serve.py 의 /api/earnfacts 가 호출
사용: python -m ingest.earn_facts 003570 [rcept_no]   # 단독 테스트
"""
import html as _html
import json, os, re, sys, time
from core.paths import save_json, load_json, CACHE_DIR
from ingest.sources.naver import get as _get
from ingest.sources.dart import doc_text
from ingest.parsers.earnings import parse_actual
from core.krx_cal import iso
from core import market as md_feed

FACT_DIR = os.path.join(CACHE_DIR, "facts")
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
                            "date": iso(dt),
                            "url": f"https://n.news.naver.com/mnews/article/{it.get('officeId')}/{it.get('articleId')}"})
        return out[:n]
    except Exception:
        return []

def facts(code, rcept_no=""):
    """종목 실적 팩트 (캐시 30분, rcept가 달라지면 재생성)"""
    path = os.path.join(FACT_DIR, f"{code}.json")
    c = load_json(path, {})
    if time.time() - c.get("_ts", 0) < TTL and c.get("_rcept", "") == rcept_no:
        return c
    out = naver_facts(code)
    try:
        out["actual"] = parse_actual(doc_text(rcept_no)) if rcept_no else {}
    except Exception:
        out["actual"] = {}
    out["news"] = naver_news(code)         # 실적발표 이후 반응 (뉴스 헤드라인)
    out["prices"] = [{"date": r["date"], "close": int(r["close"] or 0)} for r in md_feed.daily_price(code, 30)]   # 선반영(발표 전 급등) 체크
    out["trend"] = [{k: r[k] for k in ("date", "frgn", "org", "close")} for r in md_feed.investor_trend(code, 20)]   # 외국인/기관 순매수
    out["_ts"] = time.time()
    out["_rcept"] = rcept_no
    save_json(path, out)
    return out


if __name__ == "__main__":
    print(json.dumps(facts(sys.argv[1] if len(sys.argv) > 1 else "005930", sys.argv[2] if len(sys.argv) > 2 else ""), ensure_ascii=False, indent=1)[:3000])
