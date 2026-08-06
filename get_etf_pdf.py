# -*- coding: utf-8 -*-
"""KRX 전체 ETF PDF(구성종목 전체) 수집 — top10 대비 소형 구성종목까지 커버.

⚠️ 이 스크립트는 KRX 데이터시스템(data.krx.co.kr)에 직접 접속한다.
   회사 TLS 검사 프록시 환경에선 KRX가 'LOGOUT'/403 으로 차단하므로,
   **집 등 일반 네트워크에서 실행**해야 한다. 차단 시 아무것도 저장하지 않고
   경고만 출력 → get_etf_themes.py 는 자동으로 네이버 top10 로 폴백한다.

  python get_etf_pdf.py          # 국내 테마 ETF 전체 PDF → data/etf_pdf.json
  python get_etf_pdf.py --test   # KRX 접속 가능 여부만 진단

출력 data/etf_pdf.json:
{ "generated_at": "...",
  "etfs": { "305540": {"name":"TIGER 2차전지테마",
                        "constituents":[{"code":"373220","name":"LG에너지솔루션","weight":15.59}, ...]}, ... } }
"""
import sys, os, json, datetime, requests
from concurrent.futures import ThreadPoolExecutor
import fetch  # ssl 패치
import get_etf_themes as gt   # 국내 테마 ETF 목록 재사용

OUT = os.path.join(fetch.DATA_DIR, "etf_pdf.json")
KRX_BASE = "http://data.krx.co.kr"
LOADER = KRX_BASE + "/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020506"
JSON_URL = KRX_BASE + "/comm/bldAttendant/getJsonData.cmd"
BLD_PDF = "dbms/MDC/STAT/standard/MDCSTAT05001"   # ETF PDF(자산구성내역)
BLD_LIST = "dbms/MDC/STAT/standard/MDCSTAT04601"  # ETF 전종목(ISU 매핑)


def _session():
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": LOADER,
                      "X-Requested-With": "XMLHttpRequest"})
    try:
        s.get(LOADER, timeout=10)   # 세션 쿠키
    except Exception:
        pass
    return s


def _post(s, bld, **params):
    data = {"bld": bld, "locale": "ko_KR"}
    data.update(params)
    r = s.post(JSON_URL, data=data, timeout=12)
    txt = r.text.strip()
    if txt == "LOGOUT" or r.status_code != 200 or not txt.startswith("{"):
        raise RuntimeError(f"KRX 차단/오류(status={r.status_code}, body={txt[:20]})")
    return r.json()


def _trdday():
    # 장 시작 전이면 전일 사용(오전 8시 이전)
    now = datetime.datetime.now()
    d = now.date()
    if now.hour < 8:
        d = d - datetime.timedelta(days=1)
    while d.weekday() >= 5:   # 토/일 → 금요일로
        d = d - datetime.timedelta(days=1)
    return d.strftime("%Y%m%d")


def isu_map(s):
    """ETF 단축코드(6자리) → ISU 표준코드(KR7...)."""
    j = _post(s, BLD_LIST, share="1", csvxls_isNo="false")
    out = {}
    for x in j.get("output", []):
        srt = x.get("ISU_SRT_CD"); isu = x.get("ISU_CD")
        if srt and isu:
            out[srt] = isu
    return out


def fetch_pdf(s, isu, trd):
    j = _post(s, BLD_PDF, trdDd=trd, isuCd=isu, share="1", money="1", csvxls_isNo="false")
    out = []
    for x in j.get("output", []):
        code = x.get("COMPST_ISU_CD", "").strip()
        # KRX 는 종목코드를 KR7XXXXXX 혹은 6자리로 줌 → 6자리 정규화
        if len(code) > 6:
            m = code[3:9] if code.startswith("KR") else code[-6:]
            code = m
        if not code.isdigit() or len(code) != 6:
            continue
        w = x.get("COMPST_RTO") or x.get("COMPST_AMT")
        try:
            weight = float(str(w).replace(",", ""))
        except Exception:
            weight = None
        out.append({"code": code, "name": x.get("COMPST_ISU_NM", "").strip(), "weight": weight})
    return out


def build():
    s = _session()
    trd = _trdday()
    try:
        imap = isu_map(s)
    except Exception as e:
        print(f"❌ KRX 접속 불가 — {e}\n   (회사 프록시 환경일 수 있음. 집/일반 네트워크에서 재실행하세요.)")
        print("   get_etf_themes.py 는 네이버 top10 로 자동 폴백합니다.")
        return None

    etfs = [e for e in gt.fetch_etf_list() if gt.match_themes(e["name"])]
    print(f"KRX 전체 PDF 수집 — 테마 ETF {len(etfs)}개 (trdDd={trd})")
    result = {}

    def _one(e):
        isu = imap.get(e["code"])
        if not isu:
            return e["code"], e["name"], []
        try:
            return e["code"], e["name"], fetch_pdf(s, isu, trd)
        except Exception:
            return e["code"], e["name"], []

    ok = 0
    with ThreadPoolExecutor(max_workers=6) as ex:   # KRX 는 과도한 동시성 싫어함
        for code, name, cons in ex.map(_one, etfs):
            if cons:
                ok += 1
            result[code] = {"name": name, "constituents": cons}
    payload = {"generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "etfs": result}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=0)
    avg = sum(len(v["constituents"]) for v in result.values()) / max(ok, 1)
    print(f"저장: {OUT} | ETF {ok}/{len(etfs)}개 성공 · 평균 구성종목 {avg:.0f}개(top10 대비 확대)")
    return payload


if __name__ == "__main__":
    if "--test" in sys.argv:
        s = _session()
        try:
            m = isu_map(s)
            print(f"✅ KRX 접속 OK — ETF {len(m)}개 ISU 매핑 확보. build 가능.")
        except Exception as e:
            print(f"❌ KRX 접속 불가 — {e}")
    else:
        build()
