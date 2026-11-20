# -*- coding: utf-8 -*-
"""들여오기 한 바퀴 — 주기 원천 → DART 목록에서 새 접수번호만 → 그 종류 파서 한 번 → 저장소. 바뀐 종류 이름들을 돌려준다(날이 바뀌면 "day" 포함).
받은 공시는 다시 받지 않고(파서 버전을 올린 종류만 다시 읽음), 받기 실패는 그날 다시 시도하지 않는다.

  python -m ingest.run
"""
import re, datetime, threading
from concurrent.futures import ThreadPoolExecutor
from core import store
from ingest.registry import DOCS, FEEDS
from ingest.sources.dart import listing, doc_text, has_body

_lock = threading.Lock()

def _feeds(today):
    done = dict(store.load("_feeds"))
    changed = {"day"} if done.get("_day") != today else set()   # 날이 바뀜 — 오늘 기준 계산을 다시
    done["_day"] = today
    for f in FEEDS:
        if f.every == "day" and done.get(f.name) == today:
            continue
        try:
            if f.run():
                changed.add(f.name)
            done[f.name] = today
        except Exception as ex:
            print(f"  [들여오기] {f.name} 실패 — 다음 바퀴에 다시: {type(ex).__name__} {ex}")
    store.save("_feeds", done)
    return changed

def _docs(today):
    failed, changed = dict(store.load("_failed")), set()
    for ty in sorted({d.listing for d in DOCS}):
        docs = [d for d in DOCS if d.listing == ty]
        since = {d.kind: (datetime.date.today() - datetime.timedelta(days=d.days)).strftime("%Y%m%d") for d in docs}
        stored = {d.kind: store.load(d.kind) for d in docs}
        stocks, jobs = {}, []
        for r in listing(ty, max(d.days for d in docs)):
            if not has_body(r["report_nm"]):
                continue
            c = re.sub(r"\s+", "", r["report_nm"])
            d = next((d for d in docs if d.match(c)), None)
            if not d or r["rcept_dt"] < since[d.kind]:
                continue
            if d.kind not in stocks:
                stocks[d.kind] = d.stocks()
            if r["stock_code"] not in stocks[d.kind]:
                continue
            have = stored[d.kind].get(r["rcept_no"])
            if (have and have.get("v", 1) >= d.version) or failed.get(r["rcept_no"]) == today:
                continue
            jobs.append((d, r))
        if not jobs:
            continue
        print(f"  [들여오기] 목록 {ty} 새 공시 {len(jobs)}건")
        def one(job):
            d, r = job
            try:
                return job, d.parse(doc_text(r["rcept_no"])) if d.parse else {}
            except Exception:
                return job, None
        rows = {}
        with ThreadPoolExecutor(2) as ex:
            for (d, r), v in ex.map(one, jobs):
                if v is None:
                    failed[r["rcept_no"]] = today
                    continue
                rows.setdefault(d.kind, dict(store.load(d.kind)))[r["rcept_no"]] = {
                    "kind": d.kind, "stock": r["stock_code"], "corp": r["corp_name"],
                    "market": "KOSPI" if r["corp_cls"] == "Y" else "KOSDAQ", "rcept_dt": r["rcept_dt"],
                    "title": re.sub(r"\s+", " ", r["report_nm"]).strip(), "v": d.version, **v}
        for k, v in rows.items():
            store.save(k, v)
            changed.add(k)
    store.save("_failed", {k: v for k, v in failed.items() if v == today})
    return changed

def run():
    today = datetime.date.today().isoformat()
    with _lock:
        changed = _feeds(today) | _docs(today)
    print(f"  [들여오기] 바뀐 종류: {', '.join(sorted(changed)) or '없음'}")
    return changed

if __name__ == "__main__":
    run()
