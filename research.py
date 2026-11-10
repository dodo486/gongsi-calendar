# -*- coding: utf-8 -*-
"""
배당 리서치 — 배당 이력 장기 수집(증분 캐시) + 배당 선진화 판별 + 예상배당 추정
- data/div_history.json : 접수번호별 원문 파싱 캐시 (한 번 파싱한 문서는 다시 안 받음 → 재실행 빠름)
- data/research.json    : companies(종목별 선진화 판별) + events(예상배당, 웹 캘린더 노란색)

회차 단위 = [종목 × 분기(Q1~Q4)]. 결산배당=Q4, 중간배당=Q2, 분기배당=Q1~Q3.
선진화 판별 (회차별): 그 회차 첫 배당결정 공시일 < 기준일 → 선진화(배당액 먼저), 아니면 전통(깜깜이).
  종목·배당구분의 판정은 가장 최근 회차를 따른다(방식을 바꾸는 회사가 있음).
예상 기준일:
  · 전통   → 분기말 + 직전 회차의 (기준일 − 분기말) 일수  (보통 0일 = 분기말·12/31 고정)
  · 선진화 → 실적발표 예정일(결산실적공시예고·IR) + 직전 회차의 (기준일 − 공시일) 일수
             발표 예정일을 모르면 전통과 같은 분기말 기준 오프셋
  · 기준일(주주명부폐쇄) 공고만 나오고 배당결정 전이면 기준일은 공고값
  · 공고 전인 선진화 회차는 오늘+2주보다 빠를 수 없음 (기준일 2주 전 공고 의무)
  · 선진화인데 실적발표가 지나도(MISS_GRACE_DAYS) 배당 공시가 없으면 그 회차는 미실시로 보고 제외
금액: 같은 배당구분 직전 정기 회차 금액 반복 (이전 회차 중앙값의 2배 초과 회차는 특별배당 포함으로 보고 건너뜀).
companies[종목].history·pattern: 회차별 배당 이력과 '분기배당 Q1·Q2·Q3 + 결산배당 Q4 (연 4회)' 같은 구성 — 웹 상세 표시용.

사용: python research.py [이력일수=430]
"""
import sys, os, json, time, datetime, threading
from fetch import fetch_range, load_watchlist, DATA_DIR, save_json, parsed   # fetch import 시 콘솔 UTF-8 설정 공유
import dividends as dv
import earn_sched

HIST_PATH = dv.HIST_PATH   # 배당 파싱 캐시 경로는 dividends 와 공유(단일 출처)
OUT_PATH = os.path.join(DATA_DIR, "research.json")
_LOCK = threading.RLock()

QE_MD = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}
TYPE_QUARTERS = {"분기배당": (1, 2, 3), "중간배당": (2,), "결산배당": (4,)}
HORIZON_DAYS = 270     # 이보다 먼 예측은 표시 안 함 (~9개월)
MISS_GRACE_DAYS = 3    # 선진화 회사가 실적발표 후 이 일수가 지나도 배당 공시 없으면 미실시
IR_WINDOW_DAYS = 100   # 분기말 후 이 안에 열린 실적 IR 을 그 분기 실적발표로 본다
NOTICE_MIN_DAYS = 14   # 기준일은 법상 2주 전 공고 — 공고 전인 선진화 회차는 오늘+14일보다 빠를 수 없다
SAME_ROUND_DAYS = 45   # 기준일 공고가 예상 기준일 ±이 안이면 같은 회차 (divpoints 와 같은 기준)
SPECIAL_MULT = 2       # 같은 구분 이전 회차 금액 중앙값의 이 배수를 넘으면 특별배당 포함으로 본다

def load_hist():
    return dv.load_doc_cache()   # dividends 와 동일 로더 재사용

