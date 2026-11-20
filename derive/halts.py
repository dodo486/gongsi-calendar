# -*- coding: utf-8 -*-
"""매매거래정지·거래재개 수집기 — KRX KIND(정지/해제 공시) + DART(재개 예정일 보강)

- KIND todaydisclosure(marketType 1=유가, 2=코스닥)에서 '매매거래정지' 제목 공시 스캔
- watchlist(코스피200+코스닥150) 종목만 남김 (노이즈 컷)
- 각 정지 공시 본문에서 정지일시·정지사유·만료일시 파싱
- 재개(해제) 예정일:
    · 만료가 '장종료시까지'(당일정지) → 익영업일 (확정)
    · 만료가 구체적 날짜 → 그 날짜 (확정)
    · 만료가 '변경상장/신주' 등 다중일 → DART 결정공시의 '상장예정일' 당겨옴 (예정)
    · KIND '정지해제' 공시가 이미 나온 종목 → 그 해제일로 확정(예정 딱지 제거)
- data/halts.json 저장 → 웹 공시 캘린더가 '🚫정지 시작'·'✅거래재개' 2점 이벤트로 표시
사용:
  python -m derive.halts            # 최근 정지/재개 → data/halts.json
"""
import json, os, re, datetime
from core.paths import DATA_DIR, save_json
from ingest.sources.dart import KEY, api, doc_text, corp_code
from core.krx_cal import next_bday, norm_date, is_business_day
from ingest.sources import kind as kind_client

SCAN_DAYS = 50                       # 정지/해제 공시 스캔 기간(일)
FEED_PATH = os.path.join(DATA_DIR, "halt_feed_cache.json")  # "날짜|시장"→정지·해제 공시 행 — 지난 날짜 목록은 안 바뀜
FEED_RECHECK_DAYS = 2                                        # 오늘·어제 목록만 늦은 공시 대비 매번 다시
BODY_PATH = os.path.join(DATA_DIR, "halt_body_cache.json")  # rcept→본문 값(정지: halt_date·reason·expire / 해제: release)
DART_PATH = os.path.join(DATA_DIR, "halt_dart_cache.json")  # stock→{resume,halt} DART 보강 캐시

def _short_reason(reason, extra):
    """정지사유(+기타사유) → 짧은 라벨."""
    txt = (extra or "") + " " + (reason or "")
    for kw in ("주식분할", "액면분할", "주식병합", "액면병합", "무상증자", "유상증자", "감자",
               "회사분할", "합병", "영업양수도", "주식교환", "조회공시", "불성실공시",
               "상장적격성", "실질심사", "정리매매", "투자자 보호", "투자유의"):
        if kw.replace(" ", "") in txt.replace(" ", ""):
            return kw
    r = (reason or "").strip()
    return (r[:20] + "…") if len(r) > 20 else (r or "매매거래정지")

def _parse_body(rcept, cache):
    """정지 공시 본문 파싱(캐시). return {halt_date, reason, reason_full, expire}."""
    if rcept in cache:
        return cache[rcept]
    t = kind_client.body_text(rcept)
    if not t:   # 받기 실패는 캐시에 남기지 않음 — 다음 회차 재시도
        return {"halt_date": "", "reason": "", "reason_full": "", "expire": ""}
    tc = t.replace(" ", "")
    hd = re.search(r"정지일시(\d{4}-\d{2}-\d{2})", tc)
    exp = re.search(r"만료일시([^0-9]*?(?:\d{4}-\d{2}-\d{2})?[가-힣]*)", tc)
    sy = re.search(r"정지사유(.*?)3\.정지기간", tc)
    et = re.search(r"기타사유[:：]?\s*([가-힣A-Za-z0-9]+)", tc)
    reason_full = (sy.group(1) if sy else "").strip()
    out = {
        "halt_date": hd.group(1) if hd else "",
        "reason": _short_reason(reason_full, et.group(1) if et else ""),
        "reason_full": reason_full[:60],
        "expire": (exp.group(1) if exp else "").strip(),
    }
    cache[rcept] = out
    return out

# ── DART 보강 (결정공시 상장예정일) ──
_DATE = r"(\d{4}\D+\d{1,2}\D+\d{1,2})"

