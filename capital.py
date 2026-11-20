# -*- coding: utf-8 -*-
"""유상·무상증자 캘린더용 데이터 추출 (원문 파싱 — 정형 API 없음)
- 대상 공시: 주요사항보고서(유상증자결정 / 무상증자결정 / 유무상증자결정) — 감시대상 한정(배당과 동일)
- 신주배정기준일 파싱 → 권리부매수일(T-2)·권리락일(T-1) 계산 (배당 T-1/T-2 로직 재사용)
- 제3자배정 등 신주배정기준일이 없는 건은 자동 제외(권리락 없음)
- data/capital.json 저장 → 배당 캘린더에 무증(보라/연보라)·유증(파랑/하늘) 표시
사용:
  python capital.py            # 최근 1년 유·무상증자 → data/capital.json
  python capital.py 30         # 최근 30일
"""
import sys, os, re, json, math, datetime, threading
from fetch import listing, parsed, has_body
from base import load_watchlist, DATA_DIR, save_json
from net import TLS_MODE
from krx_cal import t_minus, norm_date
import md_feed

_LOCK = threading.RLock()
CAP_PATH = os.path.join(DATA_DIR, "capital.json")
CACHE_PATH = os.path.join(DATA_DIR, "cap_cache.json")   # rcept_no -> 파싱결과(원문 재다운로드 방지)
SCAN_DAYS = 365   # 공시일 기준 과거 1년 — 지난 증자도 공시가 오래됐다는 이유로 캘린더에서 빠지지 않게

RE_REC = re.compile(r"신주배정기준일\s*(\d{4}\D+\d{1,2}\D+\d{1,2})")
# 무상: "1주당 신주배정 주식수 보통주식 (주) 0.5" / 유상: "1주당 신주배정주식수 (주) 0.24" 둘 다 대응
RE_RATIO = re.compile(r"1주당\s*신주배정\s*주식수\s*(?:보통주식)?\s*\(주\)\s*([\d.]+)")
# 신주 상장(입고)예정일 — 무상 "신주의 상장 예정일" / 유상 "신주의 상장예정일" 둘 다.
# 라벨 바로 뒤 날짜만 잡으므로 정정공시의 정정전(라벨~날짜 사이에 사유텍스트) 은 건너뛰고 정정후가 잡힌다.
RE_LIST = re.compile(r"신주의\s*상장\s*예정일\s*(\d{4}\D+\d{1,2}\D+\d{1,2})")
# 신주인수권증서 상장(입고) 시작일 — 유상 주주배정 "신주인수권증서 상장(예정)기간 : YYYY.MM.DD ~ ..."
RE_RIGHTS = re.compile(r"신주인수권증서\s*상장(?:예정)?기간\s*[:：]?\s*(\d{4}\D+\d{1,2}\D+\d{1,2})")
RE_SECTIONS = re.compile(r"Ⅰ\.\s*유상증자\s*1\.(.*?)Ⅱ\.\s*무상증자\s*1\.(.*)")
# 유상 신주 발행가 — 확정 우선, 없으면 예정. 정정공시는 정정표 뒤에 본문이 오므로 마지막 값을 쓴다.
RE_ISSUE = re.compile(r"(확정|예정)발행가\s*보통주식\s*\(원\)\s*([\d,]+)")

def _is_cap(nm):
    return has_body(nm) and any(k in (nm or "") for k in ("유상증자결정", "무상증자결정", "유무상증자결정"))

def parse_cap(t, nm):
    """증자구분 → 필드. 유무상증자결정은 본문 Ⅰ(유상)·Ⅱ(무상)를 따로 파싱. 신주배정기준일 없는 구분은 뺀다."""
    if "유무상증자결정" in nm:
        m = RE_SECTIONS.search(t)
        parts = {"유상증자": m.group(1), "무상증자": m.group(2)} if m else {}
    else:
        parts = {"유상증자" if "유상증자결정" in nm else "무상증자": t}
    out = {}
    for typ, s in parts.items():
        m = RE_REC.search(s)
        if not m:
            continue
        r = RE_RATIO.search(s)
        ml = RE_LIST.search(s)
        mr = RE_RIGHTS.search(s)
        issue = dict(RE_ISSUE.findall(s)) if typ == "유상증자" else {}
        out[typ] = {"record_date": norm_date(m.group(1)),
                    "issue_price": int((issue.get("확정") or issue.get("예정") or "0").replace(",", "")),
                    "ratio": r.group(1) if r else "",
                    "listing_date": norm_date(ml.group(1)) if ml else "",   # 신주 상장(입고)일
                    # 신주인수권증서 상장(입고)일 — 유상 주주배정에만 존재, 무상 오탐 방지
                    "rights_date": norm_date(mr.group(1)) if mr and typ == "유상증자" else ""}
    return out

