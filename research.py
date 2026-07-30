# -*- coding: utf-8 -*-
"""
배당 리서치 — 배당 이력 장기 수집(증분 캐시) + 배당 선진화 판별 + 예상배당 추정
- data/div_history.json : 접수번호별 원문 파싱 캐시 (한 번 파싱한 문서는 다시 안 받음 → 재실행 빠름)
- data/research.json    : companies(종목별 선진화 판별) + events(예상배당, 웹 캘린더 노란색)

선진화 판별 근거:
  · 결산배당 기준일이 1~5월(주총 이후) → 배당액 확정 후 기준일 설정 = 선진화
  · 결산배당 기준일이 12월(연말)      → 기준일 먼저·배당액은 이듬해 확정 = 구형(깜깜이)
  · 분기·중간배당 기준일이 분기말이 아니고 공시 후 ~2주 뒤 → 기준일 분리 설정 = 선진화(2024 자본시장법 개정 반영)

사용: python research.py [이력일수=430]
"""
import sys
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
import os, json, time, calendar, datetime, threading
from fetch import fetch_range, load_watchlist, DATA_DIR, save_json
import dividends as dv

HIST_PATH = os.path.join(DATA_DIR, "div_history.json")
OUT_PATH = os.path.join(DATA_DIR, "research.json")
_LOCK = threading.RLock()

QUARTER_ENDS = {"03-31", "06-30", "09-30", "12-31"}
CYCLE_DEFAULT = {"분기배당": 3, "중간배당": 12, "결산배당": 12}   # 이력 1건뿐일 때 가정할 주기(개월)
HORIZON_MONTHS = 9   # 이보다 먼 예측은 표시 안 함

def load_hist():
    if os.path.exists(HIST_PATH):
        return json.load(open(HIST_PATH, encoding="utf-8"))
    return {}

