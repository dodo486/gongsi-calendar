# -*- coding: utf-8 -*-
"""층 검사 — 계산(derive)은 들여오기(ingest)를 import 하지 않는다(저장소만 읽음), 파서는 접속하지 않는다. 어기면 실패(CI).
MIGRATING = 아직 들여오기·계산으로 나누는 중인 모듈 — 다 나누면 비운다.

  python -m app.check_layers
"""
import os, re, sys
from core.paths import BASE

MIGRATING = {"derive/capital.py", "derive/earnings.py", "derive/earn_detail.py", "derive/halts.py", "derive/limits.py",
             "derive/actions.py", "derive/disclosures.py", "derive/closearb.py", "derive/industry.py"}
RULES = [("derive/", r"^\s*(from|import) ingest\b", "계산 층이 들여오기를 import"),
         ("ingest/parsers/", r"^\s*(from|import) (ingest\.sources|urllib|requests)\b", "파서가 접속")]

def main():
    bad = []
    for root, _, fs in os.walk(BASE):
        for f in fs:
            rel = os.path.relpath(os.path.join(root, f), BASE).replace(os.sep, "/")
            if not f.endswith(".py") or rel in MIGRATING:
                continue
            src = open(os.path.join(root, f), encoding="utf-8").read()
            bad += [f"{rel}: {why}" for pre, pat, why in RULES if rel.startswith(pre) and re.search(pat, src, re.M)]
    for x in bad:
        print("  ✖", x)
    print(f"층 검사: 위반 {len(bad)} · 나누는 중 {len(MIGRATING)}")
    return bad

if __name__ == "__main__":
    sys.exit(1 if main() else 0)
