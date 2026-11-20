# -*- coding: utf-8 -*-
"""예상 실적발표 일정 — 저장소의 결산실적공시예고[예고]·기업설명회(IR)개최[IR] → data/earn_sched.json (웹 배당 캘린더 청록색)
같은 회차는 최신 접수가 이김. IR은 개최목적에 '실적'이 든 것만.

  python -m derive.earn_sched
"""
import os, datetime
from core import store, universe
from core.paths import DATA_DIR, DART_VIEW, save_json

SCHED_PATH = os.path.join(DATA_DIR, "earn_sched.json")
COLLECT_DAYS = 45          # 예고는 보통 발표 1~2주 전 — 45일치 접수분에서 다가오는 일정
IR_DAYS = 14               # IR 안내는 보통 개최 1~10일 전 접수
PAST_KEEP_DAYS = 14        # 지나간 예상 일정도 2주간 캘린더에 유지

def collect(watch=None):
    """예고 + IR → 다가오는 예정 실적발표 이벤트 (src로 구분). watch 없으면 감시종목"""
    watch = universe.watch() if watch is None else watch
    today = datetime.date.today()
    since = lambda n: (today - datetime.timedelta(days=n)).strftime("%Y%m%d")
    newest = {}
    for kind, days, key in (("earn_notice", COLLECT_DAYS, "period_end"), ("ir", IR_DAYS, "expected_date")):
        for no, d in sorted(store.load(kind).items(), key=lambda x: (x[1]["rcept_dt"], x[0])):
            if d["stock"] not in watch or d["rcept_dt"] < since(days) or not d.get("expected_date") or (kind == "ir" and "실적" not in d.get("purpose", "")):
                continue
            e = {"corp": d["corp"], "stock": d["stock"], "market": d["market"], "rcept_no": no, "rcept_dt": d["rcept_dt"],
                 "url": DART_VIEW + no, "src": "예고" if kind == "earn_notice" else "IR", "expected_date": d["expected_date"]}
            e.update({"period_end": d["period_end"]} if kind == "earn_notice" else {k: d.get(k, "") for k in ("time", "purpose", "sponsor")})
            newest[(kind, d["stock"], d[key])] = e
    keep_from = (today - datetime.timedelta(days=PAST_KEEP_DAYS)).isoformat()
    evs = [e for e in newest.values() if e["expected_date"] >= keep_from]
    evs.sort(key=lambda x: (x["expected_date"], x.get("time", ""), x["corp"]))
    return evs

Q_LABEL = {"03": "1Q", "06": "반기", "09": "3Q", "12": "연간"}   # 결산실적 예고의 결산기간 종료월 → 이름

def _texts(e):
    """화면 문구 — 분기 이름·캘린더 제목·출처"""
    ir = e["src"] == "IR"
    e["q_label"] = Q_LABEL.get((e.get("period_end") or "")[5:7], "")
    e["cal_title"] = e["corp"] + (" IR" if ir else f" {e['q_label']}" if e.get("period_end") else "")
    e["src_text"] = "기업설명회(IR)개최 안내공시" if ir else "결산실적공시예고 안내공시"
    return e

def main():
    evs = [_texts(e) for e in collect()]
    save_json(SCHED_PATH, {"generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "count": len(evs), "events": evs})
    n_ir = sum(1 for e in evs if e["src"] == "IR")
    print(f"earn_sched.json 저장: 예정 {len(evs)}건 (예고 {len(evs) - n_ir} · IR {n_ir})")

if __name__ == "__main__":
    main()
