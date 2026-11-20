# -*- coding: utf-8 -*-
"""
배당 캘린더용 데이터 추출 (원문 파싱 — 정형 API 없음)
- 대상 공시 2종:
  1) 현금ㆍ현물배당결정            → 1주당 배당금(원), (배당기준일·지급예정일)
  2) 현금ㆍ현물배당을위한주주명부폐쇄(기준일)결정 → 기준일
- 회사·배당구분별로 병합 → data/dividends.json
- 웹은 기준일 날짜에 "회사명 (금액원)" 표시
"""
import sys, os, re, json, datetime, calendar, threading
from fetch import listing, doc_text, has_body
from base import load_watchlist, DATA_DIR, save_json
from net import TLS_MODE
from krx_cal import t_minus, norm_date

_LOCK = threading.RLock()   # dividends.json 동시 쓰기 방지

def div_universe():
    """배당 수집 대상 = 감시종목(코스피200+코스닥150) ∪ 지수배당포인트용 ETF PDF 구성종목.
    공시 탭·토스트 대상(watchlist)은 그대로 두고 배당만 넓힌다 (2026-09-29 결정)."""
    import index_pdf
    return set(load_watchlist() or {}) | index_pdf.codes()

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

# ── 회차 — 배당 하나 = 종목 · 배당종류 · 사업연도 · 분기 (div_facts 도 같은 규칙을 씀) ──
TYPE_QUARTERS = {"분기배당": (1, 2, 3), "중간배당": (2,), "결산배당": (4,)}
ACC_PATH = os.path.join(DATA_DIR, "acc_mt.json")   # 종목별 결산월 (div_collect.acc_months 가 채움)

def quarter_end(fy, q, acc):
    m, y = acc - 3 * (4 - q), fy
    if m <= 0:
        m, y = m + 12, y - 1
    return datetime.date(y, m, calendar.monthrange(y, m)[1])

def round_of(t, record, decided, acc):
    """회차가 몇 사업연도 몇 분기 몫인지 — 기준일이 그 종류의 분기말이면 그 분기, 아니면 결정(또는 기준일) 직전 분기말"""
    ref = decided if decided and decided < record else record
    cands = [(fy, q, quarter_end(fy, q, acc)) for fy in range(record.year - 1, record.year + 2) for q in TYPE_QUARTERS[t]]
    same = [c for c in cands if c[2] == record]
    return same[0] if same else max((c for c in cands if c[2] < ref), key=lambda c: c[2])

def _round_key(stock, div_type, record, decided, acc):
    """같은 회차면 같은 키 — 정정으로 기준일이 바뀌어도 같은 사업연도·분기면 한 건. 배당종류를 못 읽었으면 기준일로."""
    if div_type not in TYPE_QUARTERS or not record:
        return (stock, div_type, record)
    d = lambda s: datetime.date.fromisoformat(s) if s else None
    fy, q, _ = round_of(div_type, d(record), d(decided), acc)
    return (stock, div_type, fy, q)

# --- 배당 원문 파싱 캐시 (dividends·div_collect 공용) ---
# rcept_no 별 파싱 결과를 저장/재사용 → 같은 공시 문서를 두 번 다시 내려받지 않는다.
# div_collect.collect 가 장기 이력을 채우고, dividends.main 이 그걸 그대로 재사용.
HIST_PATH = os.path.join(DATA_DIR, "div_history.json")

def load_doc_cache():
    """div_history.json(파싱 캐시)을 dict 로 로드. 없거나 깨졌으면 빈 dict."""
    if os.path.exists(HIST_PATH):
        try:
            return json.load(open(HIST_PATH, encoding="utf-8"))
        except Exception:
            return {}
    return {}

SAME_ROUND_GAP = 31