def dart_resume(stock, halt_date, dcache):
    """분할/병합/감자/회사분할 결정공시에서 '상장예정일'(=재개 예정일) 당겨오기. 캐시."""
    key = f"{stock}|{halt_date}"
    if key in dcache:
        return dcache[key]
    res = ""
    try:
        cc = corp_code(stock)
    except Exception:
        return ""   # 고유번호 표를 못 받음 — 캐시에 남기지 않고 다음 회차 재시도
    if cc:
        try:
            end = datetime.date.today().strftime("%Y%m%d")
            bgn = (datetime.date.today() - datetime.timedelta(days=150)).strftime("%Y%m%d")
            d = api({"crtfc_key": KEY, "corp_code": cc, "bgn_de": bgn, "end_de": end,
                     "page_no": 1, "page_count": 100})
            if d.get("status") not in ("000", "013"):   # 013 = 조회 결과 없음(정상)
                return ""
            for r in d.get("list", []):
                nm = r.get("report_nm", "")
                if not any(k in nm for k in ("분할결정", "병합", "감자결정", "회사분할")):
                    continue
                if "정지" in nm:          # KRX 정지공시 자체는 제외(결정 원문만)
                    continue
                t = doc_text(r["rcept_no"]).replace(" ", "")
                m = re.search(r"상장예정일" + _DATE, t)
                if m:
                    res = norm_date(m.group(1))
                    break
        except Exception:
            return ""   # 받기 실패는 캐시에 남기지 않음 — 다음 회차 재시도
    dcache[key] = res
    return res

# ── 수집 ──
HALT_KW = "매매거래정지"
def _halt_rows(html):
    """KIND 당일공시 목록 HTML → 정지·해제 공시 행 [접수번호, 제목, 회사명]"""
    out = []
    for row in re.split(r"</tr>", html):
        if HALT_KW not in row:
            continue
        ma = re.search(r"openDisclsViewer\('(\d+)'", row)
        mt = re.search(r"title='([^']*매매거래정지[^']*)'", row)
        mc = re.search(r"companysummary_open\([^)]*\);\s*return false;\"\s*title='([^']+)'", row)
        if ma and mt and mc:
            out.append([ma.group(1), mt.group(1).strip(), mc.group(1).strip()])
    return out

