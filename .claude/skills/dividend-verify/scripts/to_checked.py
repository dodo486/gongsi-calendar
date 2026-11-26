# -*- coding: utf-8 -*-
"""두 번째 판독자 판정(_workspace/expected_rows.json: {접수번호: {batch, rows}}) → div_checked.json 판독자 판정.
파서 정책 줄마다 맞음·틀림, 판독자만 읽은 줄은 빠짐(value = 있어야 할 줄). 같은 공시의 옛 판독자 판정은 바꿈, 사람 판정은 그대로.
확인 목록(data/out/div_queue.json)에서 사람이 볼 때 판독자 다름·판독자 제안으로 보인다.

  PYTHONPATH=. PYTHONIOENCODING=utf-8 python .claude/skills/dividend-verify/scripts/to_checked.py
"""
import datetime, json
from core import store
from core.krx_cal import to_date
from core.paths import DIV_CHECKED_PATH, load_json, save_json
from derive.dividend.facts import policy_sig, _fys

KEYS = ("kind", "slot", "lo", "hi")
key = lambda r: (tuple(r.get(k) for k in KEYS), tuple(r.get("fy") or [None, None]))

docs = {**store.load("plan"), **store.load("return_plan")}
judged = json.load(open("_workspace/expected_rows.json", encoding="utf-8"))
today = datetime.date.today().isoformat()
doc = load_json(DIV_CHECKED_PATH, {"decisions": []})
doc["decisions"] = [d for d in doc["decisions"] if d["by"] == "사람" or d["sig"].split("|")[0] not in judged]
count = {"맞음": 0, "틀림": 0, "빠짐": 0}
for no, j in judged.items():
    e = docs.get(no)
    if not e:
        continue
    d = to_date(e["rcept_dt"])
    got = [r for r in e.get("rows", []) if r["kind"] != "high_div"]
    want = [r for r in j["rows"] if r.get("kind") != "high_div"]
    W, G = {key(r) for r in want}, {key(r) for r in got}
    for r, verdict in [(r, "맞음" if key(r) in W else "틀림") for r in got] + [(r, "빠짐") for r in want if key(r) not in G]:
        row = {**{k: r.get(k) for k in KEYS}, "fy": _fys(r.get("fy") or [None, None], d), "src": no}
        count[verdict] += 1
        doc["decisions"].append({"code": e["code"], "fy": row["fy"][0], "item": "policy", "sig": policy_sig(row), "verdict": verdict, "by": "판독자",
                                 "date": today, "note": f"두 번째 판독자 {j['batch']}" + (f" — {e.get('name', '')} {no}" if verdict == "빠짐" else ""),
                                 **({"value": row} if verdict == "빠짐" else {})})
save_json(DIV_CHECKED_PATH, doc)
print(count)
