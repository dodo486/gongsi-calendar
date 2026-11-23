# -*- coding: utf-8 -*-
"""잠정실적(공정공시) 원문 파서 — 당기 실적(억원)·증감률·투자판단 중요사항 (접속 없음)"""
import re
from core.fmt import num

_UNIT = {"백만원": 0.01, "억원": 1.0, "천원": 1e-5, "원": 1e-8}

def parse_actual(t):
    """잠정실적(공정공시) 원문 → 당기 실적(억원) + QoQ/YoY 증감율 + 투자판단 중요사항
    원문 행 구조: <항목> 당해실적 당기 전기 전기대비% 흑전여부 전년동기 전년동기대비% 흑전여부
    """
    um = re.search(r"단위\s*:\s*(백만원|억원|천원|원)", t)
    mul = _UNIT.get(um.group(1) if um else "백만원", 0.01)
    # 분기 라벨 — 표기 변형 대응: (2026.2Q) / 2026년 2분기 / 실패 시 당기 누적기간 종료월로 추론
    label = ""
    m = re.search(r"(\d{4})\s*[.년]\s*([1-4])\s*(?:Q|분기)", t)
    if m:
        label = f"{m.group(1)}.{m.group(2)}Q"
    else:
        pe = re.search(r"~\s*(\d{4})-(\d{2})-\d{2}", t)   # 당기 누적기간 종료월 (03/06/09/12)
        if pe:
            q = {"03": "1", "06": "2", "09": "3", "12": "4"}.get(pe.group(2))
            if q:
                label = f"{pe.group(1)}.{q}Q"


    def row(name):
        m = re.search(name + r"\s*당해실적((?:\s+\S+){7})", t)
        if not m:
            return None
        tok = m.group(1).split()
        amt = lambda v: (None if num(v) is None else round(num(v) * mul))
        flag = lambda v: ("" if v in ("-", "") else v)
        return {"cur": amt(tok[0]), "prev": amt(tok[1]), "prev_yr": amt(tok[4]),
                "qoq": num(tok[2]), "qoq_flag": flag(tok[3]),
                "yoy": num(tok[5]), "yoy_flag": flag(tok[6])}

    rs, ro, rn = row("매출액"), row("영업이익"), row("당기순이익")
    if rs is None and ro is None:
        return {}
    fmt = lambda v: "-" if v is None else f"{v:,}"
    nm = re.search(r"투자판단과\s*관련한\s*중요사항\s*[-:.\s]*(.+)$", t)
    notes = (nm.group(1).strip()[:400] if nm else "")
    out = {"label": label,
           "sales": fmt(rs["cur"] if rs else None),
           "op": fmt(ro["cur"] if ro else None),
           "np": fmt(rn["cur"] if rn else None),
           "growth": {"sales": rs, "op": ro, "np": rn},
           "notes": notes}
    return out
