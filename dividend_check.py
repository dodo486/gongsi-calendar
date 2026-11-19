# -*- coding: utf-8 -*-
"""배당 데이터 자가 점검 — 계산 결과가 규칙·정답표·과거 적중률과 맞는지 매번 검사한다.

- 날짜 관계: 매수일(T-2) < 배당락일(T-1) ≤ 기준일, 영업일 계산이 krx_cal 과 같은가
- 예상: 결산기말 고정이면 기준일 = 결산기말·분기말, 시즌 창 밖이면 경고가 붙었나, 이유 문장·내부 용어
- 회차·공시 짝: 회차의 결산기말 여부, 기준일 안내 자율공시가 붙은 회차가 그럴듯한가
- 지수배당포인트: 월물 합계, 배당락일이 그 월물 구간 안인가
- 정답표: docs/배당예측-워크플로우.md §5.1 사례 8종목
- 적중률: research.py --verify 결과가 기준 아래로 떨어지지 않았나
결과는 data/dividend_check.json — 오류가 있으면 monitor 가 알림, CI 는 배포를 멈춘다.
사용:
  python dividend_check.py        # 오류 있으면 종료코드 1
"""
import datetime, json, os, re, sys
from base import DATA_DIR, save_json
from krx_cal import t_minus

OUT_PATH = os.path.join(DATA_DIR, "dividend_check.json")
SEASON_DAYS = {"결산배당": 123, "중간배당": 92, "분기배당": 92}
METHODS = {"고정", "유동", "유동 가능", "판별 불가", "확정 기준일"}
JARGON = re.compile(r"\bA[12]\b|\bD1[34]\b|정관 [OX]\b|앵커")
MIN_HIT = {"판정 적중(고정/유동)": 93, "월물 적중": 93, "유동 기준일 ±7일 이내": 85}

# 사례 정답표 (설계 §5.1) — 종목코드: [(검사 이름, 함수(회사, 예상들) → bool)]
def _judg(c, t):
    return (c.get("judgments") or {}).get(t) or {}
GOLDEN = {
    "039490": ("키움증권", [("결산 = 결산기말 고정", lambda c, ev: _judg(c, "결산배당").get("method") == "고정"),
                         ("직전 결산 회차 금액 먼저 공시", lambda c, ev: any(r["div_type"] == "결산배당" and r.get("amount_first") for r in c["rounds"][:1]))]),
    "001720": ("신영증권", [("결산월 3", lambda c, ev: c["acc_mt"] == 3 and c["acc_known"]),
                         ("결산 = 결산기말(3/31) 고정", lambda c, ev: _judg(c, "결산배당").get("method") == "고정"
                                                       and all(e["record_date"].endswith("-03-31") for e in ev if e["div_type"] == "결산배당"))]),
    "011070": ("LG이노텍", [("결산 정관상 이사회 지정 가능", lambda c, ev: (c["charter"].get("결산") or {}).get("v") == "O"),
                          ("결산 결정기관 주총", lambda c, ev: c["organ"].get("결산") == "주주총회"),
                          ("결산 = 이사회 지정", lambda c, ev: _judg(c, "결산배당").get("method") == "유동")]),
    "086790": ("하나금융지주", [("다음 결산 회차엔 기준일 예고 없음(안내가 나온 회차에만)",
                            lambda c, ev: all(e["ladder"][2]["state"] == "off" for e in ev if e["div_type"] == "결산배당"))]),
    "005930": ("삼성전자", [("결산 정관상 결산기말 고정", lambda c, ev: (c["charter"].get("결산") or {}).get("v") == "X"),
                         ("결산·분기 = 결산기말·분기말 고정", lambda c, ev: all(j["method"] == "고정" for j in c["judgments"].values()))]),
    "095660": ("네오위즈", [("결산 = 선진화 정관 · 사례 없음", lambda c, ev: _judg(c, "결산배당").get("method") == "고정"
                                                          and _judg(c, "결산배당").get("grade") == "사례없음")]),
    "017670": ("SK텔레콤", [("분기 = 이사회 지정", lambda c, ev: _judg(c, "분기배당").get("method") == "유동")]),
    "004020": ("현대제철", [("결산 정관상 이사회 지정 가능", lambda c, ev: (c["charter"].get("결산") or {}).get("v") == "O"),
                         ("결산 = 이사회 지정", lambda c, ev: _judg(c, "결산배당").get("method") == "유동")]),
}


def _d(s):
    try:
        return datetime.date.fromisoformat(s)
    except (TypeError, ValueError):
        return None

