# -*- coding: utf-8 -*-
"""
배당 캘린더용 데이터 추출 (원문 파싱 — 정형 API 없음)
- 대상 공시 2종:
  1) 현금ㆍ현물배당결정            → 1주당 배당금(원), (배당기준일·지급예정일)
  2) 현금ㆍ현물배당을위한주주명부폐쇄(기준일)결정 → 기준일
- 회사·배당구분별로 병합 → data/dividends.json
- 웹은 기준일 날짜에 "회사명 (금액원)" 표시
"""
import sys, io, os, re, json, zipfile, datetime, threading, urllib.request
import numpy as np
from fetch import fetch_range, load_watchlist, DATA_DIR, KEY, TLS_MODE, save_json
from krx_cal import holiday_dates, default_years

_LOCK = threading.RLock()   # dividends.json 동시 쓰기 방지 (main 재생성 vs upsert)

# 영업일 계산용 휴장일 배열(numpy) — 공용 달력(krx_cal)에서 생성
_HOL_ARR = np.array(sorted(str(d) for d in holiday_dates(default_years())), dtype="datetime64[D]")

def t_minus(record_iso, n):
    """배당기준일 기준 n번째 직전 영업일. n=1 → 배당락일(기준일 직전 영업일),
    n=2 → 배당매수일(배당부 마지막 매수일 = 잔고확정일).
    기준일이 휴장일(주말·공휴일, 예: 12/31 연말폐장)이어도 그 하루가 카운트를
    잡아먹지 않도록, '기준일 하루 전(달력)'부터 직전 영업일들을 센다."""
    if not record_iso:
        return ""
    try:
        d = np.datetime64(record_iso, "D") - np.timedelta64(1, "D")  # 기준일 하루 전
        return str(np.busday_offset(d, -(n - 1), roll="backward", holidays=_HOL_ARR))
    except Exception:
        return ""

def t_minus_2(record_iso):
    return t_minus(record_iso, 2)

DATE = r"(\d{4}[-.]\d{1,2}[-.]\d{1,2}|\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일)"

def norm_div_type(s):
    """배당구분 정규화 — 문서마다 ':' 포함, 표기 차이 등으로 병합 키가 갈라지는 것 방지"""
    s = re.sub(r"[^가-힣A-Za-z0-9]", "", s or "")
    for kw in ("중간", "분기", "결산", "임시", "특별"):
        if kw in s:
            return kw + "배당"
    return s

def norm_date(s):
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", s or "")
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""

def doc_text(rcept_no):
    url = f"https://opendart.fss.or.kr/api/document.xml?crtfc_key={KEY}&rcept_no={rcept_no}"
    with urllib.request.urlopen(url, timeout=30) as r:
        raw = r.read()
    zf = zipfile.ZipFile(io.BytesIO(raw))
    name = max(zf.namelist(), key=lambda n: zf.getinfo(n).file_size)
    data = zf.read(name)
    for enc in ("utf-8", "euc-kr", "cp949"):
        try: txt = data.decode(enc); break
        except Exception: txt = data.decode("utf-8", "replace")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", txt))

def parse_decision(t):
    per = re.search(r"1주당\s*배당금\(원\)\s*보통주식\s*([\d,]+)", t)
    rec = re.search(r"배당기준일\s*" + DATE, t)
    pay = re.search(r"배당금지급\s*예정일자\s*" + DATE, t)
    div = re.search(r"배당구분\s*(\S+)", t)
    ps = per.group(1).replace(",", "") if per else ""
    return {"per_share": ps if ps not in ("", "-") else "",
            "record_date": norm_date(rec.group(1)) if rec else "",
            "pay_date": norm_date(pay.group(1)) if pay else "",
            "div_type": norm_div_type(div.group(1) if div else "")}

def parse_record(t):
    rec = re.search(r"기준일\s*" + DATE, t)   # "(기준일) 시작일" 은 날짜 아니라 자동 skip
    div = re.search(r"배당구분\s*(\S+)", t)
    return {"record_date": norm_date(rec.group(1)) if rec else "",
            "div_type": norm_div_type(div.group(1) if div else "")}

def _match_key(index, stock, div_type):
    """(종목, 배당구분) 키 매칭 — 한쪽 문서의 배당구분 파싱이 비었을 때 같은 종목의 유일 엔트리에 병합"""
    key = (stock, div_type)
    if key in index:
        return key
    cands = [k for k in index if k[0] == stock]
    if len(cands) == 1 and "" in (div_type, cands[0][1]):
        return cands[0]
    return key

# --- 배당 원문 파싱 캐시 (dividends·research 공용) ---
# rcept_no 별 파싱 결과를 저장/재사용 → 같은 공시 문서를 두 번 다시 내려받지 않는다.
# research.build_history 가 장기 이력을 채우고, dividends.main 이 그걸 그대로 재사용.
HIST_PATH = os.path.join(DATA_DIR, "div_history.json")
_HEALED = {}   # 자가치유로 재파싱한 캐시 항목(rcept→entry) — main()에서 디스크에 영구 반영

