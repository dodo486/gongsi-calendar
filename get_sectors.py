"""섹터 택소노미 빌더 — 종목별 '테마' 태그(ETF PDF 합의 기반) 부여.

유니버스는 전체시장 거래대금 상위(market_universe) ∪ 공시 watchlist.
테마는 ETF 구성종목(get_etf_themes) 합의 + 소수 수동 큐레이션.

  python get_sectors.py            # 유니버스 테마 분류
  python get_sectors.py 1000       # 유니버스 크기 지정

sectors.json 구조:
{ "generated_at": "...",
  "stocks": { "005930": {"name":"삼성전자","themes":["반도체","HBM"]}, ... } }
"""
import sys, json, os, datetime
from fetch import DATA_DIR, load_watchlist

OUT = os.path.join(DATA_DIR, "sectors.json")
ETF_THEMES_PATH = os.path.join(DATA_DIR, "etf_themes.json")
DEFAULT_N = 800   # 수급 유니버스 크기(거래대금 상위)

# ETF PDF 기반 테마 중 너무 광범위해 노이즈가 큰 것(대형주 쏠림) 제외
ETF_THEME_BLOCK = {"AI", "인터넷플랫폼", "지주"}
# 합의(consensus) 판정: 같은 테마 ETF 여러 개에 '공통'으로 담긴 종목만 그 테마로 인정.
# (1개 ETF에만 담긴 건 광범위 ETF에서 새어든 노이즈로 간주)
ETF_MIN_SCORE = 2   # 최소 몇 개의 동일테마 ETF에 공통으로 담겨야 하는지


def load_etf_themes():
    """ETF 구성종목 기반 code→themes 맵. 공통도(consensus) 필터 적용.
    종목이 같은 테마 ETF ETF_MIN_SCORE개 이상에 담겨야 그 테마로 인정.
    단, 그 테마의 ETF가 애초에 ETF_MIN_SCORE개 미만이면 1개라도 인정."""
    if not os.path.exists(ETF_THEMES_PATH):
        return {}
    try:
        d = json.load(open(ETF_THEMES_PATH, encoding="utf-8"))
    except Exception:
        return {}
    etf_cnt = {th: v.get("etf_count", 1) for th, v in d.get("themes", {}).items()}
    out = {}
    for code, v in d.get("stocks", {}).items():
        score = v.get("score", {})
        themes = []
        for t in v.get("themes", []):
            if t in ETF_THEME_BLOCK:
                continue
            need = min(ETF_MIN_SCORE, etf_cnt.get(t, 1))   # ETF 적은 테마는 완화
            if score.get(t, 0) >= need:
                themes.append(t)
        if themes:
            out[code] = themes
    return out

# 수동 테마 = ETF PDF(get_etf_themes)가 못 잡는 '틈새'만 보강.
# (대부분의 테마는 ETF 구성종목에서 자동 추출 — 여기선 국내 테마 ETF가 없는 것만)
THEMES = {
    # HBM (반도체 세부 — 별도 신호로 유지)
    "005930": ["HBM"], "000660": ["HBM"],
    # 희토류/희소금속 (국내 테마 ETF 부재)
    "010130": ["희토류"], "000910": ["희토류"], "047400": ["희토류"],
    "285490": ["희토류"], "037370": ["희토류"], "027580": ["희토류"],
    # 광통신/광학 (국내 테마 ETF 부재)
    "138080": ["광통신"], "356860": ["광통신"], "010170": ["광통신"],
    "230240": ["광통신"], "046970": ["광통신"],
}


def load_cache():
    if os.path.exists(OUT):
        try:
            return json.load(open(OUT, encoding="utf-8")).get("stocks", {})
        except Exception:
            pass
    return {}


def classify(codes_names, refresh=False):
    """codes_names: {code: name}. 종목별 name + 테마(ETF PDF 합의 ∪ 수동) 만 저장.
    (WICS 표준업종 크롤은 제거됨)"""
    cache = load_cache()
    etf_themes = load_etf_themes()
    targets = list(codes_names.keys())
    for code, name in codes_names.items():
        e = cache.setdefault(code, {})
        if name:
            e["name"] = name
        e.pop("wics", None); e.pop("wics_no", None)   # 레거시 WICS 필드 제거
        # 테마 = ETF PDF 기반(주) ∪ 수동 큐레이션(보조)
        e["themes"] = list(dict.fromkeys(etf_themes.get(code, []) + THEMES.get(code, [])))
    payload = {"generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "stocks": cache}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=0)
    themed = sum(1 for c in targets if cache.get(c, {}).get("themes"))
    print(f"저장: {OUT} | 유니버스 {len(targets)}종목 · 테마보유 {themed} · 캐시총 {len(cache)}")
    return payload


def build(n=DEFAULT_N, refresh=False):
    """유니버스 = 전체시장 거래대금 상위 N ∪ 공시 watchlist."""
    import market_universe as mu
    cn = {r["code"]: r["name"] for r in mu.top_universe(n)}
    wl = load_watchlist() or {}
    for code, info in wl.items():
        cn.setdefault(code, info.get("name", ""))
    return classify(cn, refresh=refresh)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args and args[0].isdigit():
        build(n=int(args[0]))
    else:
        build()
