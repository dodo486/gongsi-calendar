# -*- coding: utf-8 -*-
"""배당 데이터 자가 점검 — 계산 결과가 규칙·정답표·과거 적중률과 맞는지 매번 검사한다.

- 날짜 관계: 매수일(T-2) < 배당락일(T-1) ≤ 기준일, 영업일 계산이 krx_cal 과 같은가
- 예상: 결산기말 고정이면 기준일 = 결산기말·분기말, 시즌 창 밖이면 경고가 붙었나, 이유 문장·내부 용어
- 회차·공시 짝: 회차의 결산기말 여부, 기준일 안내 자율공시가 붙은 회차가 그럴듯한가
- 장부·정책 대조(계산에 안 쓰는 출처와): 3분기보고서 누적 vs 공시 회차 합, 컨센 DPS 가 있는데 결산 예측 없음,
  숫자를 못 읽은 주주환원 공시, 회사가 그해 현금 총액·주당 금액을 밝혔는데 금액이 정책을 안 씀(주식 수를 모르는 등)
- 지수배당포인트: 월물 합계, 배당락일이 그 월물 구간 안인가
- 정답표: docs/배당예측-워크플로우.md §5.1 사례 8종목
- 적중률: verify 결과가 기준 아래로 떨어지지 않았나, 미래 오염 시험이 0건인가
- 들여오기 점검(store doc_coverage): 정답 묶음 실패(오류), 수집 종류에 안 걸린 배당 공시·안 받은 공시·옛 버전·정책 줄로 안 잡힌 숫자(경고)
- 정책 줄: 값이 말이 되나(배당성향 1~100%, 주당 금액이 작년 연간의 POLICY_JUMP 배 이내, 사업연도 5년 안), 나중에 나온 실제 배당과 맞나(정확한 금액·최소치)
- 거래소 배당락 안내와 장부 배당락일
- 확인 목록(div_queue.json): 지수·월물마다 확인 안 된 재료가 걸린 포인트 비중(경고)
읽는 것은 배당 장부(div_book.json)·지수배당포인트·검증 결과·들여오기 점검·확인 목록뿐. 결과는 data/out/dividend_check.json — 오류가 있으면 monitor 가 알림.
사용:
  python -m derive.dividend.check        # 오류 있으면 종료코드 1
"""
import datetime, re, sys
from core import store
from core.paths import save_json, load_json, stamp, DIVIDEND_CHECK_PATH, DIV_BOOK_PATH, DIVPOINTS_PATH, DIV_VERIFY_PATH, DIV_QUEUE_PATH
from core.krx_cal import t_minus, to_date, iso
from derive.dividend import facts
from derive.dividend.facts import quarter_end, SEASON_DAYS, OWNER_NI as NI, quarter_only, fin

METHODS = {"고정", "유동", "유동 가능", "판별 불가", "확정 기준일"}
JARGON = re.compile(r"\bA[12]\b|\bD1[34]\b|정관 [OX]\b|앵커")
POLICY_JUMP = 5   # 정책 줄 주당 금액이 작년 연간의 이 배수 넘으면 단위·종류를 잘못 읽은 의심
EXDIV_NEAR = 20   # 거래소 배당락 안내와 장부 회차를 짝지을 날 수
UNREAD_SHOW = 10   # 점검에 낱낱이 보여줄 못 읽은 주주환원 공시 수
CONS_GAP = 0.5   # 예상 금액이 컨센과 이만큼 넘게 다르면 경고 — 컨센은 과거 검증 전이라 순서엔 못 넣고 사람 눈으로
MIN_HIT = {"판정 적중(고정/유동)": 93, "월물 적중": 93, "유동 기준일 ±7일 이내": 85}
FIN_SHOW = 5   # 재무 대조에서 낱낱이 보여줄 수
MAX_AMOUNT_ERR = 20   # 결산 금액 백테스트 오차 중앙값(%)이 이보다 커지면 오류 — 금액이 나빠져도 알게(2026-11 16.7~18.3)

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


