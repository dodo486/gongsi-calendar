# -*- coding: utf-8 -*-
"""
watchlist.json 생성/갱신
- KOSPI200: 네이버 금융에서 실시간 수집 (이 PC에서 정상 작동, 자동 갱신 가능)
- KOSDAQ150: 임시 스냅샷(kosdaq150_snapshot.json) 사용
             ⚠️ TODO: KRX 자동화(실제 크롬 CDP) 또는 ETF 보유내역으로 교체 예정
"""
import os, re, urllib.request
from core.paths import BASE, save_json, load_json
from ingest.sources import net   # 사내망 TLS opener 세팅
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"}

def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("euc-kr", "replace")  # 네이버 금융은 euc-kr

def kospi200_from_naver():
    stocks = {}
    for page in range(1, 25):
        html = fetch(f"https://finance.naver.com/sise/entryJongmok.naver?type=KPI200&page={page}")
        pairs = re.findall(r'/item/main\.naver\?code=(\d{6})"[^>]*>([^<]+)</a>', html)
        if not pairs:
            break
        for code, name in pairs:
            stocks[code] = {"name": name.strip(), "market": "KOSPI"}
    return stocks

def kosdaq150_from_snapshot():
    path = os.path.join(BASE, "kosdaq150_snapshot.json")
    snap = load_json(path, None)
    if not snap:
        print("  [!] kosdaq150_snapshot.json 없음 — 코스닥150 비어있음")
        return {}
    return {c: {"name": v["name"], "market": "KOSDAQ"} for c, v in snap["stocks"].items()}

def main():
    kospi = kospi200_from_naver()
    print(f"  KOSPI200(네이버): {len(kospi)}종목")
    kosdaq = kosdaq150_from_snapshot()
    print(f"  KOSDAQ150(스냅샷): {len(kosdaq)}종목")
    stocks = {**kospi, **kosdaq}
    payload = {
        "count": len(stocks),
        "kospi200": len(kospi),
        "kosdaq150": len(kosdaq),
        "sources": {"kospi200": "naver(live)", "kosdaq150": "snapshot(TODO:automate)"},
        "stocks": stocks,
    }
    out = os.path.join(BASE, "watchlist.json")
    save_json(out, payload)
    print(f"저장: {out} (총 {len(stocks)}종목)")

if __name__ == "__main__":
    main()
