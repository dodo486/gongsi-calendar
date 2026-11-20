# -*- coding: utf-8 -*-
"""한국 증시 휴장일 달력·영업일 계산 — 배당·증자·만기 등 여러 모듈이 공유해 중복 제거.
fetch(=API 키) 의존이 없어 단독으로도 import 가능."""
import datetime, re
import numpy as np
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
        last = datetime.date(y, 12, 31)       # 연말 휴장 = 그해 마지막 평일 (12/31 이 주말이면 그 전 금요일)
        while last.weekday() >= 5 or last in days:
            last -= datetime.timedelta(days=1)
        days.add(last)
    return days

def default_years():
    """오늘 기준 3년 전~2년 뒤 — 과거 회차 검증(2년+)·내후년 기준일·만기일까지 휴장일이 빠지지 않게."""
    y = datetime.date.today().year
    return list(range(y - 3, y + 3))

_HOL_ARR = np.array(sorted(str(d) for d in holiday_dates(default_years())), dtype="datetime64[D]")

def t_minus(record_iso, n):
    """기준일(배당기준일·신주배정기준일) 기준 n번째 직전 영업일. n=1 → 락일, n=2 → 권리부 마지막 매수일.
    T+2 결제라 '기준일까지 결제되는 마지막 영업일(L)'이 기준 — 기준일이 영업일이면 L=기준일,
    휴장일(주말·공휴일·12/31 폐장)이면 L=그 직전 영업일. 락일 = L-1, 매수일 = L-2 영업일.
    예) 2024 기준일 12/31(휴장) → L=12/30, 배당락 12/27, 매수일 12/26 (실제 KRX 일정과 일치)."""
    if not record_iso:
        return ""
    try:
        last = np.busday_offset(np.datetime64(record_iso, "D"), 0, roll="backward", holidays=_HOL_ARR)  # L
        return str(np.busday_offset(last, -n, holidays=_HOL_ARR))
    except Exception:
        return ""

def prev_business_day(d, hol):
    """d(date)가 주말·휴장일(hol)이면 직전 영업일로 당김."""
    while d.weekday() >= 5 or d in hol:
        d -= datetime.timedelta(days=1)
    return d

def is_business_day(d):
    """d(date) 가 주말·휴장일이 아닌가"""
    return bool(np.is_busday(np.datetime64(d), holidays=_HOL_ARR))

# 수능일 — KRX 개장·마감 1시간 늦춤(10:00~16:30). 교육부가 매년 발표하면 추가
EXAM_DAYS = {datetime.date(2025, 11, 13), datetime.date(2026, 11, 19)}

def session_delay(d):
    """그날 KRX 개장·마감이 몇 분 늦는가 (개장, 마감) — 수능일 (60, 60), 연초 첫 거래일 (60, 0), 평소 (0, 0)"""
    if d in EXAM_DAYS:
        return (60, 60)
    first = datetime.date(d.year, 1, 2)
    while not is_business_day(first):
        first += datetime.timedelta(days=1)
    return (60, 0) if d == first else (0, 0)

def next_bday(iso):
    """iso 다음 영업일(주말·휴장일 제외) — 당일 거래정지의 재개일 등."""
    return str(np.busday_offset(np.datetime64(iso), 1, roll="forward", holidays=_HOL_ARR))

def to_date(s):
    """'YYYY-MM-DD'·'YYYYMMDD' → date, 못 읽으면 None"""
    s = (s or "").replace("-", "")
    return datetime.date(int(s[:4]), int(s[4:6]), int(s[6:8])) if len(s) >= 8 and s[:8].isdigit() else None

def iso(d):
    return d.isoformat() if d else ""

def norm_date(s):
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", s or "")
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""
