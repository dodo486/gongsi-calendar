# -*- coding: utf-8 -*-
"""예상 실적발표 일정 — 소스 2종 수집 (웹 배당 캘린더에 청록색으로 표시)
1. DART '결산실적공시예고(안내공시)' — 회사가 결산실적 공시예정일을 미리 알림 [예고]
2. DART '기업설명회(IR)개최(안내공시)' — 실적발표 컨퍼런스콜 일시·목적·후원 증권사 [IR]
- 원문은 rcept_no 캐시로 같은 문서 재다운로드 방지
- data/earn_sched.json 저장
사용:
  python earn_sched.py [일수=45]
"""
import json, os, sys, re, datetime
from fetch import DATA_DIR, load_watchlist, save_json, fetch_range, TLS_MODE
import dividends as dv   # doc_text(원문 다운로드) 재사용

SCHED_PATH = os.path.join(DATA_DIR, "earn_sched.json")
CACHE_PATH = os.path.join(DATA_DIR, "earn_sched_cache.json")   # rcept_no→파싱 결과 캐시
KW = "결산실적공시예고"
IR_KW = "기업설명회"
COLLECT_DAYS = 45          # 예고는 보통 발표 1~2주 전 — 넉넉히 45일치 훑어 다가오는 일정 확보
IR_DAYS = 14               # IR 안내는 보통 개최 1~10일 전 접수 — 14일이면 충분

def parse_sched(t):
    """예고 본문 → {expected_date(공시예정일), period_end(결산기간 종료일)}"""
    exp = re.search(r"공시예정일\s*(\d{4}-\d{2}-\d{2})", t)
    end = re.search(r"종료일\s*(\d{4}-\d{2}-\d{2})", t)
    return {"expected_date": exp.group(1) if exp else "",
            "period_end": end.group(1) if end else ""}

def parse_ir(t):
    """IR개최 본문 → {expected_date(개최일), time, purpose(개최목적), sponsor(후원 증권사)}"""
    dt = re.search(r"일시\s*(\d{4}-\d{1,2}-\d{1,2})\s*(\d{1,2}:\d{2})?", t)
    pur = re.search(r"개최목적\s*(.+?)\s*\d\s*\.\s*개최방법", t)
    spo = re.search(r"후원기관\s*([^\s<]+)", t)
    d = dt.group(1) if dt else ""
    if d:   # 2026-8-3 → 2026-08-03 정규화
        y, m, dd = d.split("-")
        d = f"{y}-{int(m):02d}-{int(dd):02d}"
    return {"kind": "ir", "expected_date": d,
            "time": (dt.group(2) or "") if dt else "",
            "purpose": (pur.group(1).strip()[:60] if pur else ""),
            "sponsor": spo.group(1) if spo else ""}

def _load_cache():
    if os.path.exists(CACHE_PATH):
        try: return json.load(open(CACHE_PATH, encoding="utf-8"))
        except Exception: return {}
    return {}

def _save_cache(cache):
    cutoff = (datetime.date.today() - datetime.timedelta(days=120)).strftime("%Y%m%d")
    save_json(CACHE_PATH, {k: v for k, v in cache.items() if k[:8] >= cutoff})

def _base(r, rno):
    return {"corp": r["corp_name"], "stock": r["stock_code"],
            "market": "KOSPI" if r["corp_cls"] == "Y" else "KOSDAQ",
            "rcept_no": rno, "rcept_dt": r["rcept_dt"],
            "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rno}"}

def collect(days=COLLECT_DAYS, watch="__load__"):
    """예고공시 + IR개최 공시 → 다가오는 예정 실적발표 이벤트 (src로 구분)"""
    if watch == "__load__":
        watch = load_watchlist() or {}
    today = datetime.date.today()
    end = today.strftime("%Y%m%d")
    cache, changed = _load_cache(), False

    # ① 결산실적공시예고 [예고]
    bgn = (today - datetime.timedelta(days=days)).strftime("%Y%m%d")
    rows = []
    for cls in ("Y", "K"):
        rows += fetch_range(bgn, end, cls, "I")
    merged = {}   # (stock, period_end) → entry (정정 대비 최신 접수 우선)
    for r in rows:
        if KW not in (r.get("report_nm") or "") or r.get("stock_code") not in watch:
            continue
        rno = r["rcept_no"]
        d = cache.get(rno)
        if d is None:                              # 새 문서만 원문 1회 다운로드+파싱
            try: d = parse_sched(dv.doc_text(rno))
            except Exception: d = {"expected_date": "", "period_end": ""}
            cache[rno] = d; changed = True
        if not d.get("expected_date"):
            continue
        key = (r["stock_code"], d["period_end"])
        cur = merged.get(key)
        if cur is None or r["rcept_dt"] >= cur["rcept_dt"]:
            merged[key] = {**_base(r, rno), "src": "예고",
                           "expected_date": d["expected_date"], "period_end": d["period_end"]}

    # ② 기업설명회(IR)개최 — 개최목적에 '실적'이 든 것만 (실적발표 컨퍼런스콜) [IR]
    ir_merged = {}   # (stock, 개최일) → entry
    for r in rows:   # 같은 I-유형 목록 재사용 (기간이 더 길지만 과거 IR은 아래 날짜필터로 제거)
        nm = r.get("report_nm") or ""
        if IR_KW not in nm or r.get("stock_code") not in watch:
            continue
        rno = r["rcept_no"]
        if r["rcept_dt"] < (today - datetime.timedelta(days=IR_DAYS)).strftime("%Y%m%d"):
            continue
        d = cache.get(rno)
        if d is None:
            try: d = parse_ir(dv.doc_text(rno))
            except Exception: d = {"kind": "ir", "expected_date": ""}
            cache[rno] = d; changed = True
        if not d.get("expected_date") or "실적" not in d.get("purpose", ""):
            continue
        key = (r["stock_code"], d["expected_date"])
        cur = ir_merged.get(key)
        if cur is None or r["rcept_dt"] >= cur["rcept_dt"]:
            ir_merged[key] = {**_base(r, rno), "src": "IR",
                              "expected_date": d["expected_date"], "time": d.get("time", ""),
                              "purpose": d.get("purpose", ""), "sponsor": d.get("sponsor", "")}
    if changed:
        _save_cache(cache)
    today_iso = today.strftime("%Y-%m-%d")
    evs = [e for e in list(merged.values()) + list(ir_merged.values())
           if e["expected_date"] >= today_iso]   # 다가오는 일정만
    evs.sort(key=lambda x: (x["expected_date"], x.get("time", ""), x["corp"]))
    return evs

def main(days=COLLECT_DAYS):
    evs = collect(days)
    save_json(SCHED_PATH, {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(evs), "events": evs,
    })
    n_ir = sum(1 for e in evs if e["src"] == "IR")
    print(f"TLS={TLS_MODE} | earn_sched.json 저장: 예정 {len(evs)}건 (예고 {len(evs)-n_ir} · IR {n_ir})")

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else COLLECT_DAYS
    main(n)