def load_doc_cache():
    """div_history.json(파싱 캐시)을 dict 로 로드. 없거나 깨졌으면 빈 dict."""
    if os.path.exists(HIST_PATH):
        try:
            return json.load(open(HIST_PATH, encoding="utf-8"))
        except Exception:
            return {}
    return {}

def doc_fields(rcept_no, kind, cache=None):
    """공시 원문의 배당 필드(per_share/record_date/pay_date/div_type)를 반환.
    캐시에 있으면 재사용(다운로드 0), 없으면 그때만 원문 1회 다운로드+파싱.
    kind: 'decision' | 'record'.

    자가치유: 최초 수집 때 원문이 아직 안 채워져(초기 스냅샷) 기준일이 빈 값으로 캐시된
    경우, 최근(≤21일) 공시면 재파싱해 캐시를 채운다. 오래된 빈 항목(제3자배정·기준일 미정 등
    실제로 없는 경우)은 무한 재다운로드를 막기 위해 그대로 둔다."""
    c = cache.get(rcept_no) if cache is not None else None
    if c is not None and c.get("record_date"):
        return {"per_share": c.get("per_share", ""), "record_date": c.get("record_date", ""),
                "pay_date": c.get("pay_date", ""), "div_type": c.get("div_type", "")}
    if c is not None:   # 캐시에 있으나 기준일이 빔 → 최근분만 재파싱(자가치유)
        cutoff = (datetime.date.today() - datetime.timedelta(days=21)).strftime("%Y%m%d")
        if (c.get("rcept_dt", "") or "0") < cutoff:
            return {"per_share": c.get("per_share", ""), "record_date": "",
                    "pay_date": c.get("pay_date", ""), "div_type": c.get("div_type", "")}
    t = doc_text(rcept_no)
    d = parse_decision(t) if kind == "decision" else parse_record(t)
    if c is not None and (d.get("record_date") or d.get("per_share")):   # 비었던 캐시 채움
        for k in ("per_share", "record_date", "pay_date", "div_type"):
            if d.get(k):
                c[k] = d[k]
        _HEALED[rcept_no] = c   # main()에서 디스크(div_history.json)에 영구 반영
    return d

