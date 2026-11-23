# -*- coding: utf-8 -*-
"""계산 한 바퀴 — 바뀐 종류를 읽는 계산과 그 뒤 계산만 등록표 순서대로 한 번씩. 저장소만 읽고 네트워크는 안 씀.

  python -m derive.run        # 전부
"""
import sys
from core import ops
from derive.registry import CALCS

def run(changed=None, log=True):
    """changed: 바뀐 종류 이름들(None 이면 전부). 반환 {계산 이름: 결과}"""
    done, failed = {}, set()
    for c in CALCS:
        if changed is not None and not (c.reads & set(changed)) and not any(a in done for a in c.after):
            continue
        if any(a in failed for a in c.after):
            failed.add(c.name)
            continue
        result = ops.run(c.name, c.run, group="계산")
        if result is ops.FAILED:
            failed.add(c.name)
        else:
            done[c.name] = result
    if log or failed:
        print(f"  [계산] {', '.join(done) or '없음'}" + (f" · 실패 {', '.join(sorted(failed))}" if failed else ""))
    return done

if __name__ == "__main__":
    run(None if len(sys.argv) < 2 else sys.argv[1:])