def collect():
    name2 = kind_client.name2code()
    fcache = json.load(open(FEED_PATH, encoding="utf-8")) if os.path.exists(FEED_PATH) else {}
    bcache = json.load(open(BODY_PATH, encoding="utf-8")) if os.path.exists(BODY_PATH) else {}
    dcache = json.load(open(DART_PATH, encoding="utf-8")) if os.path.exists(DART_PATH) else {}
    today = datetime.date.today()
    recheck_from = (today - datetime.timedelta(days=FEED_RECHECK_DAYS - 1)).strftime("%Y-%m-%d")
    oldest = (today - datetime.timedelta(days=SCAN_DAYS)).strftime("%Y-%m-%d")

    halts, releases, seen = {}, {}, set()   # code→halt / code→[해제일]
    for dd in range(SCAN_DAYS + 1):
        d = (today - datetime.timedelta(days=dd)).strftime("%Y-%m-%d")
        for market in ("1", "2"):
            key = f"{d}|{market}"
            rows = fcache.get(key)
            if rows is None or d >= recheck_from:
                try:
                    html = kind_client.feed(d, market)
                except Exception:
                    continue
                rows = _halt_rows(html)
                # 공시가 하나도 없는 영업일 목록은 오류 페이지일 수 있어 저장하지 않음(다음 회차 재조회)
                if "openDisclsViewer" in html or not is_business_day(datetime.date.fromisoformat(d)):
                    fcache[key] = rows
            for rcept, title, nm in rows:
                if nm not in name2:                       # watchlist 필터
                    continue
                if rcept in seen:
                    continue
                seen.add(rcept)
                code, mk = name2[nm]
                is_release = ("정지해제" in title) or ("및정지해제" in title) or \
                             (("해제" in title) and ("정지" not in title.replace("정지해제", "")))
                if is_release:
                    # 해제 공시 본문의 해제일시(없으면 접수일) — 재개 확정일. 받은 본문은 캐시(받기 실패는 다음 회차 재시도)
                    if rcept in bcache:
                        rdate = bcache[rcept]["release"]
                    else:
                        t = kind_client.body_text(rcept).replace(" ", "")
                        m = re.search(r"해제일시(\d{4}-\d{2}-\d{2})", t)
                        rdate = m.group(1) if m else d
                        if t:
                            bcache[rcept] = {"release": rdate}
                    releases.setdefault(code, []).append(rdate)
                    continue
                # 정지(또는 예고) 공시 — 종목별 최신 1건만
                if code in halts:
                    continue
                b = _parse_body(rcept, bcache)
                halts[code] = {
                    "corp": nm, "code": code, "market": mk,
                    "reason": b["reason"], "reason_full": b["reason_full"],
                    "halt_date": b["halt_date"], "expire": b["expire"],
                    "rcept_no": rcept, "url": kind_client.VIEWER.format(rcept),
                    "pre": "예고" in title,
                }

    out = []
    for code, h in halts.items():
        exp = h.pop("expire", "")
        resume, confirmed = "", False
        # 1) KIND 해제공시가 이미 나옴 → 확정
        rel = [x for x in releases.get(code, []) if not h["halt_date"] or x >= h["halt_date"]]   # 이번 정지 이후 해제만
        if rel:
            resume, confirmed = max(rel), True
        # 2) 만료가 구체 날짜
        elif re.search(r"\d{4}-\d{2}-\d{2}", exp):
            resume = re.search(r"(\d{4}-\d{2}-\d{2})", exp).group(1); confirmed = True
        # 3) 당일정지(장종료시까지/장개시전 등) → 익영업일
        elif h["halt_date"] and re.search(r"장종료|당일|장개시|장중", exp):
            resume = next_bday(h["halt_date"]); confirmed = True
        # 4) 다중일(변경상장/신주 등) → DART 상장예정일(예정)
        elif re.search(r"변경상장|신주|상장", exp) or not exp:
            r = dart_resume(code, h["halt_date"], dcache)
            if r:
                resume, confirmed = r, False
        h["resume_date"] = resume
        h["resume_confirmed"] = confirmed
        # 관련성 필터: 재개가 과거로 3일 넘게 지난 건 제외(이미 풀림)
        if resume and resume < (datetime.date.today() - datetime.timedelta(days=3)).strftime("%Y-%m-%d"):
            continue
        h["marks"] = _marks(h)
        out.append(h)

    save_json(FEED_PATH, {k: v for k, v in fcache.items() if k[:10] >= oldest})
    save_json(BODY_PATH, bcache)
    save_json(DART_PATH, dcache)
    out.sort(key=lambda x: (x.get("halt_date") or "9999", x["corp"]))
    return out

def _marks(e):
    """화면 캘린더 표시 — 정지 시작·거래재개(확정/예정) 두 점"""
    out = []
    if e.get("halt_date"):
        out.append({"type": "", "date": e["halt_date"], "title": f"🚫 {e['corp']} 정지·{e.get('reason') or ''}", "cls": "halt-stop"})
    if e.get("resume_date"):
        out.append({"type": "", "date": e["resume_date"], "title": f"✅ {e['corp']} 재개{'' if e['resume_confirmed'] else '(예정)'}",
                    "cls": "halt-resume" if e["resume_confirmed"] else "halt-resume halt-tent"})
    return out

def main():
    evs = collect()
    payload = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(evs), "events": evs,
    }
    save_json(os.path.join(DATA_DIR, "halts.json"), payload)
    conf = sum(e["resume_confirmed"] for e in evs)
    print(f"halts.json 저장: {len(evs)}건 (재개확정 {conf} · 예정 {len(evs)-conf})")
    for e in evs:
        rr = e["resume_date"] or "미정"
        tag = "" if e["resume_confirmed"] else "(예정)"
        print(f"  {e['corp']}({e['code']},{e['market']}) 정지 {e['halt_date']} · {e['reason']} → 재개 {rr}{tag}")

if __name__ == "__main__":
    main()
