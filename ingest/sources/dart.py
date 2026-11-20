# -*- coding: utf-8 -*-
"""DART(OpenDART) 접속 — 공시 목록·원문·고유번호. 이벤트와 무관한 공용 수집 도구.
- 키: 환경변수 DART_KEY(CI) 또는 dart_key.txt
- TLS 는 net(사내망 대응), 저장·경로는 base 를 쓴다
"""
import io, json, os, re, sys, time, datetime, threading, zipfile, urllib.request, urllib.error, ssl
from core import store
from core.paths import BASE, save_json, CACHE_DIR
from ingest.sources import net   # import 시 TLS opener 세팅

_KEY_PATH = os.path.join(BASE, "dart_key.txt")
KEY = os.environ.get("DART_KEY", "").strip()   # CI(GitHub Actions)에선 시크릿 env로 주입
if not KEY:
    if not os.path.exists(_KEY_PATH):
        sys.exit("[!] dart_key.txt 없음 — dart_key.txt.example 을 dart_key.txt 로 복사하고 "
                 "OpenDART API 키를 넣어주세요 (무료 발급: https://opendart.fss.or.kr)")
    KEY = open(_KEY_PATH, encoding="utf-8").read().strip()

def api(params, endpoint="list", tries=5):
    """OpenDART JSON API. 연결 오류는 tries 번까지 5·10·20·40초 쉬며 재시도 — 실시간 감시는 tries=1 로 바로 실패."""
    q = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"https://opendart.fss.or.kr/api/{endpoint}.json?{q}"
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.URLError as ex:
            if isinstance(getattr(ex, "reason", None), ssl.SSLError):
                print("  [!] TLS 인증 실패 — 사내망이면 `pip install truststore` 또는 "
                      "환경변수 DART_INSECURE_TLS=1 을 검토하세요")
                raise
            if attempt == tries - 1:
                raise
        except (ConnectionError, TimeoutError):
            if attempt == tries - 1:
                raise
        time.sleep(5 * 2 ** attempt)

def fetch_range(bgn, end, corp_cls, pblntf_ty=None, strict=False, tries=5):
    """해당 시장(corp_cls)의 기간 내 공시를 페이지 넘겨가며 수집 (pblntf_ty로 유형 제한).
    strict=True 면 DART 오류를 예외로 올린다 — 받은 구간을 저장해 두는 쪽이 빈 결과를 '훑음'으로 굳히지 않게."""
    out, page = [], 1
    while True:
        params = {
            "crtfc_key": KEY, "bgn_de": bgn, "end_de": end,
            "corp_cls": corp_cls, "page_no": page, "page_count": 100,
        }
        if pblntf_ty:
            params["pblntf_ty"] = pblntf_ty
        d = api(params, tries=tries)
        if d.get("status") != "000":
            if d.get("status") == "013":  # 조회된 데이터 없음
                break
            if strict:
                raise RuntimeError(f"DART {corp_cls} status={d.get('status')} {d.get('message')}")
            print(f"  [!] {corp_cls} status={d.get('status')} {d.get('message')}")
            break
        out.extend(d.get("list", []))
        total_page = d.get("total_page", 1)
        if page >= total_page:
            break
        page += 1
        time.sleep(0.15)  # API 예의상 살짝 쉼
    return out

# ── 공시 목록 저장본 — 받은 구간은 다시 받지 않는다 ──
LIST_PATH = os.path.join(store.DIR, "dart_listing.json")   # 목록 자체도 사실 — 계산이 저장소로 읽음
LIST_FIELDS = ("rcept_no", "rcept_dt", "report_nm", "stock_code", "corp_name", "corp_cls", "flr_nm")
LIST_RETAIN = {"I": 830, "A": 430, "B": 400}   # 유형별 보관 일수 — 가장 길게 쓰는 곳(리서치 이력 800일 등) + 여유
RECHECK_DAYS = 3                              # 늦게 올라오는 접수 대비 끝 며칠은 매번 다시
_DAY = datetime.timedelta(days=1)
_list_lock = threading.Lock()
_list = {"mtime": None, "store": None}

def _list_store():
    mt = os.path.getmtime(LIST_PATH) if os.path.exists(LIST_PATH) else None
    if _list["store"] is None or mt != _list["mtime"]:   # 다른 프로세스가 갱신했으면 다시 읽음
        _list["store"] = json.load(open(LIST_PATH, encoding="utf-8")) if mt else {}
        _list["mtime"] = mt
    return _list["store"]

def _list_save(store):
    save_json(LIST_PATH, store, indent=None)
    _list["mtime"] = os.path.getmtime(LIST_PATH)

def live_rows(bgn, end, types=("B", "I")):
    """monitor 실시간 감지용 — 좁은 구간(어제~오늘)을 저장 없이 바로 조회"""
    return [r for cls in ("Y", "K") for ty in types for r in fetch_range(bgn, end, cls, ty, tries=1)]

