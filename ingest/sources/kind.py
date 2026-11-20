# -*- coding: utf-8 -*-
"""KRX KIND(kind.krx.co.kr) 접속 — 당일공시 목록·공시 본문·감시종목 이름 매핑.
거래정지(kind_halt)·선물 상하한가(kind_limits)·시장조치(krx_actions)가 같이 쓴다.
(data.krx.co.kr 은 이 PC에서 차단되지만 kind 는 접근 가능)"""
import re, datetime, http.cookiejar, urllib.request, urllib.parse
from ingest.sources.net import build_ssl_context
from core.paths import load_watchlist

FEED_URL = "https://kind.krx.co.kr/disclosure/todaydisclosure.do"
VIEWER = "https://kind.krx.co.kr/common/disclsviewer.do?method=searchInitInfo&acptNo={}&docno="

_OPENER = None
def get(url, data=None, ref="https://kind.krx.co.kr/", ajax=False):
    """쿠키 유지 세션으로 GET(data 있으면 POST) → 디코드한 본문. 문서서버는 euc-kr 이 섞여 있어 utf-8 실패 시 euc-kr."""
    global _OPENER
    if _OPENER is None:
        _OPENER = urllib.request.build_opener(urllib.request.HTTPSHandler(context=build_ssl_context()),
                                              urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": ref}
    if ajax:
        hdr["X-Requested-With"] = "XMLHttpRequest"
    if data is not None:
        hdr["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        data = urllib.parse.urlencode(data).encode()
    b = _OPENER.open(urllib.request.Request(url, data=data, headers=hdr), timeout=20).read()
    for enc in ("utf-8", "euc-kr"):
        try:
            return b.decode(enc)
        except Exception:
            continue
    return b.decode("utf-8", "replace")

def feed(sel_date, market_type, corp=""):
    """당일공시 목록 HTML. market_type: 1=유가, 2=코스닥, 3=파생상품시장. sel_date: YYYY-MM-DD. corp: 회사명 검색."""
    today = datetime.date.today().strftime("%Y-%m-%d")
    data = {"method": "searchTodayDisclosureSub", "currentPageSize": "500", "pageIndex": "1",
            "orderMode": "0", "orderStat": "D", "forward": "todaydisclosure_sub", "chose": "all",
            "todayFlag": "Y" if sel_date == today else "N", "selDate": sel_date,
            "marketType": market_type, "searchCorpName": corp}
    return get(FEED_URL, data, ref=FEED_URL, ajax=True)

def body_text(rcept):
    """공시 본문 평문(태그 제거·공백 정규화). KIND 3단계: searchInitInfo → docNo → searchContents 의 setPath(.htm) → 본문.
    못 받으면 빈 문자열."""
    ref = f"https://kind.krx.co.kr/common/disclsviewer.do?method=searchInitInfo&acptNo={rcept}"
    m = re.search(r"""value=['"](\d{10,}\|[^'"]*)['"]""", get(ref + "&docno="))
    if not m:
        return ""
    sc = get("https://kind.krx.co.kr/common/disclsviewer.do",
             {"method": "searchContents", "docNo": m.group(1).split("|")[0]}, ref=ref)
    u = re.search(r"setPath\('[^']*','(https?://[^']+\.htm)'", sc)
    if not u:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", get(u.group(1), ref=ref)))

def name2code():
    """감시종목 회사명 → (종목코드, 시장). KIND 목록엔 회사명만 있어 코드로 바꿀 때 쓴다."""
    m = {}
    for code, info in (load_watchlist() or {}).items():
        nm = (info.get("name") or info.get("corp") or "").strip()
        if nm:
            m[nm] = (code, info.get("market", ""))
    return m
