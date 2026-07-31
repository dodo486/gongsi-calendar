# -*- coding: utf-8 -*-
"""
선물 상하한가(가격제한폭 단계 도달) 수집기 — KRX KIND 파생상품시장 공시
- 소스: https://kind.krx.co.kr  (※ data.krx.co.kr 은 이 PC에서 차단되지만 kind 는 접근 가능)
- todaydisclosure(marketType=3=파생상품시장)에서 '가격제한폭 확대요건 도달' 공시만 수집
- 지수선물(코스피200선물·KRX300선물 등) + 주식선물(코스피200/코스닥150 편입 종목) 로 분류·필터
- data/limits.json 저장 → 웹 '상하한가' 탭이 읽음
- 실시간: monitor.py 가 폴링하며 신규 접수번호(acptNo)에 윈도우 토스트 알림
사용:
  python kind_limits.py            # 오늘 선물 가격제한폭 도달 공시 → data/limits.json
  python kind_limits.py 2026-05-06 # 특정일자
"""
import json, os, sys, re, datetime, ssl, http.cookiejar, urllib.request, urllib.parse
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
# fetch 의 TLS 세팅(truststore→certifi)·경로·watchlist 재사용
from fetch import TLS_MODE, DATA_DIR, BASE, load_watchlist, save_json

KIND_URL = "https://kind.krx.co.kr/disclosure/todaydisclosure.do"
VIEWER = "https://kind.krx.co.kr/common/disclsviewer.do?method=searchInitInfo&acptNo={}&docno="
HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://kind.krx.co.kr/disclosure/todaydisclosure.do",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}
LIMIT_KW = "가격제한폭 확대요건 도달"   # 이 문구가 든 파생 공시만 대상

def fetch_html(sel_date=None):
    """KIND 파생상품시장 당일공시 목록 HTML (marketType=3)"""
    today = datetime.date.today().strftime("%Y-%m-%d")
    d = sel_date or today
    data = {
        "method": "searchTodayDisclosureSub", "currentPageSize": "300", "pageIndex": "1",
        "orderMode": "0", "orderStat": "D", "forward": "todaydisclosure_sub", "chose": "all",
        "todayFlag": "Y" if d == today else "N", "selDate": d,
        "marketType": "3", "searchCorpName": "",
    }
    req = urllib.request.Request(KIND_URL, data=urllib.parse.urlencode(data).encode(), headers=HDR)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")

def _name_to_code():
    """watchlist(종목코드→정보)로 회사명→(코드,시장) 역인덱스 — 표시 보강용(필터엔 지수범례 사용)"""
    wl = load_watchlist() or {}
    m = {}
    for code, info in wl.items():
        nm = (info.get("name") or info.get("corp") or "").strip()
        if nm:
            m[nm] = (code, info.get("market", ""))
    return m

def parse(html, watch_only=True):
    """가격제한폭 도달 공시 파싱 → 이벤트 리스트 (시간 내림차순)"""
    name2 = _name_to_code()
    events = []
    for row in re.split(r"</tr>", html):
        if LIMIT_KW not in row:
            continue
        m_acpt = re.search(r"openDisclsViewer\('(\d+)'", row)
        m_title = re.search(r"openDisclsViewer\([^)]*\)\"\s*title='([^']+)'", row)
        m_time = re.search(r'class="first txc">\s*([0-9]{1,2}:[0-9]{2})', row)
        m_comp = re.search(r"companysummary_open\([^)]*\);\s*return false;\"\s*title='([^']+)'", row)
        if not (m_acpt and m_title):
            continue
        title = m_title.group(1).strip()
        markets = re.findall(r"alt='([^']+)'", row)   # 지수 범례(KOSPI200/KOSDAQ150/KRX300…)
        stage = int(re.search(r"(\d)단계", title).group(1)) if re.search(r"(\d)단계", title) else 0
        direction = "상승" if "상승" in title else ("하락" if "하락" in title else "")

        if m_comp:                                     # 주식선물 (개별종목)
            name = m_comp.group(1).strip()
            in_k200 = "KOSPI200" in markets
            in_kq150 = "KOSDAQ150" in markets
            if watch_only and not (in_k200 or in_kq150):
                continue                               # 관찰대상(코스피200/코스닥150) 외 제외
            market = "KOSPI200" if in_k200 else ("KOSDAQ150" if in_kq150 else "")
            code = name2.get(name, ("", ""))[0]
            kind = "주식선물"
        else:                                          # 지수선물 (회사칸 비어있음)
            name = re.sub(r"\s*\d단계.*$", "", title).strip()   # "코스피200선물 3단계…" → "코스피200선물"
            market = "지수"
            code = ""
            kind = "지수선물"

        events.append({
            "time": m_time.group(1) if m_time else "",
            "kind": kind, "name": name, "code": code, "market": market,
            "stage": stage, "direction": direction, "title": title,
            "rcept_no": m_acpt.group(1), "url": VIEWER.format(m_acpt.group(1)),
            "markets": [x for x in markets if x in ("KOSPI200", "KOSDAQ150", "KRX300", "KTOP30", "V100")],
        })
    # 시간 내림차순(최신 먼저); 동시간이면 접수번호 큰 순
    events.sort(key=lambda e: (e["time"], e["rcept_no"]), reverse=True)
    return events

