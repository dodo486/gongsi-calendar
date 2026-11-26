# -*- coding: utf-8 -*-
"""들여오기 점검 — 배당 대상 종목의 공시를 빠뜨리지 않았나·맞게 읽었나 (공시를 다 받은 뒤, 접속 없이 저장본만) → store doc_coverage.
docs = 등록표의 공시 종류(registry 가 넘김)
1. 안 걸린 공시: DART 목록의 배당 관련 제목 중 어떤 수집 종류에도 안 걸리고 의도적 제외(EXCLUDED)도 아닌 것
2. 종류별 받기: 목록에 있는데 안 받음 · 옛 파서 버전으로 읽음
3. 못 읽은 숫자: 주주환원 공시 원문·계획서 발췌에서 앞으로의 정책을 말하는 문장(지난 실적 문장 빼고)의 배당·환원 말 근처 숫자인데 어느 정책 줄에도 안 들어간 것
4. 정답 묶음: 사람이 확인한 공시(parsers/dividend_gold.json)를 지금 파서로 다시 읽어 정답과 다르면 실패"""
import json, os, re
from core import store, universe
from core.krx_cal import ymd, days_ago
from ingest.sources import dart
from ingest.parsers.dividend import policy_rows, PAST_WORDS, FORWARD_WORDS

DIV_TITLE = re.compile(r"배당|주주\s*환원|밸류업|기업가치\s*제고|자기\s*주식|자사주|기준일|주식\s*(?:분할|병합)")
EXCLUDED = [(re.compile(r"자회사"), "자회사의 공시"),
            (re.compile(r"주식소각결정"), "자사주 소각 — 이미 배당에서 빠진 주식이라 배당 받는 주식 수가 안 바뀜"),
            (re.compile(r"신탁계약해지"), "자사주 신탁 해지 — 산 주식은 자사주로 남아 배당 받는 주식 수가 안 바뀜")]
NEAR = re.compile(r"배당|환원|자사주|자기\s*주식|성향")

NUM = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:%|조\s*원|천억\s*원|억\s*원|원)")
GOLD_PATH = os.path.join(os.path.dirname(__file__), "parsers", "dividend_gold.json")
GOLD_KEYS = ("kind", "slot", "lo", "hi", "fy")


def run(docs):
    div = universe.div()
    listing = store.load("dart_listing")
    rows = [(ty, no, r) for ty in listing for no, r in listing[ty]["rows"].items()
            if r.get("stock_code") in div and dart.has_body(r["report_nm"])]
    uncaught, by_kind = [], {}
    for ty, no, r in rows:
        c = re.sub(r"\s+", "", r["report_nm"])
        d = next((d for d in docs if ty in d.lists and d.match(c, {"title": r["report_nm"]})), None)
        if d:
            by_kind.setdefault(d.kind, []).append((no, r))
        elif DIV_TITLE.search(c) and not any(rx.search(c) for rx, _ in EXCLUDED):
            uncaught.append({"no": no, "code": r["stock_code"], "name": r["corp_name"], "date": r["rcept_dt"], "title": r["report_nm"].strip()})
    kinds = {}
    for d in docs:
        if d.stocks is not universe.div or not d.parse:
            continue
        st, cut = store.load(d.kind), ymd(days_ago(d.days))
        want = [no for no, r in by_kind.get(d.kind, []) if r["rcept_dt"] >= cut]
        kinds[d.kind] = {"listed": len(want), "missing": [no for no in want if no not in st],
                         "stale": [no for no in want if no in st and st[no].get("v", 1) < d.version]}
    unread = []
    for kind, text_of in (("return_plan", lambda e: e.get("text") or ""), ("plan", lambda e: e.get("excerpt") or "")):
        for no, e in store.load(kind).items():
            t = text_of(e)
            if e.get("code") not in div or not t:
                continue
            got = " ".join(x.get("text", "") for x in e.get("rows", []))
            miss = set()
            for m in NUM.finditer(t):
                a, b = max(t.rfind(".", 0, m.start()), m.start() - 120), t.find(".", m.end())
                sent = t[a + 1: b if 0 <= b < m.end() + 120 else m.end() + 120]
                if (NEAR.search(t[max(0, m.start() - 40): m.end() + 40]) and FORWARD_WORDS.search(sent) and not PAST_WORDS.search(sent)
                        and m[0].split()[0] not in got):
                    miss.add(m[0].replace(" ", ""))
            miss = sorted(miss)
            if miss:
                unread.append({"kind": kind, "no": no, "code": e["code"], "name": e.get("name", ""), "date": e["rcept_dt"],
                               "title": e.get("subject") or e.get("title", ""), "numbers": miss[:12]})
    gold = json.load(open(GOLD_PATH, encoding="utf-8")) if os.path.exists(GOLD_PATH) else []
    fails = []
    for g in gold:
        key = lambda x: tuple(x.get(k) for k in GOLD_KEYS)
        got = sorted((list(key(x)) for x in policy_rows(g["text"])), key=str)
        want = sorted((list(key(x)) for x in g["rows"]), key=str)
        if got != want:
            fails.append({"name": g["name"], "want": want, "got": got})
    out = {"uncaught": uncaught, "kinds": kinds, "unread": unread, "gold": {"cases": len(gold), "fails": fails}}
    if out != {k: v for k, v in store.load("doc_coverage").items()}:
        store.save("doc_coverage", out)
        return True
    return False
