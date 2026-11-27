# -*- coding: utf-8 -*-
"""고배당기업 — 조세특례제한법 §104의27 · 시행령 §104의24 (2026-02-27 신설, 2028 사업연도까지 받는 배당). 법 숫자는 이 파일에만.
고배당기업인지는 계산하지 않고 수집(KIND 고배당기업 현황 = 회사 밸류업 공시) — 여기는 '유지하려면 올해 얼마 줘야 하나'만.
요건(배당을 받는 해의 직전 사업연도 기준): 이익배당금액이 2024 사업연도분보다 안 줄어듦, 그리고 배당성향 40% 이상 또는 25% 이상 + 이익배당금액이 그 전 해보다 10% 이상 증가.
이익배당금액은 회사·거래소가 사업연도 귀속으로 셈(KIND 증가율 — LG화학 2025 99.5% = 2024분 → 2025분). 배당성향 = 이익배당금액 ÷ 연결 지배순이익(연결 없으면 별도).
순이익 0 이하면 배당성향을 25%로 보고, 부채가 자본의 2배를 넘으면 0%로 봄"""
import datetime

ENACTED = datetime.date(2025, 12, 23)   # 법 신설 — 그 전 시점의 예측에는 안 씀
BASE_YEAR, LAST_YEAR = 2024, 2028
HIGH, LOW, GROWTH, DEBT_X = 0.40, 0.25, 0.10, 2


def need(before, base, ni, debt):
    """요건을 채우는 최소 이익배당금액 — before 작년분, base 2024 사업연도분, ni 올해 순이익, debt 부채 ÷ 자본. 못 채우면(부채가 자본 2배 넘는 적자) None"""
    if ni <= 0:
        return None if debt is not None and debt > DEBT_X else max(base, (1 + GROWTH) * before)
    return max(base, min(HIGH * ni, max(LOW * ni, (1 + GROWTH) * before)))