def listing(ty, days):
    """DART 목록(유형 ty: I 거래소공시 · A 정기보고서 · B 주요사항보고) 오늘부터 days일치, 코스피+코스닥.
    받은 구간(from~to)은 저장본을 쓰고 새 구간만 조회한다 — 끝 RECHECK_DAYS일은 늦은 접수 대비 다시.
    요청한 구간을 다 받지 못하면 예외 — 일부만으로 계산해 기존 결과를 덮어쓰지 않게(받은 조각은 저장돼 다음에 이어 받음)."""
    assert days <= LIST_RETAIN[ty], f"{ty} 목록은 {LIST_RETAIN[ty]}일까지만 보관"
    today = datetime.date.today()
    want = today - datetime.timedelta(days=days)
    ymd = lambda d: d.strftime("%Y%m%d")
    day = lambda s: datetime.datetime.strptime(s, "%Y%m%d").date()
    with _list_lock:
        store = _list_store()
        s = store.setdefault(ty, {"from": "", "to": "", "rows": {}})
        before = (s["from"], s["to"], len(s["rows"]))

        def grab(a, b):
            for cls in ("Y", "K"):
                for r in fetch_range(ymd(a), ymd(b), cls, ty, strict=True):
                    s["rows"][r["rcept_no"]] = {k: r.get(k, "") for k in LIST_FIELDS}

        if not s["to"]:
            s["from"], s["to"] = ymd(want), ymd(want - _DAY)
        try:
            b = day(s["from"]) - _DAY
            while want <= b:                      # 앞쪽(과거)으로 모자란 구간
                a = max(want, b - datetime.timedelta(days=84))
                grab(a, b)
                s["from"] = ymd(a)
                _list_save(store)
                b = a - _DAY
            a = max(day(s["from"]), day(s["to"]) - datetime.timedelta(days=RECHECK_DAYS))
            while a <= today:                     # 뒤쪽(최근) 새 구간 + 끝 며칠 재확인
                e = min(today, a + datetime.timedelta(days=84))
                grab(a, e)
                s["to"] = ymd(e)
                a = e + _DAY
        except Exception as ex:
            print(f"  [!] DART 목록({ty}) 조회 실패 — 받은 구간 {s['from']}~{s['to']}: {type(ex).__name__}")
        cut = ymd(today - datetime.timedelta(days=LIST_RETAIN[ty]))
        s["rows"] = {k: r for k, r in s["rows"].items() if r["rcept_dt"] >= cut}
        s["from"] = max(s["from"], cut)
        if (s["from"], s["to"], len(s["rows"])) != before:
            _list_save(store)
        if s["from"] > ymd(want) or s["to"] < ymd(today):
            raise RuntimeError(f"DART 목록({ty}) {s['from']}~{s['to']} — 요청 {ymd(want)}~{ymd(today)} 를 다 못 받음")
        return [r for r in s["rows"].values() if r["rcept_dt"] >= ymd(want)]


def has_body(report_nm):
    """[첨부정정]은 첨부만 고친 공시 — 본문이 없어 doc_text 가 늘 실패하고, 읽을 새 내용도 없다"""
    return "[첨부정정]" not in (report_nm or "")

def doc_text(rcept_no):
    url = f"https://opendart.fss.or.kr/api/document.xml?crtfc_key={KEY}&rcept_no={rcept_no}"
    with urllib.request.urlopen(url, timeout=30) as r:
        raw = r.read()
    zf = zipfile.ZipFile(io.BytesIO(raw))
    name = max(zf.namelist(), key=lambda n: zf.getinfo(n).file_size)
    data = zf.read(name)
    for enc in ("utf-8", "euc-kr", "cp949"):
        try: txt = data.decode(enc); break
        except Exception: txt = data.decode("utf-8", "replace")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", txt))

CORP_PATH = os.path.join(CACHE_DIR, "corp_codes.json")
_corp = None
def corp_code(stock):
    """종목코드 → DART 고유번호 (corpCode.xml, 7일 캐시)"""
    global _corp
    if _corp is None and os.path.exists(CORP_PATH):
        try:
            j = json.load(open(CORP_PATH, encoding="utf-8"))
            if j.get("_ts", "") >= (datetime.date.today() - datetime.timedelta(days=7)).strftime("%Y%m%d"):
                _corp = j
        except Exception:
            pass
    if _corp is None:
        try:
            with urllib.request.urlopen(f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={KEY}", timeout=60) as r:
                zf = zipfile.ZipFile(io.BytesIO(r.read()))
        except Exception:
            if not os.path.exists(CORP_PATH):
                raise   # 표가 없음 — '고유번호 없음'("")과 구분해 호출부가 실패로 다루게
            _corp = json.load(open(CORP_PATH, encoding="utf-8"))
            return _corp.get(stock, "")
        xml = zf.read(zf.namelist()[0]).decode("utf-8")
        _corp = {"_ts": datetime.date.today().strftime("%Y%m%d")}
        for m in re.finditer(r"<list>(.*?)</list>", xml, re.S):
            sc = re.search(r"<stock_code>\s*(\S+?)\s*</stock_code>", m.group(1))
            cc = re.search(r"<corp_code>\s*(\d+)\s*</corp_code>", m.group(1))
            if sc and cc and re.fullmatch(r"[0-9A-Z]{6}", sc.group(1)):   # 2025~ 신규 상장은 영문 섞인 코드(0009K0)
                _corp[sc.group(1)] = cc.group(1)
        save_json(CORP_PATH, _corp)
    return _corp.get(stock, "")

