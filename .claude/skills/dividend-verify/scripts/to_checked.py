# -*- coding: utf-8 -*-
"""두 번째 판독자 판정(_workspace/expected_rows.json: {접수번호: {batch, rows}}) → div_checked.json 판독자 판정, 공시마다.
파서 정책 줄마다 맞음 또는 틀림 — 같은 공시에서 판독자가 같은 종류·칸을 다르게 읽었으면 틀림 하나에 고친 값(value)을 붙임(틀림+빠짐 두 줄로 쪼개지 않음).
판독자만 읽은 줄은 빠짐(value = 있어야 할 줄). 같은 공시의 옛 판독자 판정은 바꿈, 사람 판정은 그대로.
열쇠 sig 는 정책 사실(facts.policy_sig, 출처 공시 빼고), 공시는 src 칸. 판독자가 연결·별도를 안 적은 줄은 같은 내용 파서 줄의 기준으로 맞춤.

  PYTHONPATH=. PYTHONIOENCODING=utf-8 python .claude/skills/dividend-verify/scripts/to_checked.py
"""
import datetime, json
from core import store
from core.krx_cal import to_date
from core.paths import DIV_CHECKED_PATH, load_json, save_json
from derive.dividend.facts import policy_sig, said_bases, _base, _fys

KEYS = ("kind", "slot", "lo", "hi", "base")

docs = {**store.load("plan"), **store.load("return_plan")}
judged = json.load(open("_workspace/expected_rows.json", encoding="utf-8"))
said = {}   # 회사마다 비율 기준 — facts 와 같은 규칙(said_bases)
for e in sorted(docs.values(), key=lambda e: (e["rcept_dt"], e.get("rcept_no", ""))):
    said.setdefault(e["code"], []).extend(e.get("rows", []))
said = {code: said_bases(rows) for code, rows in said.items()}
today = datetime.date.today().isoformat()
doc = load_json(DIV_CHECKED_PATH, {"decisions": []})
doc["decisions"] = [d for d in doc["decisions"] if d["by"] == "사람" or d.get("src") not in judged]
count = {"맞음": 0, "틀림": 0, "틀림+고친 값": 0, "빠짐": 0}
for no, j in judged.items():
    e = docs.get(no)
    if not e:
        continue
    d = to_date(e["rcept_dt"])
    fact = lambda r: {**{k: r.get(k) for k in KEYS}, "fy": _fys(r.get("fy") or [None, None], d), "base": _base(r, said.get(e["code"], {}))}
    got = [fact(r) for r in e.get("rows", []) if r["kind"] != "high_div"]
    same = lambda a, b: all(a[k] == b[k] for k in ("kind", "slot", "lo", "hi", "fy"))
    want = [fact(r) for r in j["rows"] if r.get("kind") != "high_div"]
    want = [{**w, "base": next((g["base"] for g in got if same(g, w)), w["base"])} if not r_base else w
            for w, r_base in zip(want, [r.get("base") for r in j["rows"] if r.get("kind") != "high_div"])]   # 판독자가 기준을 안 적었으면 같은 줄의 파서 기준
    W, G = {policy_sig(r) for r in want}, {policy_sig(r) for r in got}
    left = [r for r in want if policy_sig(r) not in G]   # 판독자만 읽은 줄 — 같은 종류·칸의 틀린 파서 줄이 있으면 그 줄의 고친 값
    out = []
    for r in got:
        if policy_sig(r) in W:
            out.append((r, "맞음", None))
            continue
        fix = next((w for w in left if (w["kind"], w["slot"]) == (r["kind"], r["slot"])), None)
        if fix:
            left.remove(fix)
        out.append((r, "틀림", fix))
    out += [(r, "빠짐", r) for r in left]
    for r, verdict, value in out:
        count["틀림+고친 값" if verdict == "틀림" and value else verdict] += 1
        doc["decisions"].append({"code": e["code"], "fy": r["fy"][0], "item": "회사 정책", "sig": policy_sig(r), "src": no, "verdict": verdict, "by": "판독자",
                                 "date": today, "note": f"두 번째 판독자 {j['batch']} — {e.get('name', '')} {no}", **({"value": value} if value else {})})
save_json(DIV_CHECKED_PATH, doc)
print(count)