def build_history(days=430):
    """배당 관련 공시(배당결정+명부폐쇄)를 파싱해 rcept_no별 캐시로 누적. 신규분만 원문 요청"""
    hist = load_hist()
    today = datetime.date.today()
    watch = dv.div_universe()   # 감시종목 + 지수배당포인트용 ETF PDF 구성종목
    # DART list.json은 corp_code 없이 최대 3개월 조회 제한 → 85일 단위로 나눠 요청
    # (90일이면 1/31~5/1 처럼 2월을 낀 구간이 3개월을 넘어 0건으로 거부됨)
    rows, chunk_end = [], today
    remain = days
    while remain > 0:
        step = min(remain, 85)
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
        parse = dv.parse_record if kind == "record" else dv.parse_decision
        try:
            parsed(r["rcept_no"], lambda t: {
                "kind": kind, "stock": r["stock_code"], "corp": r["corp_name"],
                "market": "KOSPI" if r["corp_cls"] == "Y" else "KOSDAQ", "rcept_dt": r["rcept_dt"],
                "per_share": "", "pay_date": "", **parse(t)}, hist)
        except Exception:
            continue
        if (i + 1) % 50 == 0:
            print(f"    ...{i + 1}/{len(todo)} 파싱")
            with _LOCK:
                save_json(HIST_PATH, hist)   # 중간 저장 — 도중에 꺼져도 진행분 보존
        time.sleep(0.1)
    with _LOCK:
        save_json(HIST_PATH, hist)
    return hist

def _date(s):
    """'YYYY-MM-DD' 또는 'YYYYMMDD' → date"""
    s = s.replace("-", "")
    return datetime.date(int(s[:4]), int(s[4:6]), int(s[6:8]))

def quarter_end(fy, q):
    return datetime.date(fy, int(QE_MD[q][:2]), int(QE_MD[q][3:]))

def _last_qe_before(day):
    """day 직전(당일 제외) 분기말 → (연도, 분기)"""
    cands = [(day.year - 1, 4)] + [(day.year, q) for q in (1, 2, 3, 4)]
    return max(c for c in cands if quarter_end(*c) < day)

def round_quarter(div_type, record, first_dec):
    """회차가 몇 년 몇 분기 몫인지. 결산=Q4, 기준일이 분기말이면 그 분기, 아니면 공시 직전 분기말."""
    if div_type == "결산배당":
        return (record.year, 4) if record.strftime("%m-%d") == QE_MD[4] else (first_dec.year - 1, 4)
    for q, md in QE_MD.items():
        if record.strftime("%m-%d") == md:
            return record.year, q
    return _last_qe_before(first_dec)

