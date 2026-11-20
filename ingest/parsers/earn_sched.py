# -*- coding: utf-8 -*-
"""실적발표 예고 공시 파서 — 결산실적공시예고·기업설명회(IR)개최 안내공시 원문 → 값 (접속 없음)"""
import re

def parse_sched(t):
    """예고 본문 → {expected_date(공시예정일), period_end(결산기간 종료일)}"""
    exp = re.search(r"공시예정일\s*(\d{4}-\d{2}-\d{2})", t)
    end = re.search(r"종료일\s*(\d{4}-\d{2}-\d{2})", t)
    return {"expected_date": exp.group(1) if exp else "",
            "period_end": end.group(1) if end else ""}

def parse_ir(t):
    """IR개최 본문 → {expected_date(개최일), time, purpose(개최목적), sponsor(후원 증권사)}"""
    dt = re.search(r"일시\s*(\d{4}-\d{1,2}-\d{1,2})\s*(\d{1,2}:\d{2})?", t)
    pur = re.search(r"개최목적\s*(.+?)\s*\d\s*\.\s*개최방법", t)
    spo = re.search(r"후원기관\s*([^\s<]+)", t)
    d = dt.group(1) if dt else ""
    if d:   # 2026-8-3 → 2026-08-03 정규화
        y, m, dd = d.split("-")
        d = f"{y}-{int(m):02d}-{int(dd):02d}"
    return {"kind": "ir", "expected_date": d,
            "time": (dt.group(2) or "") if dt else "",
            "purpose": (pur.group(1).strip()[:60] if pur else ""),
            "sponsor": spo.group(1) if spo else ""}
