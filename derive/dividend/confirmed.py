# -*- coding: utf-8 -*-
"""확정 배당 — 저장소의 배당결정·기준일 공고(최근 90일 접수) → 회차 → data/out/dividends.json. 회차 규칙(assemble)은 배당 사실과 공용

  python -m derive.dividend.confirmed [일수=90]
"""
import sys, os, datetime, calendar
from core import store, universe
from core.paths import DART_VIEW, save_json, OUT_DIR
from core.krx_cal import t_minus

# ── 회차 — 배당 하나 = 종목 · 배당종류 · 사업연도 · 분기 ──
TYPE_QUARTERS = {"분기배당": (1, 2, 3), "중간배당": (2,), "결산배당": (4,)}

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

SAME_ROUND_GAP = 31

def assemble(entries, acc):
    """배당결정·기준일 공고 → 회차 (확정 배당·배당 사실 공용). 접수 순으로 돌아 같은 회차는 나중 공시(정정)가 이긴다.
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

def main(days=90):
    since = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y%m%d")
    stocks = universe.div()
    entries = [{**e, "rcept_no": no} for k in ("decision", "record") for no, e in store.load(k).items()
               if e["stock"] in stocks and e["rcept_dt"] >= since]
    acc = {s: int(m) for s, m in store.load("acc_mt").items() if str(m).isdigit()}
    events = []
    for e in assemble(entries, acc):
        e.pop("decided")
        e["url"] = DART_VIEW + e["rcept_no"]
        e["confirm_date"] = t_minus(e["record_date"], 2)  # 배당매수일 = 잔고확정일 (기준일 T-2 영업일)
        e["ex_date"] = t_minus(e["record_date"], 1)       # 배당락일 (기준일 T-1 영업일)
        events.append(e)
    events.sort(key=lambda x: x["confirm_date"] or x["record_date"])
    save_json(os.path.join(OUT_DIR, "dividends.json"),
              {"generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "count": len(events), "events": events})
    print(f"저장: dividends.json ({len(events)}건, 배당금有 {sum(1 for e in events if e['per_share'])}건)")

if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 90)
