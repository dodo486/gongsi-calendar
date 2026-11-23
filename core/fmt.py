# -*- coding: utf-8 -*-
"""숫자·비율 — 여러 모듈이 같은 식을 각자 짜지 않게 한 곳에 둔다. (날짜는 core.krx_cal)"""


def num(s):
    """'1,234' · 1234 · ' 12.5 ' → float / 빈 값·못 읽으면 None"""
    try:
        return float(str(s if s is not None else "").replace(",", "").strip())
    except ValueError:
        return None


def pct(a, b, nd=2):
    """a 가 b 보다 몇 % 높은가 = (a − b) / |b| × 100, nd 자리 반올림. a 가 없거나 b 가 0·없으면 None"""
    return round((a - b) / abs(b) * 100, nd) if a is not None and b else None


def signed_pct(v, nd=2):
    """+1.23% · −0.50% / 없으면 '-'"""
    return "-" if v is None else f"{v:+.{nd}f}%"