def build_rounds(decisions):
    """한 종목의 배당결정 이력 → 회차 리스트. 같은 (배당구분, 기준일) 정정·주총 재공시는 한 회차로,
    선진화 판별은 첫 공시일, 금액은 마지막 공시 기준."""
    by = {}
    for e in decisions:
        by.setdefault((e["div_type"], e["record_date"]), []).append(e)
    rounds = []
    for (t, rd), es in by.items():
        es.sort(key=lambda x: x["rcept_dt"])
        record, first_dec = _date(rd), _date(es[0]["rcept_dt"])
        fy, q = round_quarter(t, record, first_dec)
        rounds.append({"type": t, "record": record, "first_dec": first_dec, "dps": es[-1].get("per_share", ""),
                       "fy": fy, "q": q, "qe": quarter_end(fy, q), "advanced": first_dec < record})
    rounds.sort(key=lambda r: r["first_dec"])
    for r in rounds:   # 특별배당 포함 회차 표시 — 같은 구분 이전 회차 금액 중앙값의 SPECIAL_MULT 배 초과
        prior = sorted(int(x["dps"]) for x in rounds
                       if x["type"] == r["type"] and x["first_dec"] < r["first_dec"] and x["dps"])
        med = prior[len(prior) // 2] if prior else 0
        r["special"] = bool(med and r["dps"] and int(r["dps"]) > SPECIAL_MULT * med)
    return rounds

def quarter_owner(rounds):
    """분기 → 그 분기를 맡는 배당구분 (분기·중간이 겹치면 최근 회차가 있는 쪽)"""
    by_type = {}
    for r in rounds:
        by_type.setdefault(r["type"], []).append(r)
    owner = {}
    for t, rs in by_type.items():
        qs = {r["q"] for r in rs}
        if t == "분기배당" and len(qs) >= 2:   # 분기배당 회사는 Q1~Q3 매 분기 지급으로 본다
            qs |= {1, 2, 3}
        for q in qs:
            if q not in owner or rs[-1]["first_dec"] > by_type[owner[q]][-1]["first_dec"]:
                owner[q] = t
    return by_type, owner

def pattern(owner):
    """'분기배당 Q1·Q2·Q3 + 결산배당 Q4 (연 4회)'"""
    by_t = {}
    for q in sorted(owner):
        by_t.setdefault(owner[q], []).append(f"Q{q}")
    return " + ".join(f"{t or '배당'} {'·'.join(qs)}" for t, qs in by_t.items()) + f" (연 {len(owner)}회)"

def judge(rounds):
    """배당구분별 최근 회차 → 선진화 판별 (verdict, evidence, {배당구분: 선진화 여부})"""
    latest = {}
    for r in rounds:
        latest[r["type"]] = r
    adv = {t: r["advanced"] for t, r in latest.items()}
    ev = [f"{t or '배당'} 최근 회차: 공시 {r['first_dec']} → 기준일 {r['record']} — "
          f"{'선진화(배당액 먼저)' if r['advanced'] else '전통(기준일 먼저, 깜깜이)'}" for t, r in latest.items()]
    if all(adv.values()):
        return "선진화", ev, adv
    if not any(adv.values()):
        return "구형(깜깜이 배당)", ev, adv
    return "부분 선진화(" + ", ".join(f"{t} {'선진화' if a else '전통'}" for t, a in adv.items()) + ")", ev, adv

def announce_date(earn, qe):
    """그 분기 실적발표 예정일 — 결산실적공시예고(결산기간 종료일 일치) 우선, 없으면 분기말 후 실적 IR"""
    pre = [_date(e["expected_date"]) for e in earn if e.get("period_end") == qe.isoformat()]
    if pre:
        return min(pre)
    ir = [_date(e["expected_date"]) for e in earn if e.get("src") == "IR"
          and qe < _date(e["expected_date"]) <= qe + datetime.timedelta(days=IR_WINDOW_DAYS)]
    return min(ir) if ir else None

def predict(rounds, notices, earn, today):
    """회차 이력 + 기준일 공고(금액 미공시) + 실적발표 예정일 → 아직 공시 안 된 회차의 예상 (events, 미실시 판단 근거)"""
    by_type, owner = quarter_owner(rounds)
    observed = {(r["fy"], r["q"]) for r in rounds}
    out, missed = [], []
    for q, t in owner.items():
        tpl = by_type[t][-1]   # 같은 배당구분 직전 회차 (타이밍 템플릿)
        reg = next((r for r in reversed(by_type[t]) if not r["special"]), tpl)   # 금액은 직전 정기 회차
        last_qe = max(r["qe"] for r in by_type[t])
        off = (tpl["record"] - tpl["qe"]).days
        gap = (tpl["record"] - tpl["first_dec"]).days
        for fy in (today.year - 1, today.year, today.year + 1):
            qe = quarter_end(fy, q)
            if qe <= last_qe or (fy, q) in observed:
                continue
            ann = announce_date(earn, qe) if tpl["advanced"] else None
            rec = ann + datetime.timedelta(days=gap) if ann else qe + datetime.timedelta(days=off)
            near = [n for n in notices if qe <= n and abs((n - rec).days) <= SAME_ROUND_DAYS]
            if near:   # 기준일은 공고됐고 금액만 미공시 → 기준일은 공고값
                rec = min(near, key=lambda n: abs((n - rec).days))
                how = f"기준일 공고됨 {rec} (금액 미공시)"
            elif ann and ann < today - datetime.timedelta(days=MISS_GRACE_DAYS):
                missed.append(f"⚠ {fy} Q{q} {t}: 실적발표 {ann} 후 배당 공시 없음 → 미실시로 보고 예상 제외")
                continue
            elif ann:
                how = f"선진화: 실적발표 예정 {ann} + {gap}일 (직전 회차 공시 {tpl['first_dec']} → 기준일 {tpl['record']})"
            else:
                how = (f"선진화: 실적발표 예정일 미공시 → 직전 회차와 같은 시기 (분기말 +{off}일)" if tpl["advanced"]
                       else f"전통: 기준일 분기말 고정{f' +{off}일' if off else ''} (직전 회차 기준일 {tpl['record']})")
            floor = today + datetime.timedelta(days=NOTICE_MIN_DAYS)
            if tpl["advanced"] and not near and today < rec < floor:
                rec = floor
                how += f" · 기준일 공고 전이라 {NOTICE_MIN_DAYS}일 뒤({floor}) 이후로 보정"
            if not today < rec <= today + datetime.timedelta(days=HORIZON_DAYS):
                continue
            amt = reg["dps"]
            basis = (f"{fy} Q{q} {t or '배당'} · {how} · 금액 직전 정기 회차({reg['fy']} Q{reg['q']}) "
                     f"{format(int(amt), ',') + '원' if amt else '미상'}"
                     + (f" (직전 {tpl['fy']} Q{tpl['q']} {int(tpl['dps']):,}원은 특별배당 포함으로 보고 제외)"
                        if reg is not tpl else ""))
            out.append({"div_type": t, "per_share": amt, "record_date": rec.isoformat(),
                        "quarter": f"{fy} Q{q}", "advanced": tpl["advanced"], "basis": basis})
    return out, missed

def build(days=430):
    hist = build_history(days)
    today = datetime.date.today()
    try:   # 배당 수집대상 전체의 실적발표 예정일 (예고·IR) — 선진화 회차의 기준일 앵커
        earn_all = earn_sched.collect(watch=dv.div_universe())
    except Exception as ex:
        print(f"  [리서치] 실적발표 예정일 수집 실패 — 분기말 기준으로만 추정: {ex}")
        earn_all = []
    earn = {}
    for e in earn_all:
        earn.setdefault(e["stock"], []).append(e)
    stocks, records = {}, {}
    for e in hist.values():
        if e["kind"] == "decision" and e.get("record_date"):
            stocks.setdefault(e["stock"], []).append(e)
        elif e["kind"] == "record" and e.get("record_date"):
            records.setdefault(e["stock"], set()).add(_date(e["record_date"]))
    companies, events = {}, []
    for stock, es in stocks.items():
        es.sort(key=lambda x: x["rcept_dt"])
        rounds = build_rounds(es)
        verdict, ev, adv = judge(rounds)
        notices = records.get(stock, set()) - {r["record"] for r in rounds}   # 기준일만 공고, 배당결정 아직
        preds, missed = predict(rounds, notices, earn.get(stock, []), today)
        companies[stock] = {"corp": es[-1]["corp"], "verdict": verdict, "evidence": ev + missed,
                            "pattern": pattern(quarter_owner(rounds)[1]),
                            "history": [{"quarter": f"{r['fy']} Q{r['q']}", "div_type": r["type"],
                                         "announced": r["first_dec"].isoformat(), "record_date": r["record"].isoformat(),
                                         "per_share": r["dps"], "advanced": r["advanced"], "special": r["special"]}
                                        for r in sorted(rounds, key=lambda r: r["record"], reverse=True)]}
        for p in preds:
            events.append({"corp": es[-1]["corp"], "stock": stock, "market": es[-1]["market"],
                           "div_type": p["div_type"], "per_share": p["per_share"], "quarter": p["quarter"],
                           "record_date": p["record_date"], "confirm_date": dv.t_minus_2(p["record_date"]),
                           "ex_date": dv.t_minus(p["record_date"], 1), "basis": p["basis"],
                           "verdict": "선진화" if p["advanced"] else "전통(깜깜이)"})
    events.sort(key=lambda x: x["confirm_date"] or x["record_date"])
    payload = {"generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "days": days, "companies": companies, "count": len(events), "events": events}
    with _LOCK:
        save_json(OUT_PATH, payload)
    n_pro = sum(1 for c in companies.values() if c["verdict"] == "선진화")
    n_miss = sum(1 for c in companies.values() for x in c["evidence"] if x.startswith("⚠"))
    print(f"저장: research.json — 판별 {len(companies)}종목(선진화 {n_pro}) · 예상배당 {len(events)}건 · 미실시 판단 {n_miss}건")
    return payload

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 430
    build(n)
