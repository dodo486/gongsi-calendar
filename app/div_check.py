# -*- coding: utf-8 -*-
"""배당 확인 목록 판정 저장 — 화면(/api/div_check)이 보낸 사람 판정을 div_checked.json 에 남기고,
정책 줄을 고치거나(틀림 + 고친 값) 판독자가 빠졌다고 한 줄을 인정하면(빠짐 인정) div_policy.json 사람 줄로 넣은 뒤 배당 계산을 다시 돌린다.
판정 한 건 = 확인 목록 한 줄의 열쇠(sigs) 전부에 같은 판정"""
import datetime, re, threading
from core.paths import DIV_CHECKED_PATH, DIV_POLICY_PATH, load_json, save_json
import derive.run

VERDICTS = ("맞음", "틀림", "모름", "빠짐 인정")
POLICY_KEYS = ("kind", "slot", "lo", "hi", "fy")


def record(body):
    """body = {code, name, fy, item, sigs, verdict, note, value?, row?} — value: 고친 값(정책 아닌 재료, 숫자), row: 고친·인정한 정책 줄 {kind, slot, lo, hi, fy}"""
    code, verdict, sigs = str(body.get("code", "")), body.get("verdict"), body.get("sigs") or []
    if not re.fullmatch(r"\d{6}|[0-9A-Z]{6}", code) or verdict not in VERDICTS or not sigs:
        raise ValueError("판정 형식 오류")
    today = datetime.date.today().isoformat()
    doc = load_json(DIV_CHECKED_PATH, {"decisions": []})
    doc["decisions"] = [d for d in doc["decisions"] if not (d["by"] == "사람" and d["code"] == code and d["sig"] in sigs)]
    for sig in sigs:
        doc["decisions"].append({"code": code, "fy": body.get("fy"), "item": body.get("item", ""), "sig": sig, "verdict": verdict, "by": "사람",
                                 "date": today, "note": body.get("note", ""), **({"value": body["value"]} if body.get("value") not in (None, "") else {})})
    save_json(DIV_CHECKED_PATH, doc)
    row = body.get("row")
    if row and verdict in ("틀림", "빠짐 인정"):
        pol = load_json(DIV_POLICY_PATH, {"rows": []})
        pol["rows"].append({"code": code, "name": body.get("name", ""), **{k: row.get(k) for k in POLICY_KEYS},
                            "date": today, "src": "확인 목록", "title": "사람 확인", "by": "사람(확인 목록)", "text": body.get("note", ""), "updated": today})
        save_json(DIV_POLICY_PATH, pol)
    threading.Thread(target=derive.run.run, args=({"div_human"},), daemon=True, name="div_check").start()
    return {"ok": True, "saved": len(sigs)}
