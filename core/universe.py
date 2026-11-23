# -*- coding: utf-8 -*-
"""수집·계산 대상 종목 — 감시종목(코스피200+코스닥150)과 배당 대상(감시종목 ∪ 지수배당포인트용 ETF PDF 구성종목, 2026-09-29 결정)"""
import os
from core.paths import BASE, DATA_DIR, load_json

DART_MARKETS = {"Y": "KOSPI", "K": "KOSDAQ"}                    # DART 목록 corp_cls → 시장 (그 밖은 KOSDAQ 으로 본다)
KIND_MARKETS = {"K1": "KOSPI", "K2": "KOSDAQ", "K3": "파생"}    # KIND 당일공시 시장 구분 → 시장
INDICES = [   # quote_code = 시세 허브 KRX 지수 시세 코드
    {"key": "kospi200", "name": "코스피200", "quote_code": "KPI200", "etf": "KODEX 200", "etf_code": "069500",
     "src": "kodex", "fid": "2ETF01", "level_fund": "KR7102110004"},   # 지수 종가: TIGER 200
    {"key": "kosdaq150", "name": "코스닥150", "quote_code": "KQI150", "etf": "KODEX 코스닥150", "etf_code": "229200",
     "src": "kodex", "fid": "2ETF54", "level_fund": "KR7232080002"},   # 지수 종가: TIGER 코스닥150
    {"key": "krx300", "name": "KRX300", "etf": "TIGER KRX300", "etf_code": "292160",
     "src": "tiger", "fund": "KR7292160009", "level_fund": "KR7292160009"},
]
PDF_PATH = os.path.join(DATA_DIR, "store", "index_pdf.json")   # 지수 PDF 원천(ingest.feeds.index_pdf) — 저장소에 둠

_cache = {"mtime": None, "data": None}
def stocks():
    """감시종목(코스피200+코스닥150) {종목코드: {name, market}}. watchlist.json 이 없으면 None(필터 없음)."""
    path = os.path.join(BASE, "watchlist.json")
    if not os.path.exists(path):
        print("  [!] watchlist.json 없음 — 전체 공시 표시(필터 없음)")
        return None
    mtime = os.path.getmtime(path)   # 파일이 바뀔 때만 다시 읽음 — 폴링마다(20초) 다시 읽지 않게
    if _cache["mtime"] != mtime:
        _cache["data"] = load_json(path, {}).get("stocks", {})   # data 를 먼저 — 동시 호출 스레드가 None data 를 보지 않게
        _cache["mtime"] = mtime
    return _cache["data"]

def watch():
    return set(stocks() or {})

def watch_names():
    """감시종목 회사명 → (종목코드, 시장). KIND 목록엔 회사명만 있어 코드로 바꿀 때 쓴다."""
    m = {}
    for code, info in (stocks() or {}).items():
        nm = (info.get("name") or "").strip()
        if nm:
            m[nm] = (code, info.get("market", ""))
    return m

def div():
    pdf = load_json(PDF_PATH, {})
    return watch() | {r["code"] for ix in pdf.get("indices", {}).values() for r in ix.get("rows", [])}
