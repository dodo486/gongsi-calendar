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
PDF_PATH = os.path.join(DATA_DIR, "store", "index_pdf.json")   # 지수 PDF 원천(ingest.feeds.index_pdf) — 저장소에 둠

def watch():
    return set(load_watchlist() or {})

def watch_names():
    """감시종목 회사명 → (종목코드, 시장). KIND 목록엔 회사명만 있어 코드로 바꿀 때 쓴다."""
    m = {}
    for code, info in (load_watchlist() or {}).items():
        nm = (info.get("name") or info.get("corp") or "").strip()
        if nm:
            m[nm] = (code, info.get("market", ""))
    return m

def div():
    pdf = load_json(PDF_PATH, {})
    return watch() | {r["code"] for ix in pdf.get("indices", {}).values() for r in ix.get("rows", [])}
