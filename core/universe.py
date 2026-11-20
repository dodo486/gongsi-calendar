# -*- coding: utf-8 -*-
"""수집·계산 대상 종목 — 감시종목(코스피200+코스닥150)과 배당 대상(감시종목 ∪ 지수배당포인트용 ETF PDF 구성종목, 2026-09-29 결정)"""
import os
from core.paths import DATA_DIR, load_watchlist, load_json

INDICES = [
    {"key": "kospi200", "name": "코스피200", "etf": "KODEX 200", "etf_code": "069500",
     "src": "kodex", "fid": "2ETF01", "level_fund": "KR7102110004"},   # 지수 종가: TIGER 200
    {"key": "kosdaq150", "name": "코스닥150", "etf": "KODEX 코스닥150", "etf_code": "229200",
     "src": "kodex", "fid": "2ETF54", "level_fund": "KR7232080002"},   # 지수 종가: TIGER 코스닥150
    {"key": "krx300", "name": "KRX300", "etf": "TIGER KRX300", "etf_code": "292160",
     "src": "tiger", "fund": "KR7292160009", "level_fund": "KR7292160009"},
]
PDF_PATH = os.path.join(DATA_DIR, "index_pdf.json")

def watch():
    return set(load_watchlist() or {})

def div():
    pdf = load_json(PDF_PATH, {})
    return watch() | {r["code"] for ix in pdf.get("indices", {}).values() for r in ix.get("rows", [])}