def build_history(days=430):
    """배당 관련 공시(배당결정+명부폐쇄)를 파싱해 rcept_no별 캐시로 누적. 신규분만 원문 요청"""
    hist = load_hist()
    today = datetime.date.today()
    watch = load_watchlist() or {}
    # DART list.json은 corp_code 없이 최대 3개월 조회 제한 → 90일 단위로 나눠 요청
    rows, chunk_end = [], today
    remain = days
    while remain > 0:
        step = min(remain, 90)
        chunk_bgn = chunk_end - datetime.timedelta(days=step)
        for cls in ("Y", "K"):
            rows += fetch_range(chunk_bgn.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d"), cls, "I")
        chunk_end = chunk_bgn - datetime.timedelta(days=1)
        remain -= step + 1
    rows = [r for r in rows if r.get("stock_code") in watch and "자회사" not in (r.get("report_nm") or "")]
    todo = []
    for r in rows:
        nm = r["report_nm"]
        if "현금ㆍ현물배당결정" in nm:
            kind = "decision"
        elif "주주명부폐쇄" in nm and "배당" in nm:
            kind = "record"
        else:
            continue
        if r["rcept_no"] not in hist:
            todo.append((r, kind))
    print(f"  이력 캐시 {len(hist)}건 | 신규 파싱 {len(todo)}건")
    for i, (r, kind) in enumerate(todo):
        try:
            t = dv.doc_text(r["rcept_no"])
            d = dv.parse_record(t) if kind == "record" else dv.parse_decision(t)
        except Exception:
            d = {}
        hist[r["rcept_no"]] = {
            "kind": kind, "stock": r["stock_code"], "corp": r["corp_name"],
            "market": "KOSPI" if r["corp_cls"] == "Y" else "KOSDAQ", "rcept_dt": r["rcept_dt"],
            "per_share": d.get("per_share", ""), "record_date": d.get("record_date", ""),
            "pay_date": d.get("pay_date", ""), "div_type": d.get("div_type", ""),
        }
        if (i + 1) % 50 == 0:
            print(f"    ...{i + 1}/{len(todo)} 파싱")
            with _LOCK:
                save_json(HIST_PATH, hist)   # 중간 저장 — 도중에 꺼져도 진행분 보존
        time.sleep(0.1)
    with _LOCK:
        save_json(HIST_PATH, hist)
    return hist

def judge(decisions):
    """한 종목의 배당결정 이력 → 선진화 판별 (verdict, evidence 리스트)"""
    ev, pro, old, fixed = [], False, False, False
    for e in sorted(decisions, key=lambda x: x["record_date"]):
        rd = e["record_date"]
        mm, mmdd = int(rd[5:7]), rd[5:]
        label = e["div_type"] or "배당"
        if e["div_type"] == "결산배당":
            if 1 <= mm <= 5:
                pro = True
                ev.append(f"결산배당 기준일 {rd} — 주총 이후, 배당액 확정 → 기준일 설정 (선진화)")
            elif mm == 12:
                old = True
                ev.append(f"결산배당 기준일 {rd} — 연말 기준일 먼저, 배당액은 이듬해 주총 확정 (구형)")
        else:
            if mmdd not in QUARTER_ENDS and e["rcept_dt"] < rd.replace("-", ""):
                pro = True
                ev.append(f"{label} 기준일 {rd} — 분기말 아님, 공시({e['rcept_dt'][:4]}.{e['rcept_dt'][4:6]}.{e['rcept_dt'][6:]}) 후 기준일 분리 설정 (선진화)")
            elif mmdd in QUARTER_ENDS:
                fixed = True
                ev.append(f"{label} 기준일 {rd} — 분기말 고정 (전통 방식)")
    ev = list(dict.fromkeys(ev))[:6]   # 중복 제거 + 최대 6개
    if pro and old: return "부분 선진화", ev
    if pro: return "선진화", ev
    if old: return "구형(깜깜이 배당)", ev
    if fixed: return "구형 추정(분기말 고정)", ev
    return "판단불가", ev

def add_months(iso, n):
    y, m, d = int(iso[:4]), int(iso[5:7]), int(iso[8:10])
    t = m - 1 + n
    y, m = y + t // 12, t % 12 + 1
    return f"{y:04d}-{m:02d}-{min(d, calendar.monthrange(y, m)[1]):02d}"

def predict(decisions, today_iso):
    """종목의 배당결정 이력 → 다음 배당 예상 (미래 기준일만, 실제 공시 있으면 그 다음 주기로)"""
    out, by_type = [], {}
    for e in decisions:
        by_type.setdefault(e["div_type"], []).append(e)
    for dt_, es in by_type.items():
        es.sort(key=lambda x: x["record_date"])
        last = es[-1]
        if len(es) >= 2:   # 실제 관측된 지급 간격(개월) 우선
            d1, d0 = es[-1]["record_date"], es[-2]["record_date"]
            months = (int(d1[:4]) * 12 + int(d1[5:7])) - (int(d0[:4]) * 12 + int(d0[5:7]))
            cycle = months if 1 <= months <= 12 else CYCLE_DEFAULT.get(dt_, 0)
        else:
            cycle = CYCLE_DEFAULT.get(dt_, 0)
        if not cycle:
            continue
        nxt = add_months(last["record_date"], cycle)
        while nxt <= today_iso:
            nxt = add_months(nxt, cycle)
        if nxt > add_months(today_iso, HORIZON_MONTHS):
            continue
        amt = last.get("per_share", "")
        basis = (f"직전 {dt_ or '배당'}: 기준일 {last['record_date']}"
                 f"{', ' + format(int(amt), ',') + '원' if amt else ', 금액미상'}"
                 f" · 지급 주기 {cycle}개월 (이력 {len(es)}건 기반)")
        out.append({"div_type": dt_, "per_share": amt, "record_date": nxt, "basis": basis})
    return out

def build(days=430):
    hist = build_history(days)
    today_iso = datetime.date.today().strftime("%Y-%m-%d")
    stocks = {}
    for e in hist.values():
        if e["kind"] == "decision" and e.get("record_date"):
            stocks.setdefault(e["stock"], []).append(e)
    companies, events = {}, []
    for stock, es in stocks.items():
        verdict, ev = judge(es)
        companies[stock] = {"corp": es[-1]["corp"], "verdict": verdict, "evidence": ev}
        for p in predict(es, today_iso):
            events.append({"corp": es[-1]["corp"], "stock": stock, "market": es[-1]["market"],
                           "div_type": p["div_type"], "per_share": p["per_share"],
                           "record_date": p["record_date"], "confirm_date": dv.t_minus_2(p["record_date"]),
                           "ex_date": dv.t_minus(p["record_date"], 1),
                           "basis": p["basis"], "verdict": verdict})
    events.sort(key=lambda x: x["confirm_date"] or x["record_date"])
    payload = {"generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "days": days, "companies": companies, "count": len(events), "events": events}
    with _LOCK:
        save_json(OUT_PATH, payload)
    n_pro = sum(1 for c in companies.values() if "선진화" in c["verdict"] and "부분" not in c["verdict"])
    print(f"저장: research.json — 판별 {len(companies)}종목(선진화 {n_pro}) · 예상배당 {len(events)}건")
    return payload

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 430
    build(n)