# --- 본문 '확대 예정시각' 파싱 (KIND 문서서버 3단계 로더 통과) ---
EXP_CACHE_PATH = os.path.join(DATA_DIR, "limits_exp_cache.json")  # rcept_no→확대예정시각 캐시
ENRICH_CAP = 40   # 한 번의 collect에서 새로 본문 조회할 최대 건수(나머지는 다음 주기에)

def _make_cookie_opener():
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
        if os.environ.get("DART_INSECURE_TLS") == "1":
            ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx),
                                       urllib.request.HTTPCookieProcessor(cj))
_OPENER = None
def _get(url, data=None, ref="https://kind.krx.co.kr/"):
    global _OPENER
    if _OPENER is None:
        _OPENER = _make_cookie_opener()
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": ref}
    if data is not None:
        hdr["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        data = urllib.parse.urlencode(data).encode()
    b = _OPENER.open(urllib.request.Request(url, data=data, headers=hdr), timeout=15).read()
    for enc in ("euc-kr", "utf-8"):
        try:
            return b.decode(enc)
        except Exception:
            continue
    return b.decode("utf-8", "replace")

def fetch_expand_time(rcept_no):
    """공시 본문에서 '확대 예정시각'(HH:MM:SS) 추출.
    KIND 3단계: searchInitInfo→#mainDoc의 docNo→searchContents의 setPath(.htm)→본문."""
    try:
        ref = f"https://kind.krx.co.kr/common/disclsviewer.do?method=searchInitInfo&acptNo={rcept_no}"
        init = _get(ref + "&docno=")
        m = re.search(r"""value=['"](\d{10,}\|[^'"]*)['"]""", init)
        if not m:
            return ""
        docno = m.group(1).split("|")[0]
        sc = _get("https://kind.krx.co.kr/common/disclsviewer.do",
                  {"method": "searchContents", "docNo": docno}, ref=ref)
        u = re.search(r"setPath\('[^']*','(https?://[^']+\.htm)'", sc)
        if not u:
            return ""
        clean = re.sub(r"<[^>]+>", " ", _get(u.group(1), ref=ref))
        e = re.search(r"확대\s*예정시각\s*([0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)", clean)
        return e.group(1) if e else ""
    except Exception:
        return ""

def _load_exp_cache():
    if os.path.exists(EXP_CACHE_PATH):
        try:
            return json.load(open(EXP_CACHE_PATH, encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _save_exp_cache(cache):
    cutoff = (datetime.date.today() - datetime.timedelta(days=7)).strftime("%Y%m%d")
    save_json(EXP_CACHE_PATH, {k: v for k, v in cache.items() if k[:8] >= cutoff})

def collect(sel_date=None, watch_only=True, enrich=True, enrich_cap=ENRICH_CAP):
    """가격제한폭 도달 공시 수집 + 본문 '확대 예정시각' 보강(캐시, 신규분만 조회)."""
    events = parse(fetch_html(sel_date), watch_only)
    if enrich:
        cache = _load_exp_cache()
        fetched, changed = 0, False
        for e in events:
            rc = e["rcept_no"]
            if rc in cache:
                e["expand_time"] = cache[rc]
            elif fetched < enrich_cap:
                t = fetch_expand_time(rc); fetched += 1
                if t:
                    cache[rc] = t; changed = True
                e["expand_time"] = t
            # 캡 초과분은 expand_time 미설정 → 다음 주기에 채움
        if changed:
            _save_exp_cache(cache)
    return events

def main():
    sel = sys.argv[1] if len(sys.argv) > 1 and re.match(r"\d{4}-\d{2}-\d{2}", sys.argv[1]) else None
    evs = collect(sel, enrich_cap=300)   # 수동 실행: 가능한 만큼 본문 보강
    payload = {
        "date": sel or datetime.date.today().strftime("%Y-%m-%d"),
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(evs), "events": evs,
    }
    save_json(os.path.join(DATA_DIR, "limits.json"), payload)
    idx = sum(e["kind"] == "지수선물" for e in evs)
    stk = sum(e["kind"] == "주식선물" for e in evs)
    s3 = sum(e["stage"] >= 3 for e in evs)
    exp = sum(bool(e.get("expand_time")) for e in evs)
    print(f"TLS={TLS_MODE} | limits.json 저장: {len(evs)}건 "
          f"(지수선물 {idx} · 주식선물 {stk} · 3단계 {s3} · 확대예정시각 {exp})")

if __name__ == "__main__":
    main()
