# -*- coding: utf-8 -*-
"""매매거래정지·거래재개 수집기 — KRX KIND(정지/해제 공시) + DART(재개 예정일 보강)

- KIND todaydisclosure(marketType 1=유가, 2=코스닥)에서 '매매거래정지' 제목 공시 스캔
- watchlist(코스피200+코스닥150) 종목만 남김 (노이즈 컷)
- 각 정지 공시 본문에서 정지일시·정지사유·만료일시 파싱
- 재개(해제) 예정일:
    · 만료가 '장종료시까지'(당일정지) → 익영업일 (확정)
    · 만료가 구체적 날짜 → 그 날짜 (확정)
    · 만료가 '변경상장/신주' 등 다중일 → DART 결정공시의 '상장예정일' 당겨옴 (예정)
    · KIND '정지해제' 공시가 이미 나온 종목 → 그 해제일로 확정(예정 딱지 제거)
- data/halts.json 저장 → 웹 공시 캘린더가 '🚫정지 시작'·'✅거래재개' 2점 이벤트로 표시
사용:
  python kind_halt.py            # 최근 정지/재개 → data/halts.json
"""
import json, os, re, io, zipfile, datetime, http.cookiejar, urllib.request, urllib.parse
import numpy as np
from fetch import build_ssl_context, DATA_DIR, BASE, KEY, api, load_watchlist, save_json
from dividends import doc_text
from krx_cal import holiday_dates, default_years

SCAN_DAYS = 50                       # 정지/해제 공시 스캔 기간(일)
KIND_FEED = "https://kind.krx.co.kr/disclosure/todaydisclosure.do"
VIEWER = "https://kind.krx.co.kr/common/disclsviewer.do?method=searchInitInfo&acptNo={}&docno="
CORP_PATH = os.path.join(DATA_DIR, "corp_codes.json")     # stock_code→corp_code 캐시
BODY_PATH = os.path.join(DATA_DIR, "halt_body_cache.json")  # rcept→{halt_date,reason,expire} 캐시
DART_PATH = os.path.join(DATA_DIR, "halt_dart_cache.json")  # stock→{resume,halt} DART 보강 캐시

_HOL = np.array(sorted(str(d) for d in holiday_dates(default_years())), dtype="datetime64[D]")
def next_bday(iso):
    """iso 다음 영업일(주말·휴장일 제외) — 당일정지 재개일 계산용"""
    return str(np.busday_offset(np.datetime64(iso), 1, roll="forward", holidays=_HOL))

# ── KIND opener (쿠키+TLS, kind_limits 와 동일 방식) ──
_OPENER = None
def _get(url, data=None, ref="https://kind.krx.co.kr/"):
    global _OPENER
    if _OPENER is None:
        cj = http.cookiejar.CookieJar()
        _OPENER = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=build_ssl_context()),
            urllib.request.HTTPCookieProcessor(cj))
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": ref}
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

def _feed(sel_date, market):
    today = datetime.date.today().strftime("%Y-%m-%d")
    data = {"method": "searchTodayDisclosureSub", "currentPageSize": "300", "pageIndex": "1",
            "orderMode": "0", "orderStat": "D", "forward": "todaydisclosure_sub", "chose": "all",
            "todayFlag": "Y" if sel_date == today else "N", "selDate": sel_date,
            "marketType": market, "searchCorpName": ""}
    return _get(KIND_FEED, data, ref=KIND_FEED)

def _name2code():
    wl = load_watchlist() or {}
    m = {}
    for code, info in wl.items():
        nm = (info.get("name") or info.get("corp") or "").strip()
        if nm:
            m[nm] = (code, info.get("market", ""))
    return m

# ── KIND 본문(정지일·사유·만료) ──
def _body_text(rcept):
    """KIND 3단계 문서 로더 → 본문 평문(공백정규화)."""
    ref = f"https://kind.krx.co.kr/common/disclsviewer.do?method=searchInitInfo&acptNo={rcept}"
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
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _get(u.group(1), ref=ref)))

def _short_reason(reason, extra):
    """정지사유(+기타사유) → 짧은 라벨."""
    txt = (extra or "") + " " + (reason or "")
    for kw in ("주식분할", "액면분할", "주식병합", "액면병합", "무상증자", "유상증자", "감자",
               "회사분할", "합병", "영업양수도", "주식교환", "조회공시", "불성실공시",
               "상장적격성", "실질심사", "정리매매", "투자자 보호", "투자유의"):
        if kw.replace(" ", "") in txt.replace(" ", ""):
            return kw
    r = (reason or "").strip()
    return (r[:20] + "…") if len(r) > 20 else (r or "매매거래정지")