def main(days=90):
    today = datetime.date.today()
    bgn = (today - datetime.timedelta(days=days)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    watch = load_watchlist() or {}
    print(f"TLS={TLS_MODE} | 기간 {bgn}~{end} | 감시대상 {len(watch)}")

    cache = load_doc_cache()   # research 가 채운 파싱 캐시(=div_history.json)
    def _is_dec(nm): return "현금ㆍ현물배당결정" in nm
    def _is_rec(nm): return "주주명부폐쇄" in nm and "배당" in nm

    # (a) 과거분은 캐시에서 바로 재구성 — 90일 list.json 재스캔·문서 재다운로드 모두 0
    def _crow(rno, c):
        return {"rcept_no": rno, "stock_code": c.get("stock", ""), "corp_name": c.get("corp", ""),
                "corp_cls": "Y" if c.get("market") == "KOSPI" else "K", "rcept_dt": c.get("rcept_dt", "")}
    cached_kind = {}
    cached_rows = []
    for rno, c in cache.items():
        if c.get("kind") in ("decision", "record") and bgn <= c.get("rcept_dt", "") and c.get("stock") in watch:
            cached_rows.append(_crow(rno, c)); cached_kind[rno] = c["kind"]
    cached_rnos = set(cached_kind)

    # (b) 캐시가 비었으면(콜드스타트) 전체기간, 아니면 최근 며칠만 라이브 스캔해 신규/누락분 보강(자가치유)
    scan_days = days if not cache else 5
    scan_bgn = (today - datetime.timedelta(days=scan_days)).strftime("%Y%m%d")
    live = []
    for cls in ("Y", "K"):
        live += fetch_range(scan_bgn, end, cls, "I")
    live = [r for r in live if r.get("stock_code") in watch
            and "자회사" not in (r.get("report_nm") or "")
            and r["rcept_no"] not in cached_rnos
            and (_is_dec(r["report_nm"]) or _is_rec(r["report_nm"]))]

    decisions = [r for r in cached_rows if cached_kind[r["rcept_no"]] == "decision"] + [r for r in live if _is_dec(r["report_nm"])]
    records   = [r for r in cached_rows if cached_kind[r["rcept_no"]] == "record"]   + [r for r in live if _is_rec(r["report_nm"])]
    print(f"  배당결정 {len(decisions)} / 명부폐쇄 {len(records)} "
          f"(캐시 재사용 {len(cached_rows)} + 라이브 신규 {len(live)} · 신규분만 다운로드)")

    merged = {}  # (stock, div_type) -> entry
    def base(r):
        return {"corp": r["corp_name"], "stock": r["stock_code"],
                "market": "KOSPI" if r["corp_cls"] == "Y" else "KOSDAQ",
                "per_share": "", "record_date": "", "pay_date": "",
                "rcept_no": r["rcept_no"],
                "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r['rcept_no']}"}

    # 1) 배당결정 → 배당금(+기준일 fallback). 정정 대비 최신(rcept_dt) 우선
    for r in decisions:
        try: d = doc_fields(r["rcept_no"], "decision", cache)
        except Exception: continue
        key = _match_key(merged, r["stock_code"], d["div_type"])
        e = merged.setdefault(key, base(r))
        e["div_type"] = d["div_type"] or e.get("div_type", "")
        if r["rcept_dt"] >= e.get("_dec_dt", ""):
            e["_dec_dt"] = r["rcept_dt"]
            e["per_share"] = d["per_share"] or e["per_share"]
            e["pay_date"] = d["pay_date"] or e["pay_date"]
            e["rcept_no"] = r["rcept_no"]; e["url"] = base(r)["url"]
            if d["record_date"]: e["record_date"] = d["record_date"]

    # 2) 명부폐쇄(기준일) → 기준일 (배당결정 기준일 없을 때 우선 채움/덮어씀)
    for r in records:
        try: d = doc_fields(r["rcept_no"], "record", cache)
        except Exception: continue
        if not d["record_date"]: continue
        key = _match_key(merged, r["stock_code"], d["div_type"])
        e = merged.setdefault(key, base(r))
        e["div_type"] = d["div_type"] or e.get("div_type", "")
        if r["rcept_dt"] >= e.get("_rec_dt", ""):
            e["_rec_dt"] = r["rcept_dt"]; e["record_date"] = d["record_date"]

    events = []
    for e in merged.values():
        if not e["record_date"]:
            continue
        e["rcept_dt"] = max(e.get("_dec_dt", ""), e.get("_rec_dt", ""))  # 최근 접수일(YYYYMMDD)
        e["confirm_date"] = t_minus_2(e["record_date"])  # 배당매수일 = 잔고확정일 (기준일 T-2 영업일)
        e["ex_date"] = t_minus(e["record_date"], 1)      # 배당락일 (기준일 T-1 영업일)
        for k in ("_dec_dt", "_rec_dt"):
            e.pop(k, None)
        events.append(e)
    events.sort(key=lambda x: x["confirm_date"] or x["record_date"])

    payload = {"generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "count": len(events), "events": events}
    out = os.path.join(DATA_DIR, "dividends.json")
    with _LOCK:
        save_json(out, payload)
    print(f"저장: {out} ({len(events)}건, 배당금有 {sum(1 for e in events if e['per_share'])}건)")
    if _HEALED:   # 자가치유로 채운 캐시를 디스크에 영구 반영 (research는 재파싱 안 해 그대로 두면 고착)
        with _LOCK:
            disk = load_doc_cache()   # 최신 재로드 후 치유분만 병합(동시 추가분 클로버 방지)
            for rno, c in _HEALED.items():
                e = disk.get(rno)
                if e:
                    for k in ("per_share", "record_date", "pay_date", "div_type"):
                        if c.get(k):
                            e[k] = c[k]
            save_json(HIST_PATH, disk)
        print(f"  [자가치유] 배당 캐시 {len(_HEALED)}건 재파싱·복구 저장")
        _HEALED.clear()

def upsert(div_events):
    """새 배당 공시(collect_events 형식 dict 리스트)를 dividends.json에 즉시 증분 반영. 변경시 True"""
    with _LOCK:
        path = os.path.join(DATA_DIR, "dividends.json")
        payload = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {"count": 0, "events": []}
        idx = {(e["stock"], e.get("div_type", "")): e for e in payload["events"]}
        changed = False
        for r in div_events:
            nm = r.get("title", "")
            if "배당" not in nm or "자회사" in nm:
                continue
            is_record = "주주명부폐쇄" in nm
            try:
                t = doc_text(r["rcept_no"])
            except Exception:
                continue
            d = parse_record(t) if is_record else parse_decision(t)
            key = _match_key(idx, r["stock"], d.get("div_type", ""))
            e = idx.get(key)
            if e is None:
                e = {"corp": r["corp"], "stock": r["stock"], "market": r.get("market", ""),
                     "per_share": "", "record_date": "", "pay_date": "", "div_type": d.get("div_type", ""),
                     "rcept_no": r["rcept_no"], "url": r.get("url", ""),
                     "rcept_dt": r.get("date", ""), "confirm_date": ""}
                payload["events"].append(e); idx[key] = e
            else:
                e["div_type"] = d.get("div_type", "") or e.get("div_type", "")
            if is_record:
                if d["record_date"]: e["record_date"] = d["record_date"]
            else:
                if d["per_share"]: e["per_share"] = d["per_share"]
                if d["pay_date"]: e["pay_date"] = d["pay_date"]
                if d["record_date"]: e["record_date"] = d["record_date"]
                e["rcept_no"] = r["rcept_no"]; e["url"] = r.get("url", "")
            e["rcept_dt"] = max(e.get("rcept_dt", ""), r.get("date", ""))
            e["confirm_date"] = t_minus_2(e["record_date"])
            e["ex_date"] = t_minus(e["record_date"], 1)
            changed = True
        if changed:
            payload["events"] = [e for e in payload["events"] if e.get("record_date")]
            payload["events"].sort(key=lambda x: x.get("confirm_date") or x.get("record_date"))
            payload["count"] = len(payload["events"])
            payload["generated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_json(path, payload)
        return changed

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    main(n)
