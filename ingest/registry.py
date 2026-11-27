# -*- coding: utf-8 -*-
"""들여오기 등록표 — 공시 종류(DOCS)와 주기 원천(FEEDS). 새 공시·새 원천은 여기 한 줄 + 파서·원천 파일 하나"""
import os
from dataclasses import dataclass
from typing import Callable, Optional
from core import universe
from ingest.parsers import dividend as dv, earn_sched as es, capital as cap, corporate as co, kind as kd
from ingest.feeds import acc_mt, naver_cons, dart_alot, dart_shares, dart_xbrl, index_pdf
from ingest import coverage


@dataclass(frozen=True)
class Doc:
    kind: str                       # 저장소 이름
    lists: tuple                    # DART 목록 I 거래소공시 · A 정기공시 · B 주요사항 / KIND 당일공시 K1 유가 · K2 코스닥 · K3 파생
    days: int                       # 목록을 며칠치 보나 (0 = 오늘만)
    match: Callable[[str, dict], bool]   # (공백 뺀 제목, 목록 행) → 이 종류인가 (같은 목록 안에서는 위에 있는 종류가 먼저)
    parse: Optional[Callable]       # 원문 → 값. None 이면 원문을 안 받고 목록 정보만
    version: int = 1                # 올리면 저장된 공시를 다시 읽음
    stocks: Optional[Callable[[], set]] = universe.div   # DART 종목 거르기. KIND 는 회사명뿐이라 None(계산에서 거름)


@dataclass(frozen=True)
class Feed:
    name: str
    every: str                      # "pass" 매 바퀴(할 일 없으면 바로 끝남) · "day" 하루 한 번
    run: Callable[[], bool]         # 받아서 저장소에 씀 → 바뀌었나
    after_docs: bool = False        # 공시를 다 받은 뒤에 (들여오기 점검)


def _own(*words):
    return lambda c, r=None: "자회사" not in c and any(w in c for w in words)

def _has(*words):
    return lambda c, r=None: any(w in c for w in words)

DOCS = [
    Doc("decision", ("I",), 800, _own("현금ㆍ현물배당결정"), dv.parse_decision, 3),
    Doc("record", ("I",), 800, lambda c, r: _own("주주명부폐쇄")(c) and "배당" in c, dv.parse_record),
    Doc("agm_record", ("I",), 800, _own("주주명부폐쇄"), None),
    Doc("notice", ("I",), 800, lambda c, r: _own("기타경영사항")(c) and ("배당" in c or "기준일" in c or c.endswith("기타경영사항(자율공시)"))
        or _own("기타안내사항")(c) and "배당" in c,
        dv.parse_notice, 5),
    Doc("split", ("I",), 800, _own("주식분할결정", "주식병합결정"), lambda t: {**dv.parse_split(t), **co.parse_relist(t)}, 2),
    Doc("agm_call", ("I",), 800, _own("주주총회소집결의"), dv.parse_agm, 2),
    Doc("agm_result", ("I",), 800, _own("주주총회결과"), dv.parse_agm, 2),
    Doc("plan", ("I",), 800, _own("기업가치제고계획", "지배구조보고서"), dv.parse_plan, 13),
    Doc("return_plan", ("I",), 800, _own("수시공시의무관련사항"), dv.parse_return, 8),
    Doc("stock_div", ("I",), 800, _own("주식배당결정"), dv.parse_stock_div),
    Doc("exdiv", ("I",), 800, lambda c, r: _own("배당락", "권배락")(c), dv.parse_exdiv),
    Doc("treasury", ("B",), 365, lambda c, r: _own("자기주식취득결정", "자기주식취득신탁계약체결결정", "자기주식처분결정")(c), dv.parse_treasury),
    Doc("earn_notice", ("I",), 45, _has("결산실적공시예고"), es.parse_sched),
    Doc("ir", ("I",), 14, _has("기업설명회"), es.parse_ir),
    Doc("cap_mixed", ("B",), 365, _has("유무상증자결정"), lambda t: cap.parse_cap(t, "유무상증자결정"), stocks=universe.watch),
    Doc("cap_rights", ("B",), 365, _has("유상증자결정"), lambda t: cap.parse_cap(t, "유상증자결정"), stocks=universe.watch),
    Doc("cap_bonus", ("B",), 365, _has("무상증자결정"), lambda t: cap.parse_cap(t, "무상증자결정"), stocks=universe.watch),
    Doc("reduction", ("B",), 150, _has("감자결정", "회사분할결정"), co.parse_relist, stocks=universe.watch),
    Doc("charter_table", ("A",), 400, _has("사업보고서", "반기보고서", "분기보고서"), dv.parse_policy),
    Doc("kind_halt", ("K1", "K2"), 50, _has("매매거래정지"), kd.parse_halt, stocks=None),
    Doc("kind_action", ("K1", "K2"), 0, lambda c, r: "시장본부" in r["submitter"] or "거래소" in r["submitter"], None, stocks=None),
    Doc("kind_limit", ("K3",), 0, _has("가격제한폭확대요건도달"), kd.parse_limit, stocks=None),
]

def _index_pdf():
    before = os.path.getmtime(universe.PDF_PATH) if os.path.exists(universe.PDF_PATH) else None
    index_pdf.ensure()
    return os.path.exists(universe.PDF_PATH) and os.path.getmtime(universe.PDF_PATH) != before

FEEDS = [
    Feed("index_pdf", "pass", _index_pdf),       # 배당 대상 종목을 정하므로 맨 앞
    Feed("acc_mt", "pass", acc_mt.run),
    Feed("consensus", "day", naver_cons.run),
    Feed("alot", "day", dart_alot.run),
    Feed("shares", "day", dart_shares.run),
    Feed("xbrl", "day", dart_xbrl.run),
    Feed("doc_coverage", "pass", lambda: coverage.run(DOCS), after_docs=True),   # 빠뜨린 공시·못 읽은 숫자·정답 묶음 — 공시를 다 받은 뒤
]

KINDS = [d.kind for d in DOCS] + [f.name for f in FEEDS]
assert len(KINDS) == len(set(KINDS)), "등록표에 같은 종류가 두 번"
