# -*- coding: utf-8 -*-
"""KRX KIND(kind.krx.co.kr) 접속 — 당일공시 목록·공시 본문. 들여오기(ingest.run)만 쓴다.
(data.krx.co.kr 은 이 PC에서 차단되지만 kind 는 접근 가능)"""
import re, time, datetime, threading, http.cookiejar, urllib.error, urllib.request, urllib.parse
from ingest.sources.net import build_ssl_context
from core.krx_cal import iso

FEED_URL = "https://kind.krx.co.kr/disclosure/todaydisclosure.do"

GAP = 0.3                  # 요청 사이 최소 간격(초) — KIND 는 몰아서 받으면 429
BACKOFF = (5, 20)          # 429 를 받으면 쉬었다 다시
_OPENER = None
_pace = {"lock": threading.Lock(), "at": 0.0}

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
    for wait in (*BACKOFF, None):
        with _pace["lock"]:
            time.sleep(max(0.0, _pace["at"] + GAP - time.time()))
            _pace["at"] = time.time()
        try:
            b = _OPENER.open(urllib.request.Request(url, data=data, headers=hdr), timeout=20).read()
            break
        except urllib.error.HTTPError as ex:
            if ex.code != 429 or wait is None:
                raise
            time.sleep(wait)
    for enc in ("utf-8", "euc-kr"):
        try:
            return b.decode(enc)
        except Exception:
            continue
    return b.decode("utf-8", "replace")

def feed(sel_date, market_type, corp=""):
    """당일공시 목록 HTML. market_type: 1=유가, 2=코스닥, 3=파생상품시장. sel_date: YYYY-MM-DD. corp: 회사명 검색."""
    today = iso(datetime.date.today())
    data = {"method": "searchTodayDisclosureSub", "currentPageSize": "500", "pageIndex": "1",
            "orderMode": "0", "orderStat": "D", "forward": "todaydisclosure_sub", "chose": "all",
            "todayFlag": "Y" if sel_date == today else "N", "selDate": sel_date,
            "marketType": market_type, "searchCorpName": corp}
    return get(FEED_URL, data, ref=FEED_URL, ajax=True)

def rows(sel_date, market_type):
    """당일공시 목록 → (목록이 제대로 왔나, 행들 [{acpt 접수번호, time, corp 회사명, title, submitter 제출인, indices 지수 범례}])"""
    html = feed(sel_date, market_type)
    txt = lambda s: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()
    attr = lambda s: (re.search(r"title='([^']+)'", s) or [None, ""])[1].strip()
    out = []
    for row in re.split(r"</tr>", html):
        acpt = re.search(r"openDisclsViewer\('(\d+)'", row)
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if acpt and len(tds) >= 3:
            out.append({"acpt": acpt[1], "time": (re.search(r"([0-9]{1,2}:[0-9]{2})", tds[0]) or [None, ""])[1],
                        "name": attr(tds[1]), "title": attr(tds[2]) or txt(tds[2]),
                        "submitter": txt(tds[3]) if len(tds) > 3 else "", "indices": re.findall(r"alt='([^']+)'", row)})
    return "openDisclsViewer" in html, out

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
