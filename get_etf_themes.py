# -*- coding: utf-8 -*-
"""ETF 구성종목(PDF) 기반 테마 자동 분류 — 손수 큐레이션 대체.

아이디어: 테마형 ETF의 구성종목은 운용사가 전문적으로 리밸런싱한 '정답지'.
국내 업종/테마 ETF(네이버 etfTabCode=2)의 TOP10 구성종목을 수집해
"이 종목이 어느 테마 ETF에 담겼나 = 그 종목의 테마" 로 역산.

  python get_etf_themes.py         # data/etf_themes.json 생성
  python get_etf_themes.py --show  # 테마별 종목 요약 출력

출력 data/etf_themes.json:
{ "generated_at": "...",
  "themes": { "반도체": {"codes":["005930",...], "etfs":["TIGER 반도체TOP10",...]}, ... },
  "stocks": { "005930": {"themes": ["반도체","HBM"], "score": {"반도체":4,"HBM":2}}, ... } }
"""
import sys, os, json, datetime, requests
from concurrent.futures import ThreadPoolExecutor
import fetch  # ssl 패치

HDR = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
_S = requests.Session(); _S.headers.update(HDR)
_S.mount("https://", requests.adapters.HTTPAdapter(pool_connections=12, pool_maxsize=24))
OUT = os.path.join(fetch.DATA_DIR, "etf_themes.json")

ETF_LIST = "https://finance.naver.com/api/sise/etfItemList.nhn"
ETF_ANALYSIS = "https://m.stock.naver.com/api/stock/{}/etfAnalysis"
DOMESTIC_THEME_TAB = 2   # 네이버 ETF 탭: 2=국내 업종/테마

# 해외·전략형 ETF 배제(국내 테마만 남기기)
EXCLUDE = ["미국", "글로벌", "차이나", "중국", "일본", "인도", "베트남", "유럽", "선진", "신흥",
           "나스닥", "S&P", "필라델피아", "다우", "닛케이", "항셍", "리츠", "커버드콜", "위클리",
           "배당", "고배당", "채권", "금리", "혼합", "TR", "레버리지", "인버스", "환율", "달러",
           "필수소비재", "은행채"]   # 부분문자열 오매칭 방지(필수소비재⊃수소, 은행채⊃은행)

# 정규 테마 ← ETF 이름 키워드 매칭(다중 매칭 허용). 순서 무관, 부분일치.
# 주의: 부분문자열 매칭이라 접미사(소부장 등)는 섹터 전용 키워드로 한정할 것.
THEME_KEYWORDS = {
    "반도체": ["반도체", "시스템반도체", "HBM"],  # 반도체 소부장/전공정/후공정 모두 포함
    "HBM": ["HBM"],
    "2차전지": ["2차전지", "이차전지", "배터리"],
    "바이오": ["바이오", "헬스케어", "제약", "신약"],
    "미용": ["미용", "뷰티", "에스테틱"],  # 의료기기 제외(미용≠의료기기)
    "화장품": ["화장품"],
    "로봇": ["로봇"],
    "방산우주": ["방산", "국방", "우주", "항공"],
    "원전": ["원자력", "원전"],
    "전력설비": ["전력", "전력기기", "송전", "변압"],
    "신재생에너지": ["신재생", "태양광", "풍력", "수소", "친환경에너지"],
    "AI": ["AI", "인공지능"],
    "자동차": ["자동차", "전기차", "모빌리티", "자율주행"],
    "조선": ["조선"],
    "게임": ["게임", "e스포츠"],
    "인터넷플랫폼": ["인터넷", "플랫폼", "IT"],
    "엔터미디어": ["엔터", "미디어", "K-POP", "케이팝", "콘텐츠"],
    "금융": ["은행", "증권", "보험", "금융"],
    "지주": ["지주", "홀딩스"],
    "희토류": ["희토류", "희소금속"],
}


def fetch_etf_list():
    j = _S.get(ETF_LIST, timeout=10).json()
    etfs = j.get("result", {}).get("etfItemList", [])
    out = []
    for e in etfs:
        if e.get("etfTabCode") != DOMESTIC_THEME_TAB:
            continue
        name = e.get("itemname", "")
        if any(x in name for x in EXCLUDE):
            continue
        out.append({"code": e["itemcode"], "name": name})
    return out