def load_cache():
    if os.path.exists(CACHE_PATH):
        try:
            return json.load(open(CACHE_PATH, encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _finalize(e):
    e["buy_date"] = t_minus(e["record_date"], 2)   # 권리부매수일(T-2) — 이날까지 매수해야 신주배정
    e["ex_date"] = t_minus(e["record_date"], 1)    # 권리락일(T-1)
    if e["type"] == "무상증자":   # 신주상장일 T-2 부터 신주 잔고가 들어와 매도 가능
        e["sell_date"] = t_minus(e.get("listing_date", ""), 2)
    return e

def build_events(rows, cache):
    """같은 증자 = 같은 종목·증자구분이고 신주배정기준일이 120일 안 — 접수 순으로 돌아 정정 공시가 원본을 덮음.
    신주배정기준일 있는 건만."""
    merged = {}
    for r in sorted(rows, key=lambda r: (r.get("rcept_dt", ""), r["rcept_no"])):
        try:
            parts = parsed(r["rcept_no"], lambda t: parse_cap(t, r["report_nm"]), cache)
        except Exception:
            continue
        for typ, d in parts.items():
            if not d["record_date"]:
                continue
            rec = datetime.date.fromisoformat(d["record_date"])
            key = next((k for k, e in merged.items() if k[:2] == (r["stock_code"], typ)
                        and abs((datetime.date.fromisoformat(e["record_date"]) - rec).days) <= 120),
                       (r["stock_code"], typ, r["rcept_no"]))
            merged[key] = {"corp": r["corp_name"], "stock": r["stock_code"],
                           "market": "KOSPI" if r["corp_cls"] == "Y" else "KOSDAQ",
                           "type": typ, "rcept_no": r["rcept_no"],
                           "rcept_dt": r.get("rcept_dt", ""),
                           "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r['rcept_no']}",
                           **d}
    return [_finalize(e) for e in merged.values()]

def _won(n):
    return f"{n:,.0f}"

def _pct(v):
    return f"{v:+.2f}%" if v else "0%"

def _view(e, today_s):
    """화면이 그대로 그리는 값 — 캘린더 표시(날짜·제목·색 클래스·매수일/락일 구분), 다음 일정, 무상 1주 보유 손익"""
    bonus = e["type"] == "무상증자"
    pre = "무상" if bonus else "유상"
    even = (f" 본전 {_won(e['base_price'])}·현재 {_won(e['price'])} {_pct(e['ret'])}" if e.get("ret") is not None
            else f" 예상기준가 {_won(e['base_price'])}" if e.get("base_price") else "")
    marks = [{"type": "buy", "date": e["buy_date"], "title": f"[{pre}매수일] {e['corp']}", "cls": "cap-bonus-buy" if bonus else "cap-rights-buy"},
             {"type": "ex", "date": e["ex_date"], "title": f"[{pre}권리락] {e['corp']}{even}", "cls": "cap-bonus-ex" if bonus else "cap-rights-ex"}]
    if e.get("sell_date"):
        marks.append({"type": "", "date": e["sell_date"], "title": f"[무상매도가능] {e['corp']} ⚠엠피스 장부단가{even}", "cls": "cap-sell"})
    if e.get("listing_date"):
        tail = even if bonus else (f" 발행가 {_won(e['issue_adj'])}·현재 {_won(e['price'])} {_pct(e['issue_ret'])}" if e.get("issue_ret") is not None else "")
        marks.append({"type": "", "date": e["listing_date"], "title": f"[{pre}신주상장{'·락금액 보정' if bonus else ''}] {e['corp']}{tail}", "cls": "cap-list"})
    if e.get("rights_date"):
        marks.append({"type": "", "date": e["rights_date"], "cls": "cap-warrant",
                      "title": f"[유상신주인수권] {e['corp']}" + (f" 본전 {_won(e['even_rights'])}·" if e.get("even_rights") is not None else " ")
                               + (f"이론 {_won(e['rights_theo'])}" if e.get("rights_theo") is not None else "")})
    e["id"] = f"{e['rcept_no']}|{e['type']}"
    e["marks"] = [m for m in marks if m["date"]]
    sched = [("매수일", e.get("buy_date")), ("권리락", e.get("ex_date")), ("증서상장", e.get("rights_date")),
             ("매도가능", e.get("sell_date")), ("신주상장", e.get("listing_date"))]
    e["next"] = next(({"label": k, "date": d} for k, d in sorted((x for x in sched if x[1] and x[1] >= today_s), key=lambda x: x[1])), None)
    r, base, p = float(e.get("ratio") or 0), e.get("base_price"), e.get("sell_price") or e.get("price")
    if bonus and r and base and p and e.get("ret") is not None:
        lock, back = round(base * r), round(p * r)
        e["hold"] = {"ratio": e["ratio"], "base": base, "price": p, "lock": lock, "back": back, "gap": back - lock,
                     "price_src": "매도 가능일 종가" if e.get("sell_price") is not None else "현재가",
                     "note": "" if e.get("sell_price") is not None else " (매도 가능일 전 — 현재가로 계산)"}
    return e

def _save(events):
    today_s = datetime.date.today().isoformat()
    events = [_view(e, today_s) for e in events]
    events.sort(key=lambda x: x.get("buy_date") or x.get("record_date"))
    payload = {"generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "today": today_s,
               "ongoing_ids": [e["id"] for e in sorted((e for e in events if e["next"]), key=lambda e: e["next"]["date"])],
               "count": len(events), "events": events}
    with _LOCK:
        save_json(CAP_PATH, payload)
    return payload

def main(days=SCAN_DAYS):
    today = datetime.date.today()
    watch = load_watchlist() or {}
    cache = load_cache()
    rows = [r for r in listing("B", days)   # 증자결정 = 주요사항보고(B)
            if r["stock_code"] in watch and _is_cap(r["report_nm"])]
    events = build_events(rows, cache)
    today_s = today.isoformat()
    bonus_done = [(e["stock"], e["ex_date"], float(e["ratio"] or 0))
                  for e in events if e["type"] == "무상증자" and e["ex_date"] <= today_s]
    for e in events:
        daily = md_feed.daily_price(e["stock"], 250)   # 수정주가
        if not daily:
            continue
        price = md_feed.stock_rate(e["stock"]).get("price") or daily[0]["close"]
        r = float(e["ratio"] or 0)
        e["price"] = price
        # 발행가를 현재 주식 기준으로 — 이 건 권리락 뒤에 끝난 무상증자만큼 나눈다
        issue = e["issue_price"] / math.prod(1 + q for s, ex, q in bonus_done if s == e["stock"] and ex > e["ex_date"])
        if e["ex_date"] > today_s:   # 권리락 전: 지금 주가를 권리부 종가로 보고 예상 기준가
            if e["type"] == "무상증자":
                e["base_price"] = round(price / (1 + r))
            elif issue:
                e["base_price"] = round((price + issue * r) / (1 + r))
        if issue:
            e["issue_adj"] = round(issue)
            e["rights_theo"] = max(round(price - issue), 0)   # 신주인수권증서 이론가 = 주가 − 발행가
            e["issue_ret"] = round((price / issue - 1) * 100, 2)   # 청약 신주 발행가 대비
        if e["ex_date"] <= today_s:   # 권리락 후: 수정주가의 권리부 마지막날(T-2) 종가 = 권리락 기준가 = 본전 주가
            base = next((d["close"] for d in daily if d["date"] == e["buy_date"].replace("-", "")), None)
            if base:
                e["base_price"] = base
                if e["type"] == "무상증자":
                    e["ret"] = round((price / base - 1) * 100, 2)
                    # 신주 잔고가 돌아온 날(매도 가능일) 종가 — 락 금액 대비 돌아온 금액 비교용
                    if e["sell_date"] and e["sell_date"] <= today_s:
                        e["sell_price"] = next((d["close"] for d in daily if d["date"] == e["sell_date"].replace("-", "")), None)
                elif issue:   # 보유 수익률 = (주가 + r × 증서 이론가) ÷ 권리부 매수가 − 1
                    e["even_rights"] = round(base - issue)   # 본전 증서가 = 기준가 − 발행가
                    e["ret"] = round(((price + r * e["rights_theo"]) / (base * (1 + r) - issue * r) - 1) * 100, 2)
    with _LOCK:
        save_json(CACHE_PATH, cache)   # 신규 파싱분 캐시 보존(다음 회차 재다운로드 방지)
    _save(events)
    print(f"TLS={TLS_MODE} | 과거 {days}일 | 감시대상 {len(watch)} | 증자결정 {len(rows)}건 → {len(events)}건(기준일有) 저장")
    for e in events:
        extra = ""
        if e.get("listing_date"): extra += f" · 신주상장 {e['listing_date']}"
        if e.get("rights_date"):  extra += f" · 신주인수권상장 {e['rights_date']}"
        if "base_price" in e:     extra += f" · 기준가 {e['base_price']:,.0f} · 현재 {e['price']:,.0f}"
        if "ret" in e:            extra += f" ({e['ret']:+.2f}%)"
        if "rights_theo" in e:    extra += f" · 발행가 {e['issue_adj']:,} · 증서 이론가 {e['rights_theo']:,}"
        if "even_rights" in e:    extra += f" (본전 {e['even_rights']:,})"
        print(f"  {e['type']} {e['corp']}({e['stock']}) 기준일 {e['record_date']} · 매수 {e['buy_date']} · 락 {e['ex_date']}{extra}")

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else SCAN_DAYS
    main(n)
