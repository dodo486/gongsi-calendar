# -*- coding: utf-8 -*-
"""들여오기 한 바퀴 — 주기 원천 → 공시 목록(DART·KIND)에서 새 접수번호만 → 그 종류 파서 한 번 → 저장소.
바뀐 종류 이름들을 돌려준다(날이 바뀌면 "day" 포함). 받은 공시는 다시 받지 않고(파서 버전을 올린 종류만 다시 읽음),
받기 실패는 RETRY_MIN 분 뒤에 다시. fast = 장중 고속 바퀴 — KIND 오늘 목록만.

  python -m ingest.run
"""
import os, re, time, datetime, threading
from concurrent.futures import ThreadPoolExecutor
from core import store, ops, universe
from core.krx_cal import is_business_day, ymd, days_ago
from ingest.registry import DOCS, FEEDS
from ingest.sources import dart, kind

RETRY_MIN = 10
KIND_RECHECK_DAYS = 2      # KIND 목록은 오늘·어제만 늦은 공시 대비 다시, 지난 날짜는 저장본
_lock = threading.Lock()

def _feeds(today):
    done = dict(store.load("_feeds"))
    changed = {"day"} if done.get("_day") != today else set()   # 날이 바뀜 — 오늘 기준 계산을 다시
    done["_day"] = today
    for f in FEEDS:
        if f.every == "day" and done.get(f.name) == today:
            continue
        result = ops.run(f.name, f.run, group="들여오기")   # 실패하면 다음 바퀴에 다시
        if result is not ops.FAILED:
            done[f.name] = today
            if result:
                changed.add(f.name)
    store.save("_feeds", done)
    return changed

def _dart_rows(ty, days):
    for r in dart.listing(ty, days):
        if dart.has_body(r["report_nm"]):
            yield {"no": r["rcept_no"], "title": re.sub(r"\s+", " ", r["report_nm"]).strip(), "code": r["stock_code"],
                   "name": r["corp_name"], "market": universe.DART_MARKETS.get(r["corp_cls"], "KOSDAQ"), "rcept_dt": r["rcept_dt"]}

def _kind_rows(ty, days, today, fast, changed):
    """KIND 당일공시 목록 — 날짜·시장마다 한 번 받아 저장(지난 날짜는 안 바뀜). 목록이 비정상이면 저장 안 함"""
    before = store.load("kind_feed")
    pages = dict(before)
    d0 = datetime.date.fromisoformat(today)
    for n in range(0 if fast else days, -1, -1):
        d = d0 - datetime.timedelta(days=n)
        key = f"{d.isoformat()}|{ty}"
        if key not in pages or n < KIND_RECHECK_DAYS:
            try:
                ok, rows = kind.rows(d.isoformat(), ty[1])
            except Exception:
                continue
            if ok or not is_business_day(d):
                pages[key] = rows
        for r in pages.get(key, []):
            yield {"no": r["acpt"], "title": r["title"], "code": "", "name": r["name"], "market": universe.KIND_MARKETS[ty],
                   "rcept_dt": ymd(d), "time": r["time"], "submitter": r["submitter"], "indices": r["indices"]}
    oldest = (d0 - datetime.timedelta(days=max(d.days for d in DOCS if ty in d.lists))).isoformat()
    pages = {k: v for k, v in pages.items() if not k.endswith(ty) or k[:10] >= oldest}
    if pages != before:
        store.save("kind_feed", pages)
        changed.add("kind_feed")

def _docs(today, fast):
    failed, changed, now = dict(store.load("_failed")), set(), time.time()
    lists = sorted({ty for d in DOCS for ty in d.lists if not fast or ty.startswith("K")})
    for ty in lists:
        docs = [d for d in DOCS if ty in d.lists]
        days = max(d.days for d in docs)
        listed = os.path.getmtime(dart.LIST_PATH) if os.path.exists(dart.LIST_PATH) else None
        rows = list(_kind_rows(ty, days, today, fast, changed) if ty.startswith("K") else _dart_rows(ty, days))
        if not ty.startswith("K") and os.path.getmtime(dart.LIST_PATH) != listed:
            changed.add("dart_listing")
        since = {d.kind: ymd(days_ago(d.days, today)) for d in docs}
        stored = {d.kind: store.load(d.kind) for d in docs}
        stocks, jobs = {}, []
        for r in rows:
            c = re.sub(r"\s+", "", r["title"])
            d = next((d for d in docs if d.match(c, r)), None)
            if not d or r["rcept_dt"] < since[d.kind]:
                continue
            if d.stocks:
                if d.kind not in stocks:
                    stocks[d.kind] = d.stocks()
                if r["code"] not in stocks[d.kind]:
                    continue
            have = stored[d.kind].get(r["no"])
            if (have and have.get("v", 1) >= d.version) or now - failed.get(r["no"], 0) < RETRY_MIN * 60:
                continue
            jobs.append((d, r))
        if not jobs:
            continue
        print(f"  [들여오기] 목록 {ty} 새 공시 {len(jobs)}건")
        def one(job):
            d, r = job
            if not d.parse:
                return job, {}
            try:
                t = kind.body_text(r["no"]) if ty.startswith("K") else dart.doc_text(r["no"])
                return job, d.parse(t) if t else None
            except Exception:
                return job, None
        new = {}
        with ThreadPoolExecutor(2) as ex:
            for (d, r), v in ex.map(one, jobs):
                if v is None:
                    failed[r["no"]] = now
                    continue
                row = {k: x for k, x in r.items() if k != "no"}
                new.setdefault(d.kind, dict(stored[d.kind]))[r["no"]] = {"kind": d.kind, **row, "v": d.version, **v}
        for k, v in new.items():
            store.save(k, v)
            changed.add(k)
    store.save("_failed", {k: v for k, v in failed.items() if now - v < RETRY_MIN * 60})
    return changed

def run(fast=False):
    today = datetime.date.today().isoformat()
    with _lock:
        changed = (set() if fast else _feeds(today)) | _docs(today, fast)
    if changed or not fast:
        print(f"  [들여오기] 바뀐 종류: {', '.join(sorted(changed)) or '없음'}")
    return changed

if __name__ == "__main__":
    run()
