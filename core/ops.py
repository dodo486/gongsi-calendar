# -*- coding: utf-8 -*-
"""관제탑 — 서버에서 도는 일이 제때 도는지 한곳에서 판정한다. 화면(web/ops.html)은 /ops 로 읽기만 한다.

  every(이름, 초, 할 일)        상주 루프 — 주기마다 run
  run(이름, 할 일, every, group) 한 번 돌리고 성공·실패·걸린 시간을 남긴다. 실패는 출력하고 삼킨다
  source(함수)                  다른 모듈이 자기 항목을 낸다 (시세 허브: core.market.stats)
  snapshot()                    항목 전부 + 판정(정상·지연·오류·쉬는 중·대기)

같은 이름의 항목은 하나로 합친다 — 구독자의 실행 기록(run)과 시세 신선도(source)가 한 줄에 보인다.
"""
import time, datetime, threading

LATE_TIMES, LATE_GRACE = 3, 10   # 주기의 3배 + 10초 넘게 소식이 없으면 지연
GROUPS = {   # 화면에 보이는 순서 = 시세가 흐르는 순서
    "시세 허브": "한투(KIS)에서 시세를 받아 최신값을 들고 있는 곳",
    "구독자": "허브 시세를 받아 쓰는 기능 — 언제 마지막으로 받았고 가진 값이 얼마나 오래됐나",
    "작업": "주기마다 도는 서버 일 — 공시 확인·상하한가·거래정지·인더스트리",
    "들여오기": "원천(DART·KIND·증권사 등)에서 받아 저장소에 넣는 일",
    "계산": "저장소를 읽어 화면 파일(data/out)을 만드는 일",
}
STATES = {"error": "오류", "late": "지연", "wait": "대기", "rest": "쉬는 중", "ok": "정상"}   # 나쁜 것부터

_lock = threading.Lock()
_jobs = {}      # 이름: {group, every, at, secs, runs, error, error_at}
_sources = []


FAILED = object()   # run 이 실패했을 때 돌려주는 값


def run(name, work, every=None, group="작업"):
    """work() 의 결과 / 실패하면 FAILED"""
    t0 = time.time()
    try:
        result = work()
        rec = {"at": time.time(), "secs": time.time() - t0}
    except Exception as ex:
        print(f"  [{name}] 실패: {type(ex).__name__} {ex}")
        result, rec = FAILED, {"error": f"{type(ex).__name__} {ex}", "error_at": time.time()}
    with _lock:
        job = _jobs.setdefault(name, {"runs": 0})
        job.update(rec, group=group, every=every, runs=job["runs"] + ("at" in rec))
    return result


def every(name, seconds, work):
    while True:
        run(name, work, seconds)
        time.sleep(seconds)


def source(items):
    _sources.append(items)


def _judge(item, now):
    if item.get("error_at") and item["error_at"] >= (item.get("at") or 0):
        return "error"
    if item.get("rest"):
        return "rest"
    if not item.get("at"):
        return "wait"
    if item.get("every") and now - item["at"] > item["every"] * LATE_TIMES + LATE_GRACE:
        return "late"
    return "ok"


def snapshot():
    now = time.time()
    with _lock:
        items = {name: dict(job, name=name, detail=f"{job['runs']}번 성공 · 마지막 {job.get('secs', 0):.1f}초 걸림")
                 for name, job in _jobs.items()}
    for items_of in _sources:
        for item in items_of():
            items.setdefault(item["name"], {}).update(item)
    order = list(GROUPS)
    rows = sorted(items.values(), key=lambda i: order.index(i["group"]) if i.get("group") in order else len(order))
    for i in rows:
        i["state"] = _judge(i, now)
        i["label"] = STATES[i["state"]]
        i["ago"] = now - i["at"] if i.get("at") else None
    worst = next((s for s in ("error", "late") if any(i["state"] == s for i in rows)), "ok")
    return {"generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "state": worst, "label": STATES[worst],
            "counts": {STATES[s]: sum(i["state"] == s for i in rows) for s in STATES},
            "groups": GROUPS, "items": rows}
