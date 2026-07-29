# -*- coding: utf-8 -*-
"""
배당 캘린더용 데이터 추출 (원문 파싱 — 정형 API 없음)
- 대상 공시 2종:
  1) 현금ㆍ현물배당결정            → 1주당 배당금(원), (배당기준일·지급예정일)
  2) 현금ㆍ현물배당을위한주주명부폐쇄(기준일)결정 → 기준일
- 회사·배당구분별로 병합 → data/dividends.json
- 웹은 기준일 날짜에 "회사명 (금액원)" 표시
"""
import sys
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
import io, os, re, json, zipfile, datetime, urllib.request
import numpy as np, holidays as _hol
from fetch import fetch_range, load_watchlist, DATA_DIR, KEY, TLS_MODE

def _build_holidays():
    y = datetime.date.today().year
    years = [y - 1, y, y + 1]
    days = set(_hol.SouthKorea(years=years).keys())
    for yy in years:
        days.add(datetime.date(yy, 5, 1))    # 근로자의날 (증시 휴장)
        days.add(datetime.date(yy, 12, 31))   # 연말 폐장일
    return np.array(sorted(str(d) for d in days), dtype="datetime64[D]")

_HOL_ARR = _build_holidays()

def t_minus_2(record_iso):
    """배당기준일 → 잔고확정일 (T-2 영업일, 주말+공휴일 제외)"""
    if not record_iso:
        return ""
    try:
        return str(np.busday_offset(np.datetime64(record_iso, "D"), -2,
                                    roll="backward", holidays=_HOL_ARR))
    except Exception:
        return ""

DATE = r"(\d{4}[-.]\d{1,2}[-.]\d{1,2}|\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일)"

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
            "div_type": div.group(1) if div else ""}

def parse_record(t):
    rec = re.search(r"기준일\s*" + DATE, t)   # "(기준일) 시작일" 은 날짜 아니라 자동 skip
    div = re.search(r"배당구분\s*(\S+)", t)
    return {"record_date": norm_date(rec.group(1)) if rec else "",
            "div_type": div.group(1) if div else ""}

def main(days=90):
    today = datetime.date.today()
    bgn = (today - datetime.timedelta(days=days)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    watch = load_watchlist() or {}
    print(f"TLS={TLS_MODE} | 기간 {bgn}~{end} | 감시대상 {len(watch)}")

    rows = []
    for cls in ("Y", "K"):
        rows += fetch_range(bgn, end, cls, "I")
    rows = [r for r in rows if r.get("stock_code") in watch and "자회사" not in (r.get("report_nm") or "")]

    decisions = [r for r in rows if "현금ㆍ현물배당결정" in r["report_nm"]]
    records   = [r for r in rows if "주주명부폐쇄" in r["report_nm"] and "배당" in r["report_nm"]]
    print(f"  배당결정 {len(decisions)}건 / 명부폐쇄(기준일) {len(records)}건 파싱 중...")

    merged = {}  # (stock, div_type) -> entry
    def base(r):
        return {"corp": r["corp_name"], "stock": r["stock_code"],
                "market": "KOSPI" if r["corp_cls"] == "Y" else "KOSDAQ",
                "per_share": "", "record_date": "", "pay_date": "",
                "rcept_no": r["rcept_no"],
                "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r['rcept_no']}"}

    # 1) 배당결정 → 배당금(+기준일 fallback). 정정 대비 최신(rcept_dt) 우선
    for r in decisions:
        try: d = parse_decision(doc_text(r["rcept_no"]))
        except Exception: continue
        key = (r["stock_code"], d["div_type"])
        e = merged.setdefault(key, base(r)); e["div_type"] = d["div_type"]
        if r["rcept_dt"] >= e.get("_dec_dt", ""):
            e["_dec_dt"] = r["rcept_dt"]
            e["per_share"] = d["per_share"] or e["per_share"]
            e["pay_date"] = d["pay_date"] or e["pay_date"]
            e["rcept_no"] = r["rcept_no"]; e["url"] = base(r)["url"]
            if d["record_date"]: e["record_date"] = d["record_date"]

    # 2) 명부폐쇄(기준일) → 기준일 (배당결정 기준일 없을 때 우선 채움/덮어씀)
    for r in records:
        try: d = parse_record(doc_text(r["rcept_no"]))
        except Exception: continue
        if not d["record_date"]: continue
        key = (r["stock_code"], d["div_type"])
        e = merged.setdefault(key, base(r)); e["div_type"] = d["div_type"]
        if r["rcept_dt"] >= e.get("_rec_dt", ""):
            e["_rec_dt"] = r["rcept_dt"]; e["record_date"] = d["record_date"]

    events = []
    for e in merged.values():
        if not e["record_date"]:
            continue
        e["rcept_dt"] = max(e.get("_dec_dt", ""), e.get("_rec_dt", ""))  # 최근 접수일(YYYYMMDD)
        e["confirm_date"] = t_minus_2(e["record_date"])  # 잔고확정일 (기준일 T-2 영업일)
        for k in ("_dec_dt", "_rec_dt"):
            e.pop(k, None)
        events.append(e)
    events.sort(key=lambda x: x["confirm_date"] or x["record_date"])

    payload = {"generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "count": len(events), "events": events}
    out = os.path.join(DATA_DIR, "dividends.json")
    json.dump(payload, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"저장: {out} ({len(events)}건, 배당금有 {sum(1 for e in events if e['per_share'])}건)")

def upsert(div_events):
    """새 배당 공시(collect_events 형식 dict 리스트)를 dividends.json에 즉시 증분 반영. 변경시 True"""
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
        key = (r["stock"], d.get("div_type", ""))
        e = idx.get(key)
        if e is None:
            e = {"corp": r["corp"], "stock": r["stock"], "market": r.get("market", ""),
                 "per_share": "", "record_date": "", "pay_date": "", "div_type": d.get("div_type", ""),
                 "rcept_no": r["rcept_no"], "url": r.get("url", ""),
                 "rcept_dt": r.get("date", ""), "confirm_date": ""}
            payload["events"].append(e); idx[key] = e
        if is_record:
            if d["record_date"]: e["record_date"] = d["record_date"]
        else:
            if d["per_share"]: e["per_share"] = d["per_share"]
            if d["pay_date"]: e["pay_date"] = d["pay_date"]
            if d["record_date"]: e["record_date"] = d["record_date"]
            e["rcept_no"] = r["rcept_no"]; e["url"] = r.get("url", "")
        e["rcept_dt"] = max(e.get("rcept_dt", ""), r.get("date", ""))
        e["confirm_date"] = t_minus_2(e["record_date"])
        changed = True
    if changed:
        payload["events"] = [e for e in payload["events"] if e.get("record_date")]
        payload["events"].sort(key=lambda x: x.get("confirm_date") or x.get("record_date"))
        payload["count"] = len(payload["events"])
        payload["generated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        json.dump(payload, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return changed

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    main(n)