def _load(name):
    path = os.path.join(DATA_DIR, name)
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None

def check():
    errors, warns = [], []
    err = lambda where, msg: errors.append(f"{where}: {msg}")
    warn = lambda where, msg: warns.append(f"{where}: {msg}")
    today = datetime.date.today()

    def dates(where, e, buy_key):
        rec, ex, buy = _d(e.get("record_date")), _d(e.get("ex_date")), _d(e.get(buy_key))
        if not rec:
            return err(where, f"기준일 형식 오류 {e.get('record_date')!r}")
        if e.get("ex_date") != t_minus(e["record_date"], 1):
            err(where, f"배당락일 {e.get('ex_date')} ≠ 기준일 {rec} 의 T-1 영업일 {t_minus(e['record_date'], 1)}")
        if e.get(buy_key) != t_minus(e["record_date"], 2):
            err(where, f"매수일 {e.get(buy_key)} ≠ 기준일 {rec} 의 T-2 영업일 {t_minus(e['record_date'], 2)}")
        if buy and ex and not (buy < ex <= rec):
            err(where, f"날짜 순서 오류 매수 {buy} · 락 {ex} · 기준 {rec}")
        if not re.fullmatch(r"[0-9A-Z]{6}", e.get("stock", "")):
            err(where, f"종목코드 형식 {e.get('stock')!r}")
        if e.get("per_share") not in ("", None) and not str(e["per_share"]).isdigit():
            err(where, f"주당배당금 형식 {e.get('per_share')!r}")

    # ── 확정 배당 ──
    div = _load("dividends.json") or {"events": []}
    seen = set()
    for e in div["events"]:
        w = f"확정 {e.get('corp')}({e.get('stock')}) {e.get('div_type')} {e.get('record_date')}"
        dates(w, e, "confirm_date")
        key = (e.get("stock"), e.get("div_type"), e.get("record_date"))
        if key in seen:
            err(w, "같은 종목·종류·기준일 중복")
        seen.add(key)

    # ── 예상 배당 ──
    res = _load("research.json") or {"events": [], "companies": {}}
    real = {}
    for e in div["events"]:
        real.setdefault(e.get("stock"), []).append(_d(e.get("record_date")))
    for e in res["events"]:
        w = f"예상 {e['corp']}({e['stock']}) {e['quarter']} {e['div_type']}"
        dates(w, e, "confirm_date")
        rec, qe = _d(e["record_date"]), _d(e["quarter_end"])
        if e["method"] not in METHODS:
            err(w, f"알 수 없는 판정 {e['method']!r}")
        if e.get("grade") not in res.get("grades", {}):
            err(w, f"알 수 없는 구분 {e.get('grade')!r}")
        if e["method"] == "고정" and rec != qe:
            err(w, f"결산기말 고정인데 기준일 {rec} ≠ 결산기말·분기말 {qe}")
        if rec and qe and not (qe <= rec <= qe + datetime.timedelta(days=SEASON_DAYS[e["div_type"]])) \
                and not any("시즌 창 밖" in x for x in e.get("warn", [])):
            err(w, f"기준일 {rec} 이 시즌 창(결산기말·분기말 {qe} ~ +{SEASON_DAYS[e['div_type']]}일) 밖인데 경고 없음")
        if rec and rec < today:
            err(w, f"예상 기준일 {rec} 이 이미 지남 — 지난 회차는 확정이거나 미실시여야 함")
        if not e.get("reason"):
            err(w, "이유 문장 없음")
        for txt in (e.get("reason", ""), e.get("why", ""), e.get("anchor", "")):
            if JARGON.search(txt):
                err(w, f"화면 문구에 내부 용어: {txt}")
        if any(r and rec and abs((r - rec).days) <= 30 for r in real.get(e["stock"], [])):
            warn(w, "확정 배당과 기준일 ±30일 겹침 — 화면은 확정만 보여줌")

    # ── 회차·공시 짝 ──
    for s, c in res["companies"].items():
        for r in c["rounds"]:
            if r["fixed"] != (r["record_date"] == r["quarter_end"]):
                err(f"회차 {c['corp']}({s}) {r['quarter']} {r['div_type']}",
                    f"결산기말 여부 표시 {r['fixed']} 가 기준일 {r['record_date']} · 결산기말 {r['quarter_end']} 와 안 맞음")
        if not c.get("acc_known"):
            warn(f"회사 {c['corp']}({s})", "결산월 조회 실패 — 12월 가정")
        for n in c.get("notices", []):
            nd = _d(n["date"])
            fy, q = map(int, re.findall(r"\d+", n["quarter"])[:2])
            qe_month = (c["acc_mt"] - 3 * (4 - q) - 1) % 12 + 1
            qe_year = fy if c["acc_mt"] - 3 * (4 - q) > 0 else fy - 1
            qe = datetime.date(qe_year, qe_month, 28)
            if nd and not (qe - datetime.timedelta(days=200) <= nd <= qe + datetime.timedelta(days=SEASON_DAYS[n["div_type"]])):
                err(f"예고 {c['corp']}({s}) {n['date']}",
                    f"{n['quarter']} {n['div_type']} 에 붙었는데 공시일과 결산기말·분기말({qe:%Y-%m})이 너무 멂: {n['title']}")

    # ── 배당 장부 ──
    book = _load("div_book.json")
    if book:
        ids = [r["id"] for r in book["rows"]]
        if len(ids) != len(set(ids)):
            err("배당 장부", "같은 회차 id 중복")
        for r in book["rows"]:
            w = f"장부 {r['corp']}({r['stock']}) {r.get('div_type', '')} {r['record_date']}"
            if r["grade"] not in book["grades"]:
                err(w, f"알 수 없는 구분 {r['grade']!r}")
            if any(not m["date"] for m in r["marks"]):
                err(w, "캘린더 표시 날짜 없음")
        if not set(book["today_ids"]) | set(book["upcoming_ids"]) <= set(ids):
            err("배당 장부", "오늘·다음 만기 목록에 장부에 없는 회차")

    # ── 지수배당포인트 ──
    dp = _load("divpoints.json")
    if dp:
        exps = [_d(x) for x in dp.get("expiries", [])]
        dp_today = _d(dp.get("today")) or today
        for c in dp.get("indices", []):
            lo = dp_today
            for m, hi in zip(c.get("months", []), exps):
                w = f"지수배당포인트 {c['name']} {m['label']}"
                pts = sum(x["points"] for d in m["days"] for x in d["items"])
                if abs(pts - m["total"]) > 0.01:
                    err(w, f"합계 {m['total']} ≠ 종목 합 {pts:.4f}")
                if abs(m["confirmed"] + m["estimated"] + m.get("undecided", 0) - m["total"]) > 0.01 and \
                        abs(m["confirmed"] + m["estimated"] - m["total"]) > 0.01:
                    err(w, f"확정 {m['confirmed']} + 예상 {m['estimated']} (+ 월물미정 {m.get('undecided', 0)}) ≠ 합계 {m['total']}")
                for d in m["days"]:
                    ex = _d(d["ex_date"])
                    if not (lo < ex <= hi):
                        err(w, f"배당락 {ex} 가 월물 구간({lo} 초과 ~ {hi}) 밖")
                lo = hi

    # ── 정답표 ──
    ev_by = {}
    for e in res["events"]:
        ev_by.setdefault(e["stock"], []).append(e)
    for s, (nm, cases) in GOLDEN.items():
        c = res["companies"].get(s)
        if not c:
            err(f"정답표 {nm}({s})", "research.json 에 없음")
            continue
        for name, ok in cases:
            try:
                good = ok(c, ev_by.get(s, []))
            except Exception as ex:
                good = False; name += f" (검사 중 오류 {type(ex).__name__})"
            if not good:
                err(f"정답표 {nm}({s})", f"기대와 다름 — {name}")

    # ── 적중률 ──
    ver = _load("research_verify.json")
    if not ver:
        warn("적중률", "research_verify.json 없음 — python research.py --verify")
    else:
        sm = ver["summary"]
        for k, lim in MIN_HIT.items():
            m = re.search(r"\((\d+)%\)", str(sm.get(k, "")))
            if not m or int(m[1]) < lim:
                err("적중률", f"{k} {sm.get(k)} — 기준 {lim}% 미만")

    return errors, warns

def main():
    errors, warns = check()
    save_json(OUT_PATH, {"checked_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                         "ok": not errors, "errors": errors, "warnings": warns})
    print(f"배당 점검: 오류 {len(errors)} · 경고 {len(warns)}")
    for x in errors:
        print("  ✖", x)
    for x in warns[:20]:
        print("  △", x)
    if len(warns) > 20:
        print(f"  … 경고 {len(warns) - 20}건 더 (data/dividend_check.json)")
    return errors

if __name__ == "__main__":
    sys.exit(1 if main() else 0)
