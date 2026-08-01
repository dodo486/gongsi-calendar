# -*- coding: utf-8 -*-
"""
실적 공시 실시간 수집 + 현재 주가 등락률(네이버)
- fetch.collect_events 로 '실적' 카테고리 공시만 골라 data/earnings.json 저장
- 각 종목의 현재 등락률은 네이버 실시간 시세(polling.finance.naver.com)로 부착 (KRX 차단 우회)
- 웹 결합뷰의 '실적 그리드'(상하한가와 배당 사이)가 이 파일을 읽음
사용:
  python earnings.py            # 최근 30일 실적 공시 + 등락률 → data/earnings.json
  python earnings.py 60         # 최근 N일
"""
import bisect, json, os, re, sys, datetime, urllib.parse, urllib.request
from fetch import collect_events, DATA_DIR, load_watchlist, save_json, TLS_MODE, build_ssl_context

EARN_PATH = os.path.join(DATA_DIR, "earnings.json")
RETAIN_DAYS = 30
QUOTE_CAP = 80          # 등락률 조회 종목 상한(과도한 네이버 호출 방지)
TICK_CAP = 30           # 1회 실행당 공시시각+분봉(전후5분) 신규 계산 상한
KIND_URL = "https://kind.krx.co.kr/disclosure/todaydisclosure.do"
KIND_HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://kind.krx.co.kr/disclosure/todaydisclosure.do",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}

_CTX = None

def naver_rate(code):
    """네이버 실시간 시세 → {price, rate(부호포함 %), sign} / 실패 시 {}"""
    global _CTX
    if _CTX is None:
        _CTX = build_ssl_context()
    try:
        url = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://finance.naver.com/"})
        with urllib.request.urlopen(req, timeout=8, context=_CTX) as r:
            j = json.load(r)
        d = (j.get("datas") or [{}])[0]
        ratio = d.get("fluctuationsRatio")
        if ratio is None:
            return {}
        sign = (d.get("compareToPreviousPrice") or {}).get("code", "3")  # 1상한2상승3보합4하락5하한
        mag = float(str(ratio).replace(",", ""))
        rate = mag if sign in ("1", "2") else (-mag if sign in ("4", "5") else 0.0)
        return {"price": d.get("closePrice", ""), "rate": round(rate, 2), "sign": sign}
    except Exception:
        return {}

def naver_daily_map(code, days=40):
    """일별 {YYYY-MM-DD: {rate(부호%), open, close}} — 과거 공시일 등락률·시가갭 계산용"""
    global _CTX
    if _CTX is None:
        _CTX = build_ssl_context()
    try:
        url = f"https://m.stock.naver.com/api/stock/{code}/price?pageSize={days}&page=1"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"})
        with urllib.request.urlopen(req, timeout=8, context=_CTX) as r:
            rows = json.load(r)
        out = {}
        for x in rows:
            try:
                out[x["localTradedAt"]] = {
                    "rate": float(str(x.get("fluctuationsRatio", "")).replace(",", "")),
                    "open": int(str(x.get("openPrice", "0")).replace(",", "") or 0),
                    "close": int(str(x.get("closePrice", "0")).replace(",", "") or 0),
                }
            except Exception:
                pass
        return out
    except Exception:
        return {}

def _attach_quotes(events):
    """등락률 부착 — 과거 공시일은 '그 날짜'의 일별 변동률(확정, 재조회 안 함),
    오늘 공시는 실시간 등락률(폴러가 매 주기 갱신)"""
    today = datetime.date.today().strftime("%Y%m%d")
    live, hist, n = {}, {}, 0
    for e in events:
        c, d = e.get("stock", ""), e.get("date", "")
        if not c:
            continue
        if d == today:                      # 오늘 공시 → 실시간
            if c not in live:
                live[c] = naver_rate(c) if n < QUOTE_CAP else {}
                n += 1
            q = live[c]
            e["price"] = q.get("price", "")
            e["rate"] = q.get("rate")
            e["chg_sign"] = q.get("sign", "")
        else:                               # 과거 공시 → 공시일 당일 변동률로 고정
            if e.get("rate_fixed"):
                continue
            if c not in hist:
                hist[c] = naver_daily_map(c) if n < QUOTE_CAP else {}
                n += 1
            r = hist[c].get(f"{d[:4]}-{d[4:6]}-{d[6:8]}")
            if r is not None:
                e["rate"] = r["rate"]
                e["rate_fixed"] = True      # 과거치는 불변 — 다음 갱신 때 스킵
    return events