def _parse_body(rcept, cache):
    """정지 공시 본문 파싱(캐시). return {halt_date, reason, reason_full, expire}."""
    if rcept in cache:
        return cache[rcept]
    t = _body_text(rcept)
    tc = t.replace(" ", "")
    hd = re.search(r"정지일시(\d{4}-\d{2}-\d{2})", tc)
    exp = re.search(r"만료일시([^0-9]*?(?:\d{4}-\d{2}-\d{2})?[가-힣]*)", tc)
    sy = re.search(r"정지사유(.*?)3\.정지기간", tc)
    et = re.search(r"기타사유[:：]?\s*([가-힣A-Za-z0-9]+)", tc)
    reason_full = (sy.group(1) if sy else "").strip()
    out = {
        "halt_date": hd.group(1) if hd else "",
        "reason": _short_reason(reason_full, et.group(1) if et else ""),
        "reason_full": reason_full[:60],
        "expire": (exp.group(1) if exp else "").strip(),
    }
    cache[rcept] = out
    return out

# ── DART 보강 (corp_code 캐시 + 결정공시 상장예정일) ──
def _load_corp_codes():
    if os.path.exists(CORP_PATH):
        try:
            j = json.load(open(CORP_PATH, encoding="utf-8"))
            if j.get("_ts", "") >= (datetime.date.today() - datetime.timedelta(days=7)).strftime("%Y%m%d"):
                return j
        except Exception:
            pass
    # CORPCODE.xml 재다운로드
    url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={KEY}"
    raw = urllib.request.urlopen(url, timeout=60).read()
    zf = zipfile.ZipFile(io.BytesIO(raw))
    xml = zf.read(zf.namelist()[0]).decode("utf-8")
    mp = {"_ts": datetime.date.today().strftime("%Y%m%d")}
    for m in re.finditer(r"<list>(.*?)</list>", xml, re.S):
        b = m.group(1)
        sc = re.search(r"<stock_code>\s*(\S+?)\s*</stock_code>", b)
        cc = re.search(r"<corp_code>\s*(\d+)\s*</corp_code>", b)
        if sc and cc and re.fullmatch(r"\d{6}", sc.group(1)):
            mp[sc.group(1)] = cc.group(1)
    save_json(CORP_PATH, mp)
    return mp

_CORP = None
def _corp_code(stock):
    global _CORP
    if _CORP is None:
        _CORP = _load_corp_codes()
    return _CORP.get(stock, "")

_DATE = r"(\d{4}\D+\d{1,2}\D+\d{1,2})"
def _nd(s):
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", s or "")
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""

def dart_resume(stock, halt_date, dcache):
    """분할/병합/감자/회사분할 결정공시에서 '상장예정일'(=재개 예정일) 당겨오기. 캐시."""
    key = f"{stock}|{halt_date}"
    if key in dcache:
        return dcache[key]
    res = ""
    cc = _corp_code(stock)
    if cc:
        try:
            end = datetime.date.today().strftime("%Y%m%d")
            bgn = (datetime.date.today() - datetime.timedelta(days=150)).strftime("%Y%m%d")
            d = api({"crtfc_key": KEY, "corp_code": cc, "bgn_de": bgn, "end_de": end,
                     "page_no": 1, "page_count": 100})
            rows = d.get("list", []) if d.get("status") == "000" else []
            for r in rows:
                nm = r.get("report_nm", "")
                if not any(k in nm for k in ("분할결정", "병합", "감자결정", "회사분할")):
                    continue
                if "정지" in nm:          # KRX 정지공시 자체는 제외(결정 원문만)
                    continue
                t = doc_text(r["rcept_no"]).replace(" ", "")
                m = re.search(r"상장예정일" + _DATE, t)
                if m:
                    res = _nd(m.group(1))
                    break
        except Exception:
            res = ""
    dcache[key] = res
    return res

