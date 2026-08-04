# -*- coding: utf-8 -*-
"""유상·무상증자 캘린더용 데이터 추출 (원문 파싱 — 정형 API 없음)
- 대상 공시: 주요사항보고서(유상증자결정 / 무상증자결정 / 유무상증자결정) — 감시대상 한정(배당과 동일)
- 신주배정기준일 파싱 → 권리부매수일(T-2)·권리락일(T-1) 계산 (배당 T-1/T-2 로직 재사용)
- 제3자배정 등 신주배정기준일이 없는 건은 자동 제외(권리락 없음)
- data/capital.json 저장 → 배당 캘린더에 무증(보라/연보라)·유증(파랑/하늘) 표시
사용:
  python capital.py            # 최근 88일 유·무상증자 → data/capital.json
  python capital.py 30         # 최근 30일
"""
import sys, os, re, json, datetime, threading
from fetch import fetch_range, load_watchlist, DATA_DIR, TLS_MODE, save_json
from dividends import doc_text, t_minus, norm_date

_LOCK = threading.RLock()
CAP_PATH = os.path.join(DATA_DIR, "capital.json")
CACHE_PATH = os.path.join(DATA_DIR, "cap_cache.json")   # rcept_no -> 파싱결과(원문 재다운로드 방지)
MAX_DAYS = 88   # DART: corp_code 없이 조회 가능한 최대 기간(3개월)

RE_REC = re.compile(r"신주배정기준일\s*(\d{4}\D+\d{1,2}\D+\d{1,2})")
# 무상: "1주당 신주배정 주식수 보통주식 (주) 0.5" / 유상: "1주당 신주배정주식수 (주) 0.24" 둘 다 대응
RE_RATIO = re.compile(r"1주당\s*신주배정\s*주식수\s*(?:보통주식)?\s*\(주\)\s*([\d.]+)")

def _is_cap(nm):
    return any(k in (nm or "") for k in ("유상증자결정", "무상증자결정", "유무상증자결정"))

def cap_type(nm):
    if "유상증자결정" in nm: return "유상증자"
    if "무상증자결정" in nm: return "무상증자"
    if "유무상증자결정" in nm: return "유상증자"   # 유상 배정기준일을 주 권리로 표기
    return "유상증자" if "유상" in nm else "무상증자"

def parse_cap(t):
    m = RE_REC.search(t)
    r = RE_RATIO.search(t)
    return {"record_date": norm_date(m.group(1)) if m else "",
            "ratio": r.group(1) if r else ""}

def load_cache():
    if os.path.exists(CACHE_PATH):
        try:
            return json.load(open(CACHE_PATH, encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _fields(rno, cache):
    """파싱 필드 반환 — 캐시에 있으면 재사용(다운로드 0), 없으면 원문 1회 다운로드+파싱 후 캐시."""
    c = cache.get(rno)
    if c is not None:
        return {"record_date": c.get("record_date", ""), "ratio": c.get("ratio", "")}
    try:
        d = parse_cap(doc_text(rno))
    except Exception:
        d = {"record_date": "", "ratio": ""}
    cache[rno] = d
    return d

def _base(r):
    return {"corp": r["corp_name"], "stock": r["stock_code"],
            "market": "KOSPI" if r["corp_cls"] == "Y" else "KOSDAQ",
            "type": cap_type(r["report_nm"]), "rcept_no": r["rcept_no"],
            "rcept_dt": r.get("rcept_dt", ""),
            "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r['rcept_no']}"}

def _finalize(e):
    e["buy_date"] = t_minus(e["record_date"], 2)   # 권리부매수일(T-2) — 이날까지 매수해야 신주배정
    e["ex_date"] = t_minus(e["record_date"], 1)    # 권리락일(T-1)
    return e

def build_events(rows, cache):
    """(종목, 증자구분) 병합 — 정정 대비 최신 rcept_dt 우선, 신주배정기준일 있는 건만."""
    merged = {}
    for r in rows:
        d = _fields(r["rcept_no"], cache)
        if not d["record_date"]:
            continue
        key = (r["stock_code"], cap_type(r["report_nm"]))
        e = merged.get(key)
        if e is None or r.get("rcept_dt", "") >= e.get("rcept_dt", ""):
            row = _base(r)
            row["record_date"] = d["record_date"]
            row["ratio"] = d["ratio"]
            merged[key] = row
    return [_finalize(e) for e in merged.values()]

def _save(events):
    events.sort(key=lambda x: x.get("buy_date") or x.get("record_date"))
    payload = {"generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "count": len(events), "events": events}
    with _LOCK:
        save_json(CAP_PATH, payload)
    return payload

def main(days=MAX_DAYS):
    days = min(days, MAX_DAYS)
    today = datetime.date.today()
    bgn = (today - datetime.timedelta(days=days)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    watch = load_watchlist() or {}
    cache = load_cache()
    rows = []
    for cls in ("Y", "K"):
        rows += fetch_range(bgn, end, cls, "B")   # 증자결정 = 주요사항보고(B)
    rows = [r for r in rows if r.get("stock_code") in watch and _is_cap(r.get("report_nm", ""))]
    events = build_events(rows, cache)
    with _LOCK:
        save_json(CACHE_PATH, cache)   # 신규 파싱분 캐시 보존(다음 회차 재다운로드 방지)
    _save(events)
    print(f"TLS={TLS_MODE} | 기간 {bgn}~{end} | 감시대상 {len(watch)} | 증자결정 {len(rows)}건 → {len(events)}건(기준일有) 저장")
    for e in events:
        print(f"  {e['type']} {e['corp']}({e['stock']}) 기준일 {e['record_date']} · 매수 {e['buy_date']} · 락 {e['ex_date']}")

def upsert(cap_events):
    """collect_events 형식의 신규 증자 공시를 capital.json 에 즉시 증분 반영. 변경시 True."""
    with _LOCK:
        payload = json.load(open(CAP_PATH, encoding="utf-8")) if os.path.exists(CAP_PATH) else {"count": 0, "events": []}
        cache = load_cache()
        idx = {(e["stock"], e["type"]): e for e in payload["events"]}
        changed = False
        for r in cap_events:
            nm = r.get("title", "")
            if not _is_cap(nm):
                continue
            try:
                d = parse_cap(doc_text(r["rcept_no"]))
            except Exception:
                continue
            cache[r["rcept_no"]] = d
            if not d["record_date"]:
                continue   # 제3자배정 등 신주배정기준일 없음 → 캘린더 표시 대상 아님
            typ = cap_type(nm)
            key = (r["stock"], typ)
            cur = idx.get(key)
            if cur is None or r.get("date", "") >= cur.get("rcept_dt", ""):
                e = {"corp": r["corp"], "stock": r["stock"], "market": r.get("market", ""),
                     "type": typ, "ratio": d["ratio"], "record_date": d["record_date"],
                     "rcept_no": r["rcept_no"], "rcept_dt": r.get("date", ""),
                     "url": r.get("url", "")}
                _finalize(e)
                idx[key] = e
                changed = True
        if changed:
            payload["events"] = sorted(idx.values(), key=lambda x: x.get("buy_date") or x.get("record_date"))
            payload["count"] = len(payload["events"])
            payload["generated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_json(CAP_PATH, payload)
        save_json(CACHE_PATH, cache)
        return changed

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else MAX_DAYS
    main(n)
