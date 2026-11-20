# -*- coding: utf-8 -*-
"""유상·무상증자 캘린더
- 대상 공시: 저장소의 유상·무상·유무상증자결정(감시대상, 파서는 ingest/parsers/capital.py)
- 신주배정기준일 → 권리부매수일(T-2)·권리락일(T-1) 계산 (배당 T-1/T-2 로직 재사용)
- 제3자배정 등 신주배정기준일이 없는 건은 자동 제외(권리락 없음)
- data/out/capital.json 저장 → 배당 캘린더에 무증(보라/연보라)·유증(파랑/하늘) 표시
사용:
  python -m derive.capital            # 최근 1년 유·무상증자 → data/out/capital.json
  python -m derive.capital 30         # 최근 30일
"""
import sys, os, math, datetime, threading
from core import store, universe, market as md_feed
from core.paths import DART_VIEW, save_json, OUT_DIR
from core.krx_cal import t_minus

_LOCK = threading.RLock()
CAP_PATH = os.path.join(OUT_DIR, "capital.json")
SCAN_DAYS = 365   # 공시일 기준 과거 1년 — 지난 증자도 공시가 오래됐다는 이유로 캘린더에서 빠지지 않게
CAP_KINDS = ("cap_mixed", "cap_rights", "cap_bonus")

def _finalize(e):
    e["buy_date"] = t_minus(e["record_date"], 2)   # 권리부매수일(T-2) — 이날까지 매수해야 신주배정
    e["ex_date"] = t_minus(e["record_date"], 1)    # 권리락일(T-1)
    if e["type"] == "무상증자":   # 신주상장일 T-2 부터 신주 잔고가 들어와 매도 가능
        e["sell_date"] = t_minus(e.get("listing_date", ""), 2)
    return e

def build_events(days):
    """같은 증자 = 같은 종목·증자구분이고 신주배정기준일이 120일 안 — 접수 순으로 돌아 정정 공시가 원본을 덮음.
    신주배정기준일 있는 건만."""
    since = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y%m%d")
    watch = universe.watch()
    rows = sorted(((no, r) for k in CAP_KINDS for no, r in store.load(k).items() if r["stock"] in watch and r["rcept_dt"] >= since),
                  key=lambda x: (x[1]["rcept_dt"], x[0]))
    merged = {}
    for no, r in rows:
        for typ in ("유상증자", "무상증자"):
            d = r.get(typ)
            if not d or not d["record_date"]:
                continue
            rec = datetime.date.fromisoformat(d["record_date"])
            key = next((k for k, e in merged.items() if k[:2] == (r["stock"], typ)
                        and abs((datetime.date.fromisoformat(e["record_date"]) - rec).days) <= 120),
                       (r["stock"], typ, no))
            merged[key] = {"corp": r["corp"], "stock": r["stock"], "market": r["market"], "type": typ, "rcept_no": no,
                           "rcept_dt": r["rcept_dt"], "url": DART_VIEW + no, **d}
    return [_finalize(e) for e in merged.values()], len(rows)

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
    events, n_rows = build_events(days)
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
    _save(events)
    print(f"과거 {days}일 | 증자결정 {n_rows}건 → {len(events)}건(기준일有) 저장")
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