def match_themes(etf_name):
    hits = []
    for theme, kws in THEME_KEYWORDS.items():
        if any(k in etf_name for k in kws):
            hits.append(theme)
    return hits


def fetch_top10(code):
    try:
        j = _S.get(ETF_ANALYSIS.format(code), timeout=8).json()
        return j.get("etfTop10MajorConstituentAssets", []) or []
    except Exception:
        return []


def _load_full_pdf():
    """KRX 전체 PDF 캐시(get_etf_pdf.py). 있으면 top10 대신 전체 구성종목 사용."""
    p = os.path.join(fetch.DATA_DIR, "etf_pdf.json")
    if not os.path.exists(p):
        return {}
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for c, v in d.get("etfs", {}).items():
        cons = v.get("constituents", [])
        if not cons:
            continue
        # top10 형식(itemCode/itemName/etfWeight)으로 정규화
        out[c] = [{"itemCode": x.get("code"), "itemName": x.get("name"),
                   "etfWeight": x.get("weight")} for x in cons]
    return out


def build():
    etfs = fetch_etf_list()
    # 테마 매칭되는 ETF만
    themed = [(e, match_themes(e["name"])) for e in etfs]
    themed = [(e, t) for e, t in themed if t]
    full_pdf = _load_full_pdf()
    src = f"전체PDF({len(full_pdf)}개 ETF)" if full_pdf else "네이버 top10"
    print(f"국내 테마 ETF {len(etfs)}개 중 테마매칭 {len(themed)}개 — 구성종목 수집 [소스: {src}]…")

    def _one(pair):
        e, themes = pair
        cons = full_pdf.get(e["code"]) or fetch_top10(e["code"])
        return e, themes, cons

    theme_stock = {}   # theme -> {code: count}
    theme_etfs = {}    # theme -> set(etf name)
    stock_score = {}   # code -> {theme: count}
    names = {}         # code -> name
    etfs_out = {}      # etf code -> {name, themes, constituents:[{code,name,weight}]}
    with ThreadPoolExecutor(max_workers=12) as ex:
        for e, themes, holdings in ex.map(_one, themed):
            cons = []
            for th in themes:
                theme_etfs.setdefault(th, set()).add(e["name"])
            for h in holdings:
                code = h.get("itemCode"); nm = h.get("itemName")
                if not code or len(code) != 6:   # 해외/현금성 제외
                    continue
                names[code] = nm
                wt = h.get("etfWeight")
                cons.append({"code": code, "name": nm, "weight": wt})
                for th in themes:
                    theme_stock.setdefault(th, {})[code] = theme_stock.setdefault(th, {}).get(code, 0) + 1
                    stock_score.setdefault(code, {})[th] = stock_score.setdefault(code, {}).get(th, 0) + 1
            etfs_out[e["code"]] = {"name": e["name"], "themes": themes, "constituents": cons}

    themes_out = {}
    for th, cd in theme_stock.items():
        codes = sorted(cd, key=lambda c: -cd[c])
        etf_names = sorted(theme_etfs.get(th, []))
        themes_out[th] = {"codes": codes, "count": len(codes),
                          "etf_count": len(etf_names),        # 이 테마에 묶인 ETF 수
                          "member_count": {c: cd[c] for c in codes},  # 종목별 공통도(담긴 ETF 수)
                          "etfs": etf_names}
    stocks_out = {c: {"name": names.get(c, ""),
                      "themes": sorted(sc, key=lambda t: -sc[t]),
                      "score": sc}
                  for c, sc in stock_score.items()}

    payload = {"generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "themes": themes_out, "stocks": stocks_out, "etfs": etfs_out}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=0)
    print(f"저장: {OUT} | 테마 {len(themes_out)}종 · 종목 {len(stocks_out)}개")
    return payload


def show():
    d = json.load(open(OUT, encoding="utf-8"))
    for th, v in sorted(d["themes"].items(), key=lambda kv: -kv[1]["count"]):
        codes = v["codes"][:12]
        nms = [d["stocks"][c]["name"] for c in codes]
        print(f"\n■ {th} ({v['count']}종목, ETF {len(v['etfs'])}개)")
        print("   ", ", ".join(nms))


if __name__ == "__main__":
    if "--show" in sys.argv:
        show()
    else:
        build()
        show()