def kind_disc_time(corp, date_ymd, market):
    """KIND 당일공시 목록에서 해당 회사의 실적 공시 접수시각(HH:MM) 조회 (DART list엔 시각 없음)"""
    global _CTX
    if _CTX is None:
        _CTX = build_ssl_context()
    try:
        data = {
            "method": "searchTodayDisclosureSub", "currentPageSize": "100", "pageIndex": "1",
            "orderMode": "0", "orderStat": "D", "forward": "todaydisclosure_sub", "chose": "all",
            "todayFlag": "N", "selDate": f"{date_ymd[:4]}-{date_ymd[4:6]}-{date_ymd[6:]}",
            "marketType": "1" if market == "KOSPI" else "2", "searchCorpName": corp,
        }
        req = urllib.request.Request(KIND_URL, data=urllib.parse.urlencode(data).encode(), headers=KIND_HDR)
        with urllib.request.urlopen(req, timeout=15, context=_CTX) as r:
            page = r.read().decode("utf-8", "replace")
        for row in re.split(r"</tr>", page):
            mt = re.search(r'txc">\s*([0-9]{1,2}:[0-9]{2})', row)
            ti = re.search(r"openDisclsViewer\([^)]*\)\"\s*title='([^']+)'", row)
            if mt and ti and "실적" in ti.group(1):
                return mt.group(1)
    except Exception:
        pass
    return ""

def minute_closes(code, count=3000):
    """네이버 분봉(fchart) → {YYYYMMDDHHMM: 종가}. 최근 약 6거래일만 제공됨"""
    global _CTX
    if _CTX is None:
        _CTX = build_ssl_context()
    try:
        url = f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=minute&count={count}&requestType=0"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15, context=_CTX) as r:
            text = r.read().decode("utf-8", "replace")
        # 분봉은 시/고/저가가 null로 옴: data="202607240900|null|null|null|266000|387084"
        return {m.group(1): int(m.group(2))
                for m in re.finditer(r'data="(\d{12})\|(?:\d+|null)\|(?:\d+|null)\|(?:\d+|null)\|(\d+)\|', text)}
    except Exception:
        return {}

def _tick_rates(cmap, date_ymd, hhmm):
    """공시시각 기준 전5분/후5분 등락률(%) — 같은 날 15분 이내 분봉만 인정"""
    keys = sorted(k for k in cmap if k.startswith(date_ymd))
    if not keys:
        return None, None
    try:
        base = datetime.datetime.strptime(date_ymd + hhmm, "%Y%m%d%H:%M")
    except ValueError:
        return None, None
    def px(dt):
        k = dt.strftime("%Y%m%d%H%M")
        i = bisect.bisect_right(keys, k) - 1
        if i < 0:
            return None
        got = datetime.datetime.strptime(keys[i], "%Y%m%d%H%M")
        return cmap[keys[i]] if abs((got - dt).total_seconds()) <= 900 else None
    p0 = px(base - datetime.timedelta(minutes=5))
    pt = px(base)
    p5 = px(base + datetime.timedelta(minutes=5))
    pre = round((pt - p0) / p0 * 100, 2) if (pt and p0) else None
    post = round((p5 - pt) / pt * 100, 2) if (p5 and pt) else None
    return pre, post