def assemble(entries, acc):
    """배당결정·기준일 공고 → 회차 (dividends·div_collect 공용). 접수 순으로 돌아 같은 회차는 나중 공시(정정)가 이긴다.
    entries: {kind decision|record, stock, corp, market, rcept_no, rcept_dt(YYYYMMDD), div_type, per_share, record_date, pay_date}
    acc: {종목: 결산월}. 반환: 회차마다 {stock, corp, market, div_type, record_date, per_share, pay_date,
    decided(첫 배당결정 접수일 ISO, 없으면 ""), rcept_no(최근 배당결정, 없으면 기준일 공고), rcept_dt(최근 접수일)}"""
    ymd = lambda s: f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    merged = {}
    def key_of(e, decided):
        if e["div_type"] not in TYPE_QUARTERS:   # 배당종류를 못 읽은 공시 → 같은 종목·같은 기준일 회차에 붙임
            same = [k for k, r in merged.items() if r["stock"] == e["stock"] and r["record_date"] == e["record_date"]]
            if len(same) == 1:
                return same[0]
        k = _round_key(e["stock"], e["div_type"], e["record_date"], decided, acc.get(e["stock"], 12))
        old = merged.get(k, {}).get("record_date")
        if old and e.get("record_date") and abs((datetime.date.fromisoformat(old) - datetime.date.fromisoformat(e["record_date"])).days) > SAME_ROUND_GAP:
            return (e["stock"], e["div_type"], e["record_date"])   # 배당종류를 잘못 읽은 다른 회차 — 정정은 기준일을 한 달 넘게 옮기지 않음
        return k
    order = sorted(entries, key=lambda e: (e["kind"] != "decision", e["rcept_dt"], e["rcept_no"]))
    for e in order:
        if e["kind"] == "record" and not e.get("record_date"):
            continue
        dec = e["kind"] == "decision"
        r = merged.setdefault(key_of(e, ymd(e["rcept_dt"]) if dec else None),
                              {"stock": e["stock"], "corp": e["corp"], "market": e["market"], "div_type": "",
                               "per_share": "", "record_date": "", "pay_date": "", "decided": "", "rcept_no": e["rcept_no"], "rcept_dt": ""})
        r["div_type"] = e["div_type"] if e.get("div_type") in TYPE_QUARTERS else r["div_type"] or e.get("div_type") or ""
        r["rcept_dt"] = max(r["rcept_dt"], e["rcept_dt"])
        if e.get("record_date"):
            r["record_date"] = e["record_date"]
        if dec:
            r["per_share"] = e.get("per_share") or r["per_share"]
            r["pay_date"] = e.get("pay_date") or r["pay_date"]
            r["decided"] = r["decided"] or ymd(e["rcept_dt"])
            r["rcept_no"] = e["rcept_no"]
    return [r for r in merged.values() if r["record_date"]]

def doc_fields(rcept_no, kind, cache=None):
    """공시 원문의 배당 필드(per_share/record_date/pay_date/div_type)를 반환.
    캐시에 있으면 재사용(다운로드 0), 없으면 그때만 원문 1회 다운로드+파싱.
    kind: 'decision' | 'record'."""
    c = cache.get(rcept_no) if cache is not None else None
    if c is not None:
        return {"per_share": c.get("per_share", ""), "record_date": c.get("record_date", ""),
                "pay_date": c.get("pay_date", ""), "div_type": c.get("div_type", "")}
    t = doc_text(rcept_no)
    return parse_decision(t) if kind == "decision" else parse_record(t)

def main(days=90):
    today = datetime.date.today()
    bgn = (today - datetime.timedelta(days=days)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    watch = div_universe()
    print(f"TLS={TLS_MODE} | 기간 {bgn}~{end} | 배당 수집대상 {len(watch)} (감시종목+ETF PDF)")

    cache = load_doc_cache()   # div_collect 가 채운 파싱 캐시(=div_history.json) — 캐시에 없는 원문만 받음
    rows = sorted((r for r in listing("I", days) if r["stock_code"] in watch and "자회사" not in r["report_nm"] and has_body(r["report_nm"])),
                  key=lambda r: (r["rcept_dt"], r["rcept_no"]))   # 접수 순 — 같은 회차는 나중 공시(정정)가 이김
    decisions = [r for r in rows if "현금ㆍ현물배당결정" in r["report_nm"]]
    records = [r for r in rows if "주주명부폐쇄" in r["report_nm"] and "배당" in r["report_nm"]]
    print(f"  배당결정 {len(decisions)} / 명부폐쇄 {len(records)} "
          f"(원문 캐시 없는 {sum(r['rcept_no'] not in cache for r in decisions + records)}건만 다운로드)")

    try:
        acc = {s: int(m) for s, m in json.load(open(ACC_PATH, encoding="utf-8")).items() if str(m).isdigit()}
    except (OSError, ValueError):
        acc = {}
    entries = []
    for kind, rs in (("decision", decisions), ("record", records)):
        for r in rs:
            try:
                d = doc_fields(r["rcept_no"], kind, cache)
            except Exception:
                continue
            entries.append({**d, "kind": kind, "stock": r["stock_code"], "corp": r["corp_name"],
                            "market": "KOSPI" if r["corp_cls"] == "Y" else "KOSDAQ", "rcept_no": r["rcept_no"], "rcept_dt": r["rcept_dt"]})
    events = []
    for e in assemble(entries, acc):
        e.pop("decided")
        e["url"] = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={e['rcept_no']}"
        e["confirm_date"] = t_minus_2(e["record_date"])  # 배당매수일 = 잔고확정일 (기준일 T-2 영업일)
        e["ex_date"] = t_minus(e["record_date"], 1)      # 배당락일 (기준일 T-1 영업일)
        events.append(e)
    events.sort(key=lambda x: x["confirm_date"] or x["record_date"])

    payload = {"generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "count": len(events), "events": events}
    out = os.path.join(DATA_DIR, "dividends.json")
    with _LOCK:
        save_json(out, payload)
    print(f"저장: {out} ({len(events)}건, 배당금有 {sum(1 for e in events if e['per_share'])}건)")

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    main(n)
