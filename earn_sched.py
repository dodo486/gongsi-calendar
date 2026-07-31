# -*- coding: utf-8 -*-
"""예상 실적발표 일정 — DART '결산실적공시예고(안내공시)' 수집
- 회사가 결산실적을 언제 공시할지 미리 알리는 공시 → 관찰종목의 '예정 실적발표일'
- 문서 본문에서 '결산실적 공시예정일' + '결산대상기간 종료일'(분기 판별) 파싱
- rcept_no 캐시로 같은 문서 재다운로드 방지
- data/earn_sched.json 저장 → 웹 실적 그리드 '오른쪽'(예정 일정)이 읽음
사용:
  python earn_sched.py [일수=45]
"""
import json, os, sys, re, datetime
from fetch import DATA_DIR, load_watchlist, save_json, fetch_range, TLS_MODE
import dividends as dv   # doc_text(원문 다운로드) 재사용

SCHED_PATH = os.path.join(DATA_DIR, "earn_sched.json")
CACHE_PATH = os.path.join(DATA_DIR, "earn_sched_cache.json")   # rcept_no→파싱 결과 캐시
KW = "결산실적공시예고"
COLLECT_DAYS = 45          # 예고는 보통 발표 1~2주 전 — 넉넉히 45일치 훑어 다가오는 일정 확보

def parse_sched(t):
    """예고 본문 → {expected_date(공시예정일), period_end(결산기간 종료일)}"""
    exp = re.search(r"공시예정일\s*(\d{4}-\d{2}-\d{2})", t)
    end = re.search(r"종료일\s*(\d{4}-\d{2}-\d{2})", t)
    return {"expected_date": exp.group(1) if exp else "",
            "period_end": end.group(1) if end else ""}

def _load_cache():
    if os.path.exists(CACHE_PATH):
        try: return json.load(open(CACHE_PATH, encoding="utf-8"))
        except Exception: return {}
    return {}

def _save_cache(cache):
    cutoff = (datetime.date.today() - datetime.timedelta(days=120)).strftime("%Y%m%d")
    save_json(CACHE_PATH, {k: v for k, v in cache.items() if k[:8] >= cutoff})

def collect(days=COLLECT_DAYS, watch="__load__"):
    if watch == "__load__":
        watch = load_watchlist() or {}
    today = datetime.date.today()
    bgn = (today - datetime.timedelta(days=days)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    rows = []
    for cls in ("Y", "K"):
        rows += fetch_range(bgn, end, cls, "I")
    rows = [r for r in rows if KW in (r.get("report_nm") or "") and r.get("stock_code") in watch]

    cache, changed = _load_cache(), False
    merged = {}   # (stock, period_end) → entry (정정 대비 최신 접수 우선)
    for r in rows:
        rno = r["rcept_no"]
        d = cache.get(rno)
        if d is None:                              # 새 문서만 원문 1회 다운로드+파싱
            try: d = parse_sched(dv.doc_text(rno))
            except Exception: d = {"expected_date": "", "period_end": ""}
            cache[rno] = d; changed = True
        if not d["expected_date"]:
            continue
        key = (r["stock_code"], d["period_end"])
        cur = merged.get(key)
        if cur is None or r["rcept_dt"] >= cur["rcept_dt"]:
            merged[key] = {
                "corp": r["corp_name"], "stock": r["stock_code"],
                "market": "KOSPI" if r["corp_cls"] == "Y" else "KOSDAQ",
                "expected_date": d["expected_date"], "period_end": d["period_end"],
                "rcept_no": rno, "rcept_dt": r["rcept_dt"],
                "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rno}",
            }
    if changed:
        _save_cache(cache)
    today_iso = today.strftime("%Y-%m-%d")
    evs = [e for e in merged.values() if e["expected_date"] >= today_iso]   # 다가오는 일정만
    evs.sort(key=lambda x: (x["expected_date"], x["corp"]))
    return evs

def main(days=COLLECT_DAYS):
    evs = collect(days)
    save_json(SCHED_PATH, {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(evs), "events": evs,
    })
    print(f"TLS={TLS_MODE} | earn_sched.json 저장: {len(evs)}건 (다가오는 예정 실적발표)")

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else COLLECT_DAYS
    main(n)
