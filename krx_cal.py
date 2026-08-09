# -*- coding: utf-8 -*-
"""한국 증시 휴장일 달력 — 여러 모듈(dividends·expiries)이 공유해 중복 제거.
fetch(=API 키) 의존이 없어 단독으로도 import 가능."""
import datetime
import holidays as _hol

def holiday_dates(years):
    """주어진 연도들의 증시 휴장일 집합 — 공휴일 + 근로자의날(5/1) + 연말폐장(12/31).
    (주말은 포함하지 않음 — 호출부에서 weekday로 따로 처리)"""
    years = list(years)
    # 제헌절(7/17)은 2008년부터 공휴일이 아니라 KRX 정상 개장 → holidays 라이브러리가
    # 국경일로 포함시키는 것을 제외한다.
    days = {d for d, name in _hol.SouthKorea(years=years).items() if "제헌절" not in name}
    for y in years:
        days.add(datetime.date(y, 5, 1))     # 근로자의날 (증시 휴장)
        days.add(datetime.date(y, 12, 31))    # 연말 폐장일
    return days

def default_years():
    """오늘 기준 전년~내년 — 배당락일/만기일 계산에 충분한 범위."""
    y = datetime.date.today().year
    return [y - 1, y, y + 1]