def check():
    errors, warns = [], []
    err = lambda where, msg: errors.append(f"{where}: {msg}")
    warn = lambda where, msg: warns.append(f"{where}: {msg}")
    today = datetime.date.today()

    def dates(where, e):
        rec, ex, buy = to_date(e.get("record_date")), to_date(e.get("ex_date")), to_date(e.get("confirm_date"))
        if not rec:
            return err(where, f"기준일 형식 오류 {e.get('record_date')!r}")
        if e.get("ex_date") != t_minus(e["record_date"], 1):
            err(where, f"배당락일 {e.get('ex_date')} ≠ 기준일 {rec} 의 T-1 영업일 {t_minus(e['record_date'], 1)}")
        if e.get("confirm_date") != t_minus(e["record_date"], 2):
            err(where, f"매수일 {e.get('confirm_date')} ≠ 기준일 {rec} 의 T-2 영업일 {t_minus(e['record_date'], 2)}")
        if buy and ex and not (buy < ex <= rec):
            err(where, f"날짜 순서 오류 매수 {buy} · 락 {ex} · 기준 {rec}")
        if not re.fullmatch(r"[0-9A-Z]{6}", e.get("code", "")):
            err(where, f"종목코드 형식 {e.get('code')!r}")
        if e.get("per_share") not in ("", None) and not str(e["per_share"]).isdigit():
            err(where, f"주당배당금 형식 {e.get('per_share')!r}")

    book = load_json(DIV_BOOK_PATH, None) or {"rows": [], "companies": {}, "grades": {}, "today_ids": [], "upcoming_ids": []}
    ids = [r["id"] for r in book["rows"]]
    if len(ids) != len(set(ids)):
        err("배당 장부", "같은 회차 id 중복")
    if not set(book["today_ids"]) | set(book["upcoming_ids"]) <= set(ids):
        err("배당 장부", "오늘·다음 만기 목록에 장부에 없는 회차")
    real = {}
    for r in book["rows"]:
        if r["kind"] == "div":
            real.setdefault(r["code"], []).append(to_date(r["record_date"]))
    for e in book["rows"]:
        w = f"{'확정' if e['kind'] == 'div' else '예상'} {e['name']}({e['code']}) {e.get('div_type', '')} {e['record_date']}"
        dates(w, e)
        if e["grade"] not in book["grades"]:
            err(w, f"알 수 없는 구분 {e['grade']!r}")
        if any(not m["date"] for m in e["marks"]):
            err(w, "캘린더 표시 날짜 없음")
        if e["kind"] != "est":
            continue
        rec, qe = to_date(e["record_date"]), to_date(e["quarter_end"])
        if e["method"] not in METHODS:
            err(w, f"알 수 없는 판정 {e['method']!r}")
        if e["method"] == "고정" and rec != qe:
            err(w, f"결산기말 고정인데 기준일 {rec} ≠ 결산기말·분기말 {qe}")
        if rec and qe and not (qe <= rec <= qe + datetime.timedelta(days=SEASON_DAYS[e["div_type"]]))                 and not any("시즌 창 밖" in x for x in e.get("warn", [])):
            err(w, f"기준일 {rec} 이 시즌 창(결산기말·분기말 {qe} ~ +{SEASON_DAYS[e['div_type']]}일) 밖인데 경고 없음")
        if rec and rec < today:
            err(w, f"예상 기준일 {rec} 이 이미 지남 — 지난 회차는 확정이거나 미실시여야 함")
        if not e.get("reason"):
            err(w, "이유 문장 없음")
        for txt in (e.get("reason", ""), e.get("why", ""), e.get("anchor", "")):
            if JARGON.search(txt):
                err(w, f"화면 문구에 내부 용어: {txt}")
        if any(r and rec and abs((r - rec).days) <= 30 for r in real.get(e["code"], [])):
            warn(w, "확정 배당과 기준일 ±30일 겹침")
        cons = next((x["value"] for x in e.get("amount_signals", []) if x["signal"] == "consensus_left"), None)
        if cons and e["per_share"] and abs(int(e["per_share"]) - cons) > CONS_GAP * cons:
            warn(w, f"예상 {int(e['per_share']):,}원이 컨센 {cons:,}원과 {CONS_GAP:.0%} 넘게 다름 — {e['amount_basis']}")

    # ── 회차·공시 짝 ──
    for s, c in book["companies"].items():
        for r in c["rounds"]:
            if r["fixed"] != (r["record_date"] == r["quarter_end"]):
                err(f"회차 {c['name']}({s}) {r['quarter']} {r['div_type']}",
                    f"결산기말 여부 표시 {r['fixed']} 가 기준일 {r['record_date']} · 결산기말 {r['quarter_end']} 와 안 맞음")
        if not c.get("acc_known"):
            warn(f"회사 {c['name']}({s})", "결산월 조회 실패 — 12월 가정")
        for n in c.get("notices", []):
            nd = to_date(n["date"])
            qe = quarter_end(*map(int, re.findall(r"\d+", n["quarter"])[:2]), c["acc_mt"])
            if nd and not (qe - datetime.timedelta(days=200) <= nd <= qe + datetime.timedelta(days=SEASON_DAYS[n["div_type"]])):
                err(f"예고 {c['name']}({s}) {n['date']}",
                    f"{n['quarter']} {n['div_type']} 에 붙었는데 공시일과 결산기말·분기말({qe:%Y-%m})이 너무 멂: {n['title']}")

    # ── 장부·정책 대조 ──
    est_q = {(e["code"], e.get("quarter")) for e in book["rows"] if e["kind"] == "est" and e.get("div_type") == "결산배당"}
    for s, c in book["companies"].items():
        w = f"회사 {c['name']}({s})"
        for fy, (rep, got) in c.get("paid_gaps", {}).items():
            warn(w, f"{fy} 3분기보고서 누적 배당 {rep:,.0f}원 ≠ 공시 회차 합 {got:,}원 — 회차 사업연도·금액 확인")
        done = {r["quarter"] for r in c["rounds"] if r["div_type"] == "결산배당"}
        for fy, dps in c.get("cons_dps", {}).items():
            if f"{fy} Q4" not in done and (s, f"{fy} Q4") not in est_q:
                warn(w, f"{fy} 연간 컨센 DPS {dps:,}원이 있는데 결산 예측 없음")
        for st in c.get("statements", []):
            if st["kind"] == "return_plan" and not st["rows"] and re.search(r"\d+\s*%|[\d,]{3,}\s*원|\d\s*[조억]\s*원", st["text"]):
                warn(w, f"주주환원 공시 숫자 못 읽음 — {st['date']} {st['title']} https://dart.fss.or.kr/dsaf001/main.do?rcpNo={st['rcept_no']}")
    for e in book["rows"]:
        if e["kind"] != "est" or e.get("div_type") != "결산배당":
            continue
        fy = int(e["quarter"][:4])
        pol = [p for p in book["companies"].get(e["code"], {}).get("policies", []) if p["fy"][0] <= fy <= p["fy"][1] and (p["kind"] == "cash" or p["kind"] == "dps" and p["hi"])]
        if pol and not any(x["signal"] == "stated" for x in e.get("amount_signals", [])):
            warn(f"예상 {e['name']}({e['code']}) {e['quarter']}", f"회사 정책({pol[-1]['date']} {pol[-1]['title']})이 걸렸는데 금액에 못 씀 — {e['amount_basis']}")

    # ── 들여오기 점검 ──
    cov = store.load("doc_coverage")
    for f in cov.get("gold", {}).get("fails", []):
        err(f"정답 묶음 {f['name']}", f"파서가 정답과 다름 — 정답 {f['want']} / 지금 {f['got']}")
    groups = {}
    for u in cov.get("uncaught", []):
        groups.setdefault(re.sub(r"\s+|\(.*?\)|\[.*?\]", "", u["title"]), []).append(u)
    for title, us in sorted(groups.items(), key=lambda x: -len(x[1])):
        warn("수집 안 하는 배당 공시", f"{title} {len(us)}건 — 예: {', '.join(u['name'] + ' ' + u['date'] for u in us[:3])}")
    for kind, k in cov.get("kinds", {}).items():
        if k["missing"]:
            warn(f"공시 받기 {kind}", f"목록 {k['listed']}건 중 안 받음 {len(k['missing'])}건 — {', '.join(k['missing'][:5])}")
        if k["stale"]:
            warn(f"공시 받기 {kind}", f"옛 파서 버전으로 읽음 {len(k['stale'])}건 — 다시 받는 중")
    unread = cov.get("unread", [])
    if unread:   # 전부는 하네스(두 번째 판독자)가 읽는 목록 — 점검엔 수와 주주환원 공시 앞 몇 건만
        warn("못 읽은 숫자", f"정책 문장 숫자가 정책 줄에 없는 공시 {len(unread)}건(주주환원 {sum(u['kind'] == 'return_plan' for u in unread)}·계획서 "
                           f"{sum(u['kind'] == 'plan' for u in unread)}) — 하네스 dividend-verify 가 읽음, 목록 store doc_coverage")
        for u in [u for u in unread if u["kind"] == "return_plan"][:UNREAD_SHOW]:
            warn(f"못 읽은 숫자 {u['name']}({u['code']})", f"{u['date']} {u['title'][:40]} — {', '.join(u['numbers'][:6])} https://dart.fss.or.kr/dsaf001/main.do?rcpNo={u['no']}")

    # ── 확인 목록: 사람·판독자가 아직 확인 안 한 재료가 걸린 포인트 ──
    queue = load_json(DIV_QUEUE_PATH, {})
    seen_q = {}
    for x in queue.get("items", []):   # 사람에게 보이는 줄이 읽히나·겹치나 — 날 숫자·영어 종류·None, 같은 회사 같은 글
        if re.search(r"\d{9,}|None|(?:dps|cash|payout|return|budget|buyback|high_div)", f"{x['item']} {x['value']}"):
            err(f"확인 목록 {x['name']}({x['code']})", f"사람이 못 읽는 값 — {x['item']}: {x['value']}")
        k = (x["code"], x["item"], x["value"])
        if k in seen_q:
            err(f"확인 목록 {x['name']}({x['code']})", f"같은 줄 두 번 — {x['item']}: {x['value']}")
        seen_q[k] = 1
    for m in queue.get("summary", []):
        if m["open"]:
            warn(f"확인 목록 {m['index']} {m['month']}", f"{m['total']:.2f}pt 중 {m['open']:.2f}pt({m['open'] / m['total']:.0%})가 확인 안 된 재료에 걸림 — 화면 🔎 확인 목록")

    # ── 정책 줄: 말이 되나 · 실제 배당과 맞나 ──
    for s, c in book["companies"].items():
        w = f"정책 줄 {c['name']}({s})"
        last = {int(fy): rep.get("dps") for fy, rep in c.get("reports", {}).items()}   # 사업보고서 연간 DPS — 실제 배당 정답
        for p in c.get("policies", []):
            src = f"{p['date']} {p.get('title', '')[:30]}"
            if p["kind"] in ("payout", "return") and any(v is not None and not 1 <= v <= 100 for v in (p.get("lo"), p.get("hi"))):
                warn(w, f"비율 {p.get('lo')}~{p.get('hi')}% 가 1~100% 밖 — {src}")
            ref = last.get(p["fy"][0] - 1)
            if p["kind"] == "dps" and ref and (p.get("hi") or p.get("lo") or 0) > POLICY_JUMP * ref:
                warn(w, f"주당 {p.get('hi') or p.get('lo'):,.0f}원이 작년 연간 {ref:,.0f}원의 {POLICY_JUMP}배 넘음 — 단위·종류 확인 · {src}")
            if p["fy"][0] and p["fy"][0] > int(p["date"][:4]) + 5:
                warn(w, f"사업연도 {p['fy']} 가 공시일({p['date']})보다 5년 넘게 뒤 — {src}")
            for fy in (f for f in range(p["fy"][0], p["fy"][1] + 1) if last.get(f) and int(p["date"][:4]) <= f):
                if p["kind"] == "dps" and p["slot"] == "연간":
                    if p.get("hi") and last[fy] != p["hi"]:
                        warn(w, f"{fy} 실제 연간 {last[fy]:,.0f}원(사업보고서) ≠ 정책 주당 {p['hi']:,.0f}원 — 파싱·정책 변경 확인 · {src}")
                    elif not p.get("hi") and last[fy] < p["lo"]:
                        warn(w, f"{fy} 실제 연간 {last[fy]:,.0f}원(사업보고서) < 정책 최소 {p['lo']:,.0f}원 — 파싱·적용 연도·정책 변경 확인 · {src}")

    # ── 거래소 배당락 안내 ──
    rows_by = {}
    for e in book["rows"]:
        rows_by.setdefault(e["code"], []).append(e)
    for s, c in book["companies"].items():
        for x in c.get("exdivs", []):
            near = [e for e in rows_by.get(s, []) if abs((to_date(e["ex_date"]) - to_date(x["ex_date"])).days) <= EXDIV_NEAR]
            if near and not any(e["ex_date"] == x["ex_date"] for e in near):
                err(f"배당락 {c['name']}({s})", f"거래소 안내 {x['ex_date']}({x['reason']}) ≠ 장부 {', '.join(e['ex_date'] for e in near)}")

    # ── 지수배당포인트 ──
    dp = load_json(DIVPOINTS_PATH, None)
    if dp:
        exps = [to_date(x) for x in dp.get("expiries", [])]
        dp_today = to_date(dp.get("today")) or today
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
                    ex = to_date(d["ex_date"])
                    if not (lo < ex <= hi):
                        err(w, f"배당락 {ex} 가 월물 구간({lo} 초과 ~ {hi}) 밖")
                lo = hi

    # ── 정답표 ──
    ev_by = {}
    for e in book["rows"]:
        if e["kind"] == "est":
            ev_by.setdefault(e["code"], []).append(e)
    for s, (nm, cases) in GOLDEN.items():
        c = book["companies"].get(s)
        if not c:
            err(f"정답표 {nm}({s})", "배당 장부 companies 에 없음")
            continue
        for name, ok in cases:
            try:
                good = ok(c, ev_by.get(s, []))
            except Exception as ex:
                good = False; name += f" (검사 중 오류 {type(ex).__name__})"
            if not good:
                err(f"정답표 {nm}({s})", f"기대와 다름 — {name}")

    # ── 적중률 ──
    ver = load_json(DIV_VERIFY_PATH, None)
    if not ver:
        warn("적중률", "div_verify.json 없음 — python -m derive.dividend.verify")
    else:
        sm = ver["summary"]
        for k, lim in MIN_HIT.items():
            m = re.search(r"\((\d+)%\)", str(sm.get(k, "")))
            if not m or int(m[1]) < lim:
                err("적중률", f"{k} {sm.get(k)} — 기준 {lim}% 미만")
        leak = sm.get("미래 오염 시험") or {}
        if leak.get("다른 예측", 1):
            err("미래 오염 시험", f"예측일 뒤의 사실을 바꾸니 예측 {leak.get('다른 예측', '?')}건이 바뀜 — 과거 예측에 미래가 섞임 · 예: {leak.get('예', [])[:1]}")
        e = (sm.get("결산 금액(12월 초)") or {}).get("전체", {}).get("오차 중앙값(%)")
        if e is None or e > MAX_AMOUNT_ERR:
            err("적중률", f"결산 금액 오차 중앙값 {e}% — 기준 {MAX_AMOUNT_ERR}% 초과")

    # ── 재무제표: 보고서의 분기 3개월 = 누적끼리 뺀 값 (기간·정정이 이어지나) ──
    q_gap, n_q = [], 0
    for c in facts.contexts():
        for fy in c["years"]:
            for q in (2, 3):
                start = iso(quarter_end(fy, q - 1, c["acc"]) + datetime.timedelta(days=1))
                given, made = fin(c, NI, "연결", start, iso(quarter_end(fy, q, c["acc"]))), quarter_only(c, NI, "연결", fy, q)
                if given is not None and made is not None:
                    n_q += 1
                    if abs(given - made) > 1e6:
                        q_gap.append(f"{c['name']}({c['code']}) {fy} Q{q} 보고서 3개월 {given / 1e8:,.0f}억 · 누적끼리 뺀 값 {made / 1e8:,.0f}억")
    if q_gap:
        warn("재무 대조 분기 3개월(보고서 ↔ 누적끼리 뺀 값)", f"{len(q_gap)}/{n_q}건 다름 — 예: {' / '.join(q_gap[:FIN_SHOW])}")

    return errors, warns

def main():
    errors, warns = check()
    save_json(DIVIDEND_CHECK_PATH, {"generated_at": stamp(),
                         "ok": not errors, "errors": errors, "warnings": warns})
    print(f"배당 점검: 오류 {len(errors)} · 경고 {len(warns)}")
    for x in errors:
        print("  ✖", x)
    for x in warns[:20]:
        print("  △", x)
    if len(warns) > 20:
        print(f"  … 경고 {len(warns) - 20}건 더 (data/out/dividend_check.json)")
    return errors

if __name__ == "__main__":
    sys.exit(1 if main() else 0)
