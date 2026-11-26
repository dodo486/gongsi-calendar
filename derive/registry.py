# -*- coding: utf-8 -*-
"""계산 등록표 — 계산마다 읽는 저장소 종류와 앞 계산. 새 계산은 여기 한 줄 + 계산 파일 하나.
"day" = 날이 바뀜(오늘 기준으로 달라지는 계산). 순서는 앞 계산이 먼저 오게 적는다."""
from dataclasses import dataclass
from typing import Callable
from core import expiries
from derive import earn_sched, capital, actions, limits, halts, disclosures, earnings
from derive.dividend import facts, verify, book, points, review, check


@dataclass(frozen=True)
class Calc:
    name: str
    reads: frozenset            # 이 종류가 바뀌면 다시 계산
    after: tuple                # 이 계산들이 다시 돌면 따라서 다시 계산
    run: Callable


CALCS = [
    Calc("expiries", frozenset({"day"}), (), expiries.build),
    Calc("capital", frozenset({*capital.CAP_KINDS, "quotes", "day"}), (), capital.main),   # quotes = 시세 갱신 주기
    Calc("actions", frozenset({"kind_action", "kind_halt", "day"}), (), actions.build),
    Calc("limits", frozenset({"kind_limit", "live", "day"}), (), limits.build),          # live = 장중 고속 바퀴(등락률)
    Calc("halts", frozenset({"kind_halt", "split", "reduction", "day"}), (), halts.main),
    Calc("disclosures", frozenset({"dart_listing", "day"}), (), lambda: disclosures.main(90)),
    Calc("earnings", frozenset({"dart_listing", "kind_feed", "day"}), (), earnings.main),
    Calc("earn_sched", frozenset({"earn_notice", "ir", "day"}), (), earn_sched.main),
    Calc("div_verify", frozenset({"alot", "fin", "day"}), (), verify.verify),    # 백테스트 — 하루 한 번
    Calc("div_book", frozenset({*facts.DOC_KINDS, "agm_record", "charter_table", "acc_mt", "consensus", "alot", "fin", "shares", "div_human", "index_pdf", "earn_notice", "ir", "day"}),
         ("expiries",), book.build),
    Calc("divpoints", frozenset({"index_pdf", "day"}), ("div_book",), points.refresh),
    Calc("div_queue", frozenset({"div_human"}), ("div_book", "divpoints"), review.build),   # 확인 목록 — 사람 판정(div_human)이 바뀌어도
    Calc("dividend_check", frozenset({"doc_coverage"}), ("div_book", "div_verify", "divpoints", "div_queue"), check.main),
]

_names = [c.name for c in CALCS]
assert len(_names) == len(set(_names)), "등록표에 같은 계산이 두 번"
assert all(a in _names[:i] for i, c in enumerate(CALCS) for a in c.after), "앞 계산은 위에 적어야 함"
