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
import json, os, sys, ssl, datetime, urllib.request
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from fetch import collect_events, DATA_DIR, load_watchlist, save_json, TLS_MODE

EARN_PATH = os.path.join(DATA_DIR, "earnings.json")
RETAIN_DAYS = 30
QUOTE_CAP = 80          # 등락률 조회 종목 상한(과도한 네이버 호출 방지)

def _ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        c = ssl.create_default_context()
        if os.environ.get("DART_INSECURE_TLS") == "1":
            c.check_hostname = False; c.verify_mode = ssl.CERT_NONE
        return c
_CTX = None

def naver_rate(code):
    """네이버 실시간 시세 → {price, rate(부호포함 %), sign} / 실패 시 {}"""
    global _CTX
    if _CTX is None:
        _CTX = _ctx()
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

def _attach_quotes(events):
    cache, n = {}, 0
    for e in events:
        c = e.get("stock", "")
        if not c:
            continue
        if c not in cache:
            cache[c] = naver_rate(c) if n < QUOTE_CAP else {}
            n += 1
        q = cache[c]
        e["price"] = q.get("price", "")
        e["rate"] = q.get("rate")
        e["chg_sign"] = q.get("sign", "")
    return events

def _dedupe_sort(evs):
    seen, out = set(), []
    for e in sorted(evs, key=lambda x: (x.get("date", ""), x.get("rcept_no", "")), reverse=True):
        if e["rcept_no"] in seen:
            continue
        seen.add(e["rcept_no"]); out.append(e)
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
    _attach_quotes(evs)
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