def _attach_ticks(events):
    """과거 공시에 공시시각 + 발표 전후 반응 부착 (rcept_no별 영구 보존, _carry_over로 유지)
    - 장중(09:00~15:30) 공시 + 분봉 범위 내 → 전5분/후5분 (tick_kind=min)
    - 장후(15:30~) → 당일 등락률 + 익일 시가갭 (tick_kind=close_gap)
    - 장전(~09:00) → 전일 등락률 + 당일 시가갭 (tick_kind=open_gap)
    - 장중이지만 분봉 범위(약 6거래일) 밖 → 당일·익일 등락률 (tick_kind=day)
    """
    today = datetime.date.today().strftime("%Y%m%d")
    cmap_cache, dmap_cache, n = {}, {}, 0
    for e in events:
        st = e.get("tick_state", "")
        if st and not st.startswith("wait:"):
            continue                       # 확정 상태 — 재계산 없음
        if st == "wait:" + today:
            continue                       # 익일 갭 대기 — 하루 1회만 재시도
        if e.get("date", "") >= today or n >= TICK_CAP:
            continue                       # 오늘 공시 / 상한 도달
        code, d = e.get("stock", ""), e["date"]
        iso = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        n += 1
        t = e.get("disc_time") or kind_disc_time(e.get("corp", ""), d, e.get("market", ""))
        if not t:
            e["tick_state"] = "notime"
            continue
        e["disc_time"] = t
        hh, mm = (int(x) for x in t.split(":"))
        if code not in dmap_cache:
            dmap_cache[code] = naver_daily_map(code)
        dmap = dmap_cache[code]
        dates = sorted(dmap)
        if iso not in dmap:
            e["tick_state"] = "noday"
            continue
        i = dates.index(iso)
        cur, prv = dmap[iso], (dmap[dates[i-1]] if i > 0 else None)
        nxt = dmap[dates[i+1]] if i + 1 < len(dates) else None
        gap = lambda a, b: round((a - b) / b * 100, 2) if (a and b) else None
        if 9 <= hh and (hh, mm) < (15, 30):
            if code not in cmap_cache:
                cmap_cache[code] = minute_closes(code)
            if any(k.startswith(d) for k in cmap_cache[code]):
                pre, post = _tick_rates(cmap_cache[code], d, t)
                e["pre5"], e["post5"], e["tick_kind"] = pre, post, "min"
            else:   # 분봉 범위 밖 옛 장중 공시 → 당일·익일 등락률
                e["pre5"], e["post5"], e["tick_kind"] = cur["rate"], (nxt["rate"] if nxt else None), "day"
        elif (hh, mm) >= (15, 30):   # 장후 → 당일 등락 + 익일 시가갭
            e["pre5"] = cur["rate"]
            e["post5"] = gap(nxt["open"], cur["close"]) if nxt else None
            e["tick_kind"] = "close_gap"
            if nxt is None:          # 익일 미개장(최근 공시) — 내일 다시 계산
                e["tick_state"] = "wait:" + today
                continue
        else:                        # 장전 → 전일 등락 + 당일 시가갭
            e["pre5"] = prv["rate"] if prv else None
            e["post5"] = gap(cur["open"], prv["close"]) if prv else None
            e["tick_kind"] = "open_gap"
        e["tick_state"] = "ok" if (e.get("pre5") is not None or e.get("post5") is not None) else "nodata"
    return events

def _carry_over(evs):
    """재수집 시 이전 파일의 확정값(과거 등락률·공시시각·전후5분)을 rcept_no 기준으로 이어받음"""
    if not os.path.exists(EARN_PATH):
        return evs
    try:
        old = {e["rcept_no"]: e for e in json.load(open(EARN_PATH, encoding="utf-8")).get("events", [])}
    except Exception:
        return evs
    keep = ("rate", "rate_fixed", "disc_time", "pre5", "post5", "tick_state", "tick_kind", "price", "chg_sign")
    for e in evs:
        o = old.get(e["rcept_no"])
        if o:
            for k in keep:
                if k in o and k not in e:
                    e[k] = o[k]
    return evs

def _dedupe_sort(evs):
    """접수번호 중복 제거 + 같은 날 같은 보고서의 [기재정정]/원본은 최신 접수분만 유지"""
    seen, base_seen, out = set(), set(), []
    for e in sorted(evs, key=lambda x: (x.get("date", ""), x.get("rcept_no", "")), reverse=True):
        if e["rcept_no"] in seen:
            continue
        base = (e.get("stock", ""), e.get("date", ""),
                re.sub(r"^(\[[^\]]*\]\s*)+", "", e.get("title", "")))   # 제목 앞 [정정] 태그 제거
        if base in base_seen:
            continue   # 최신(정정)본이 이미 있음 — 원본 스킵
        seen.add(e["rcept_no"]); base_seen.add(base); out.append(e)
    return out

def collect(days=RETAIN_DAYS, watch="__load__"):
    today = datetime.date.today()
    bgn = (today - datetime.timedelta(days=days)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    if watch == "__load__":
        watch = load_watchlist()
    evs = [e for e in collect_events(bgn, end, watch=watch, verbose=False) if e.get("category") == "실적"]
    return _dedupe_sort(evs)

def main(days=RETAIN_DAYS):
    evs = collect(days)
    _carry_over(evs)     # 이전 파일의 확정값(과거 등락률·공시시각·전후5분) 이어받기
    _attach_quotes(evs)
    _attach_ticks(evs)   # 공시시각(KIND) + 발표 전5분/후5분(네이버 분봉, 최근 6거래일 한정)
    save_json(EARN_PATH, {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(evs), "events": evs,
    })
    got = sum(e.get("rate") is not None for e in evs)
    print(f"TLS={TLS_MODE} | earnings.json 저장: {len(evs)}건 (등락률 {got})")

def refresh_quotes():
    """실적 목록 재수집 없이 등락률만 갱신(가벼움) — 폴러가 매 주기 호출"""
    if not os.path.exists(EARN_PATH):
        return False
    d = json.load(open(EARN_PATH, encoding="utf-8"))
    _attach_quotes(d.get("events", []))
    d["generated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_json(EARN_PATH, d)
    return True

if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else RETAIN_DAYS
    main(days)