# ── 수집 ──
HALT_KW = "매매거래정지"
def collect():
    name2 = _name2code()
    bcache = json.load(open(BODY_PATH, encoding="utf-8")) if os.path.exists(BODY_PATH) else {}
    dcache = json.load(open(DART_PATH, encoding="utf-8")) if os.path.exists(DART_PATH) else {}

    halts, releases, seen = {}, {}, set()   # code→halt / code→release(해제일)
    for dd in range(SCAN_DAYS + 1):
        d = (datetime.date.today() - datetime.timedelta(days=dd)).strftime("%Y-%m-%d")
        for market in ("1", "2"):
            try:
                html = _feed(d, market)
            except Exception:
                continue
            for row in re.split(r"</tr>", html):
                if HALT_KW not in row:
                    continue
                ma = re.search(r"openDisclsViewer\('(\d+)'", row)
                mt = re.search(r"title='([^']*매매거래정지[^']*)'", row)
                mc = re.search(r"companysummary_open\([^)]*\);\s*return false;\"\s*title='([^']+)'", row)
                if not (ma and mt and mc):
                    continue
                nm = mc.group(1).strip()
                if nm not in name2:                       # watchlist 필터
                    continue
                rcept, title = ma.group(1), mt.group(1).strip()
                if rcept in seen:
                    continue
                seen.add(rcept)
                code, mk = name2[nm]
                is_release = ("정지해제" in title) or ("및정지해제" in title) or \
                             (("해제" in title) and ("정지" not in title.replace("정지해제", "")))
                if is_release:
                    # 해제 공시 본문의 해제일시(없으면 접수일) — 재개 확정일
                    t = _body_text(rcept).replace(" ", "")
                    m = re.search(r"해제일시(\d{4}-\d{2}-\d{2})", t)
                    rdate = m.group(1) if m else d
                    if code not in releases or rdate > releases[code]:
                        releases[code] = rdate
                    continue
                # 정지(또는 예고) 공시 — 종목별 최신 1건만
                if code in halts:
                    continue
                b = _parse_body(rcept, bcache)
                halts[code] = {
                    "corp": nm, "code": code, "market": mk,
                    "reason": b["reason"], "reason_full": b["reason_full"],
                    "halt_date": b["halt_date"], "expire": b["expire"],
                    "rcept_no": rcept, "url": VIEWER.format(rcept),
                    "pre": "예고" in title,
                }

    today = datetime.date.today().strftime("%Y-%m-%d")
    out = []
    for code, h in halts.items():
        exp = h.pop("expire", "")
        resume, confirmed = "", False
        # 1) KIND 해제공시가 이미 나옴 → 확정
        if code in releases:
            resume, confirmed = releases[code], True
        # 2) 만료가 구체 날짜
        elif re.search(r"\d{4}-\d{2}-\d{2}", exp):
            resume = re.search(r"(\d{4}-\d{2}-\d{2})", exp).group(1); confirmed = True
        # 3) 당일정지(장종료시까지/장개시전 등) → 익영업일
        elif h["halt_date"] and re.search(r"장종료|당일|장개시|장중", exp):
            resume = next_bday(h["halt_date"]); confirmed = True
        # 4) 다중일(변경상장/신주 등) → DART 상장예정일(예정)
        elif re.search(r"변경상장|신주|상장", exp) or not exp:
            r = dart_resume(code, h["halt_date"], dcache)
            if r:
                resume, confirmed = r, False
        h["resume_date"] = resume
        h["resume_confirmed"] = confirmed
        # 관련성 필터: 재개가 과거로 3일 넘게 지난 건 제외(이미 풀림)
        if resume and resume < (datetime.date.today() - datetime.timedelta(days=3)).strftime("%Y-%m-%d"):
            continue
        out.append(h)

    save_json(BODY_PATH, bcache)
    save_json(DART_PATH, dcache)
    out.sort(key=lambda x: (x.get("halt_date") or "9999", x["corp"]))
    return out

def main():
    evs = collect()
    payload = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(evs), "events": evs,
    }
    save_json(os.path.join(DATA_DIR, "halts.json"), payload)
    conf = sum(e["resume_confirmed"] for e in evs)
    print(f"halts.json 저장: {len(evs)}건 (재개확정 {conf} · 예정 {len(evs)-conf})")
    for e in evs:
        rr = e["resume_date"] or "미정"
        tag = "" if e["resume_confirmed"] else "(예정)"
        print(f"  {e['corp']}({e['code']},{e['market']}) 정지 {e['halt_date']} · {e['reason']} → 재개 {rr}{tag}")

if __name__ == "__main__":
    main()
