# -*- coding: utf-8 -*-
"""시세 조회 — jhts 시세수집팀 위임 shim.

예전엔 이 모듈이 네이버/wisereport를 직접 스크래핑했다. 지금은 그 수집 코드를
전부 jhts 시세수집팀(jhts.marketdata)으로 옮겼고, 여기서는 md_feed를 통해
받아온다. 공개 함수 이름·반환 형태는 그대로라 호출부는 바꿀 필요 없다.

- quote_batch(codes)  : 현재값 배치 {code:{name,price,rate,value,volume,mktcap}}
- stock_rate/index_rate: 현재/지수 등락률 {price,rate(부호%),sign}
- daily_price(code)   : 일별 OHLC 최신→과거
- frgn_daily(code)    : 일별 외국인·기관 순매매 최신→과거
- investor_trend(code): 일별 투자자 순매수(개인 포함) 최신→과거
- float_shares(code)  : 유동주식수
"""
import md_feed


def quote_batch(codes):
    return md_feed.quote_batch(codes)


def stock_rate(code):
    return md_feed.stock_rate(code)


def index_rate(code):
    """code: KPI200(코스피200)/KQI150(코스닥150)."""
    return md_feed.index_rate(code)


def daily_price(code, n=40):
    return md_feed.daily_price(code, n)


def frgn_daily(code, pages=2):
    return md_feed.frgn_daily(code, pages)


def investor_trend(code, n=20):
    return md_feed.investor_trend(code, n)


def float_shares(code):
    return md_feed.float_shares(code)
