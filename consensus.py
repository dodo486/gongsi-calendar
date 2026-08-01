# -*- coding: utf-8 -*-
"""실적 상세 데이터 — 컨센서스 전망 + 최근 분기 추이 + 당기 실적
- 네이버 모바일 API: 시가총액·현재가, 분기 매출/영업이익/순이익(억원), 컨센서스(E) 컬럼
- DART 잠정실적 원문: 방금 공시된 당기 실적 (네이버 반영 전 공백 커버, 백만원→억 환산)
- data/facts/{code}.json 캐시(30분) — serve.py 의 /api/earnfacts 가 호출
사용: python consensus.py 003570 [rcept_no]   # 단독 테스트
"""
import html as _html
import json, os, re, sys, time, urllib.request
from fetch import DATA_DIR, save_json, build_ssl_context
import dividends as dv

FACT_DIR = os.path.join(DATA_DIR, "facts")
os.makedirs(FACT_DIR, exist_ok=True)
TTL = 1800
_CTX = None
HDR = {"User-Agent": "Mozilla/5.0"}

def _get(url):
    global _CTX
    if _CTX is None:
        _CTX = build_ssl_context()
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=10, context=_CTX) as r:
        return json.load(r)

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

def naver_news(code, n=5):
    """종목 관련 최신 뉴스 (제목·언론사·일자·링크)"""
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

def naver_reports(code, n=5):
    """종목 대상 최신 증권사 리포트 (네이버 리서치, 제목·증권사·날짜·링크)"""
    global _CTX
    if _CTX is None:
        _CTX = build_ssl_context()
    try:
        url = f"https://finance.naver.com/research/company_list.naver?searchType=itemCode&itemCode={code}"
        req = urllib.request.Request(url, headers=HDR)
        with urllib.request.urlopen(req, timeout=10, context=_CTX) as r:
            page = r.read().decode("euc-kr", "replace")
        out = []
        for m in re.finditer(
                r'company_read\.naver\?nid=(\d+)[^"]*"[^>]*>([^<]+)</a>.*?<td>([^<]+)</td>'
                r'.*?class="date"[^>]*>(\d{2}\.\d{2}\.\d{2})</td>', page, re.S):
            nid, title, broker, date = (x.strip() for x in m.groups())
            out.append({"title": _clean(title), "broker": broker, "date": "20" + date.replace(".", "-"),
                        "url": f"https://finance.naver.com/research/company_read.naver?nid={nid}"})
            if len(out) >= n:
                break
        return out
    except Exception:
        return []

_UNIT = {"백만원": 0.01, "억원": 1.0, "천원": 1e-5, "원": 1e-8}

def parse_actual(rcept_no):
    """잠정실적(공정공시) 원문 → 당기 실적(억원) + QoQ/YoY 증감율 + 투자판단 중요사항
    원문 행 구조: <항목> 당해실적 당기 전기 전기대비% 흑전여부 전년동기 전년동기대비% 흑전여부
    """
    try:
        t = dv.doc_text(rcept_no)
    except Exception:
        return {}
    um = re.search(r"단위\s*:\s*(백만원|억원|천원|원)", t)
    mul = _UNIT.get(um.group(1) if um else "백만원", 0.01)
    lab = re.search(r"\((\d{4}\.\dQ)\)", t)

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
    out = {"label": lab.group(1) if lab else "",
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
    out["news"] = naver_news(code)         # IR·가이던스 보충용 최신 뉴스
    out["reports"] = naver_reports(code)   # 증권사 리포트 (컨콜 요약·전망 포함)
    out["_ts"] = time.time()
    out["_rcept"] = rcept_no
    save_json(path, out)
    return out

if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "005930"
    rcept = sys.argv[2] if len(sys.argv) > 2 else ""
    print(json.dumps(facts(code, rcept), ensure_ascii=False, indent=1))
