# -*- coding: utf-8 -*-
"""배당 확인 목록 — 재료표에서 사람(과 하네스 판독자)이 봐야 할 예외 줄만, 돈 영향(지수배당포인트) 순으로 → data/out/div_queue.json

올라오는 줄: 재료 상태가 없음·모름·의심 / 금액에 쓰인 회사 정책 줄이 아직 사람 확인 전(자동·판독자 다름) / 판독자가 빠졌다고 한 정책 줄.
사람이 판정한 줄(div_checked.json 같은 열쇠)은 다시 안 올라온다 — 값·출처가 바뀌면 열쇠가 바뀌어 다시 올라온다.
돈 영향 = 그 배당 건이 지수배당포인트 합계(그 지수·월물)에서 차지하는 %, 지수 중 가장 큰 값 — 지수마다 포인트 크기가 달라 비율로.
금액에 안 쓰인 빈 재료는 영향 0(맨 아래). 같은 회사·재료·값은 한 줄(여러 공시·배당 건이면 sigs·rows 에 모음 — 판정은 sigs 전부에)
요약: 지수·월물마다 합계 중 확인할 재료가 걸린 배당 건의 포인트

  python -m derive.dividend.review
"""
from core.paths import save_json, load_json, stamp, DIV_BOOK_PATH, DIVPOINTS_PATH, DIV_CHECKED_PATH, DIV_QUEUE_PATH

OPEN = ("없음", "모름", "의심", "빠짐")


def build():
    book, dp = load_json(DIV_BOOK_PATH, {}), load_json(DIVPOINTS_PATH, {})
    seen = {(d["code"], d["sig"]) for d in load_json(DIV_CHECKED_PATH, {}).get("decisions", []) if d["by"] == "사람"}
    share, months = {}, {}
    for ix in dp.get("indices", []):
        for m in ix["months"]:
            for d in m["days"]:
                for x in d["items"]:
                    share[x["id"]] = max(share.get(x["id"], 0), x["points"] / m["total"] * 100 if m["total"] else 0)
                    months.setdefault((ix["name"], m["label"]), []).append((x["id"], x["points"]))
    items = {}
    def add(r, item, value, status, check, src, sig, used, accept=None):
        if (r["code"], sig) in seen:
            return
        key = (r["code"], item, value)
        impact = share.get(r["id"], 0) if used or status in ("의심", "빠짐") else 0
        old = items.get(key)
        if old and old["impact"] >= impact:
            old["rows"] += [r["id"]] if r["id"] not in old["rows"] else []
            old["sigs"] += [sig] if sig not in old["sigs"] else []
            return
        items[key] = {"code": r["code"], "name": r["name"], "fy": int(r["quarter"][:4]), "row": r["id"], "rows": [r["id"]] + (old["rows"] if old else []),
                      "div_type": r["div_type"], "per_share": r.get("per_share", ""), "item": item, "value": value, "status": status, "check": check,
                      "src": src, "sigs": [sig] + [x for x in (old["sigs"] if old else []) if x != sig], "used": used, "impact": round(impact, 2),
                      "basis": r.get("amount_basis", ""), **({"accept": accept} if accept else {})}   # accept: 판독자 값 받기 단추가 그대로 보낼 판정·정책 줄
    for r in book.get("rows", []):
        if r["kind"] != "est" or not r.get("amount_inputs"):
            continue
        uses = r["amount_uses"]
        for e in r["amount_inputs"]:
            policy = e["item"].startswith("회사 정책")
            used = any(e["item"].startswith(u) for u in uses)
            if e["status"] in OPEN or (policy and used and e["check"] in ("자동", "판독자 다름")):
                add(r, e["item"], e["value"] + (f" → 판독자: {e['fix_text']}" if "fix" in e else ""), e["status"], e["check"], e["src"], e["sig"], used,
                    {"label": "판독자 값으로", "verdict": "틀림", "row": e["fix"]} if "fix" in e else None)
        for s in book["companies"].get(r["code"], {}).get("suggested", []):
            if int(r["quarter"][:4]) in range(s["value"]["fy"][0] or 0, (s["value"]["fy"][1] or 9999) + 1) and r["div_type"] == "결산배당":
                v = s["value"]
                add(r, "회사 정책", s["text"], "빠짐", "판독자 제안", s.get("note", ""), s["sig"], True,
                    {"label": "빠짐 인정(정책 넣기)", "verdict": "빠짐 인정", "row": {k: v.get(k) for k in ("kind", "slot", "lo", "hi", "fy")}})
    open_rows = {i for x in items.values() if x["impact"] for i in x["rows"]}
    summary = [{"index": ix, "month": mo, "total": round(sum(p for _, p in xs), 4), "open": round(sum(p for i, p in xs if i in open_rows), 4)}
               for (ix, mo), xs in months.items()]
    queue = sorted(items.values(), key=lambda x: (-x["impact"], OPEN.index(x["status"]) if x["status"] in OPEN else 9, x["name"]))
    save_json(DIV_QUEUE_PATH, {"generated_at": stamp(), "summary": summary, "counts": {"money": sum(1 for x in queue if x["impact"]), "all": len(queue)}, "items": queue})
    return queue


if __name__ == "__main__":
    q = build()
    print(f"확인 목록 {len(q)}줄 — 돈 영향 있는 줄 {sum(1 for x in q if x['impact'])}")
