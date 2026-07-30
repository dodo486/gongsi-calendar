# -*- coding: utf-8 -*-
"""
DART 공시 수집기 (v1)
- 최근 N일간 코스피(Y) + 코스닥(K) 공시를 받아서 data/disclosures.json 으로 저장
- 웹(index.html)이 이 JSON을 읽어 캘린더에 표시
- TLS 검사 환경 대응: truststore -> certifi -> (최후) unverified 순으로 시도
"""
import json, os, sys, time, datetime, urllib.request, urllib.error, ssl

BASE = os.path.dirname(os.path.abspath(__file__))
_KEY_PATH = os.path.join(BASE, "dart_key.txt")
if not os.path.exists(_KEY_PATH):
    sys.exit("[!] dart_key.txt 없음 — dart_key.txt.example 을 dart_key.txt 로 복사하고 "
             "OpenDART API 키를 넣어주세요 (무료 발급: https://opendart.fss.or.kr)")
KEY = open(_KEY_PATH, encoding="utf-8").read().strip()

def save_json(path, obj):
    """원자적 저장 — 쓰다 만 파일을 웹이 읽는 사고 방지 (tmp에 쓰고 교체)"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)

# 관심 공시 종류: (제목 키워드, 표시 카테고리). 순서 = 매칭 우선순위.
CATEGORIES = [
    ("유상증자", "유상증자"),
    ("무상증자", "무상증자"),
    ("자기주식", "자사주"),
    ("전환사채", "메자닌"), ("신주인수권부사채", "메자닌"), ("교환사채", "메자닌"),
    ("감자", "감자"),
    ("합병", "합병/분할"), ("분할", "합병/분할"),
    ("최대주주변경", "최대주주변경"),   # '최대주주등소유주식변동신고서'(단순 지분변동) 제외 위해 '변경'까지 명시
    ("영업(잠정)실적", "실적"), ("실적", "실적"), ("손익구조", "실적"),
    ("배당", "배당"),
]
REPORT_KEYWORDS = [k for k, _ in CATEGORIES]   # 빈 리스트면 종류 필터 없음
# DART 공시유형: B=주요사항보고(증자·자사주·CB/BW·감자·합병분할), I=거래소공시(배당·실적·최대주주변경)
PBLNTF_TYPES = ["B", "I"]
# 공시 캘린더에서 제외할 카테고리 — 배당은 별도 '배당 캘린더'(dividends.py)가 전담
CAL_EXCLUDE = {"배당"}

def categorize(title):
    for kw, label in CATEGORIES:
        if kw in (title or ""):
            return label
    return "기타"
DATA_DIR = os.path.join(BASE, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# --- HTTPS 준비 (사내 보안 프로그램 TLS 검사 대응) ---
def make_opener():
    try:
        import truststore
        truststore.inject_into_ssl()
        return "truststore"
    except Exception:
        pass
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
        urllib.request.install_opener(urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)))
        return "certifi"
    except Exception:
        pass
    if os.environ.get("DART_INSECURE_TLS") == "1":
        # 사내 TLS 검사 등으로 정상 검증이 불가능할 때만 명시적으로 켜는 최후 수단
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        urllib.request.install_opener(urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)))
        print("[경고] DART_INSECURE_TLS=1 — TLS 인증서 검증 없이 접속합니다")
        return "unverified"
    return "default"  # 시스템 기본 인증서로 검증 (실패 시 DART_INSECURE_TLS=1 안내됨)

TLS_MODE = make_opener()

def api(params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"https://opendart.fss.or.kr/api/list.json?{q}"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError as ex:
        if isinstance(getattr(ex, "reason", None), ssl.SSLError):
            print("  [!] TLS 인증 실패 — 사내망이면 `pip install truststore` 또는 "
                  "환경변수 DART_INSECURE_TLS=1 을 검토하세요")
        raise

def fetch_range(bgn, end, corp_cls, pblntf_ty=None):
    """해당 시장(corp_cls)의 기간 내 공시를 페이지 넘겨가며 수집 (pblntf_ty로 유형 제한)"""
    out, page = [], 1
    while True:
        params = {
            "crtfc_key": KEY, "bgn_de": bgn, "end_de": end,
            "corp_cls": corp_cls, "page_no": page, "page_count": 100,
        }
        if pblntf_ty:
            params["pblntf_ty"] = pblntf_ty
        d = api(params)
        if d.get("status") != "000":
            if d.get("status") == "013":  # 조회된 데이터 없음
                break
            print(f"  [!] {corp_cls} status={d.get('status')} {d.get('message')}")
            break
        out.extend(d.get("list", []))
        total_page = d.get("total_page", 1)
        if page >= total_page:
            break
        page += 1
        time.sleep(0.15)  # API 예의상 살짝 쉼
    return out

def load_watchlist():
    path = os.path.join(BASE, "watchlist.json")
    if not os.path.exists(path):
        print("  [!] watchlist.json 없음 — 전체 공시 표시(필터 없음)")
        return None
    wl = json.load(open(path, encoding="utf-8"))
    return wl.get("stocks", {})

def collect_events(bgn, end, watch="__load__", verbose=True):
    """기간 내 감시대상+관심종류 공시를 수집해 캘린더용 이벤트 리스트로 반환 (fetch/monitor 공용)"""
    if watch == "__load__":
        watch = load_watchlist()
    rows, seen = [], set()
    for cls, label in [("Y", "KOSPI"), ("K", "KOSDAQ")]:
        raw_n = kept = 0
        for ty in PBLNTF_TYPES:
            got = fetch_range(bgn, end, cls, ty)
            raw_n += len(got)
            for r in got:
                rno = r.get("rcept_no")
                if rno in seen:
                    continue
                if watch is not None and r.get("stock_code") not in watch:
                    continue
                if REPORT_KEYWORDS and not any(k in (r.get("report_nm") or "") for k in REPORT_KEYWORDS):
                    continue
                seen.add(rno)
                rows.append(r)
                kept += 1
        if verbose:
            print(f"  {label}: {raw_n}건 → {kept}건 (종목+종류 필터 후)")

    events = []
    for r in rows:
        rno = r.get("rcept_no", "")
        title = (r.get("report_nm", "") or "").strip()
        events.append({
            "corp": r.get("corp_name", ""),
            "stock": r.get("stock_code", ""),
            "market": "KOSPI" if r.get("corp_cls") == "Y" else "KOSDAQ",
            "title": title,
            "category": categorize(title),
            "date": r.get("rcept_dt", ""),   # YYYYMMDD
            "rcept_no": rno,
            "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rno}",
            "flr": r.get("flr_nm", ""),
        })
    return events

def main(days=7):
    today = datetime.date.today()
    bgn = (today - datetime.timedelta(days=days)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    print(f"TLS={TLS_MODE} | 기간 {bgn}~{end}")
    events = [e for e in collect_events(bgn, end) if e["category"] not in CAL_EXCLUDE]

    # 기존 파일과 병합 — 짧은 기간 백필이 기존 이력을 지우지 않게 (rcept_no 기준 중복 제거)
    out_path = os.path.join(DATA_DIR, "disclosures.json")
    if os.path.exists(out_path):
        have = {e["rcept_no"] for e in events}
        old = json.load(open(out_path, encoding="utf-8")).get("events", [])
        events += [e for e in old if e.get("rcept_no") not in have]
    events.sort(key=lambda e: e.get("date", ""), reverse=True)

    dates = [e["date"] for e in events if e.get("date")]
    payload = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "range": {"bgn": min(dates), "end": max(dates)} if dates else {},
        "count": len(events),
        "events": events,
    }
    save_json(out_path, payload)
    print(f"저장: {out_path} ({len(events)}건)")

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    main(n)
