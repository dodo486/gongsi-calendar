# -*- coding: utf-8 -*-
"""
watchlist.json 생성/갱신
- KOSPI200: 네이버 금융에서 실시간 수집 (이 PC에서 정상 작동, 자동 갱신 가능)
- KOSDAQ150: 임시 스냅샷(kosdaq150_snapshot.json) 사용
             ⚠️ TODO: KRX 자동화(실제 크롬 CDP) 또는 ETF 보유내역으로 교체 예정
"""
import json, os, re, ssl, urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))

def opener():
    try:
        import truststore; truststore.inject_into_ssl(); return
    except Exception: pass
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
        urllib.request.install_opener(urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)))
    except Exception: pass

opener()
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
    if not os.path.exists(path):
        print("  [!] kosdaq150_snapshot.json 없음 — 코스닥150 비어있음")
        return {}
    snap = json.load(open(path, encoding="utf-8"))
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
    json.dump(payload, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"저장: {out} (총 {len(stocks)}종목)")

if __name__ == "__main__":
    main()
