# -*- coding: utf-8 -*-
"""구조 검사 — 층: 계산(derive)은 들여오기(ingest)를 import 하지 않고, 파서는 접속하지 않는다.
등록표: 계산이 읽는 종류는 누군가 채우고, 들여오는 종류는 누군가 읽는다.

  python -m app.check_layers
"""
import os, re, sys
from core.paths import BASE

SIGNALS = {"day", "live", "quotes", "dart_listing", "kind_feed", "div_human"}   # 들여오기·monitor·서버(사람 판정 저장) 가 내는 변화 신호
RULES = [("derive/", r"^\s*(from|import) ingest\b", "계산 층이 들여오기를 import"),
         ("ingest/parsers/", r"^\s*(from|import) (ingest\.sources|urllib|requests)\b", "파서가 접속")]

def main():
    bad = []
    for root, _, fs in os.walk(BASE):
        for f in fs:
            rel = os.path.relpath(os.path.join(root, f), BASE).replace(os.sep, "/")
            if not f.endswith(".py"):
                continue
            src = open(os.path.join(root, f), encoding="utf-8").read()
            bad += [f"{rel}: {why}" for pre, pat, why in RULES if rel.startswith(pre) and re.search(pat, src, re.M)]
    from ingest.registry import DOCS, FEEDS
    from derive.registry import CALCS
    made = {d.kind for d in DOCS} | {f.name for f in FEEDS} | SIGNALS
    read = set().union(*(c.reads for c in CALCS))
    bad += [f"등록표: 계산이 읽는데 채우는 곳 없음 — {k}" for k in sorted(read - made)]
    bad += [f"등록표: 들여오는데 읽는 계산 없음 — {k}" for k in sorted(made - read)]
    for x in bad:
        print("  ✖", x)
    print(f"구조 검사: 위반 {len(bad)}")
    return bad

if __name__ == "__main__":
    sys.exit(1 if main() else 0)
