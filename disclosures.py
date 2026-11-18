# -*- coding: utf-8 -*-
"""공시 탭 수집 — 감시종목(코스피200+코스닥150)의 관심 종류 공시 → data/disclosures.json
- 웹(index.html) 공시 탭이 이 JSON을 읽어 캘린더에 표시, monitor 가 실시간 갱신·토스트
- 실적 그리드(earnings)도 collect_events 로 '실적' 공시를 고른다
사용:
  python disclosures.py 90   # 과거 90일 백필 (기존 파일과 병합)
"""
import json, os, sys, datetime
from base import DATA_DIR, save_json, load_watchlist
from fetch import fetch_range, listing
from net import TLS_MODE

# 관심 공시 종류: (제목 키워드, 표시 카테고리). 순서 = 매칭 우선순위.
CATEGORIES = [
    ("유상증자", "유상증자"),
    ("무상증자", "무상증자"),
    ("자기주식", "자사주"),
    ("전환사채", "메자닌"), ("신주인수권부사채", "메자닌"), ("교환사채", "메자닌"),
    ("감자", "감자"),
    ("합병", "합병/분할"), ("분할", "합병/분할"),
    ("최대주주변경", "최대주주변경"),   # '최대주주등소유주식변동신고서'(단순 지분변동) 제외 위해 '변경'까지 명시
    ("결산실적공시예고", "실적예고"),   # 실적 '발표 예정일' 안내 — 실제 실적(아래)보다 먼저 매칭
    ("영업(잠정)실적", "실적"), ("실적", "실적"), ("손익구조", "실적"),
    ("배당", "배당"),
]
REPORT_KEYWORDS = [k for k, _ in CATEGORIES]
# DART 공시유형: B=주요사항보고(증자·자사주·CB/BW·감자·합병분할), I=거래소공시(배당·실적·최대주주변경)
PBLNTF_TYPES = ["B", "I"]
# 공시 캘린더에서 제외할 카테고리 — 배당·실적·실적예고는 각자 별도 그리드 전담
CAL_EXCLUDE = {"배당", "실적", "실적예고"}

def categorize(title):
    for kw, label in CATEGORIES:
        if kw in (title or ""):
            return label
    return "기타"

def live_rows(bgn, end):
    """monitor 실시간 폴링용 — 좁은 구간(어제~오늘)을 저장 없이 바로 조회"""
    return [r for cls in ("Y", "K") for ty in PBLNTF_TYPES for r in fetch_range(bgn, end, cls, ty)]

def stored_rows(days):
    """백필·실적 재수집용 — 공용 목록 저장본(받은 구간은 다시 안 받음)"""
    return [r for ty in PBLNTF_TYPES for r in listing(ty, days)]

def collect_events(rows, watch="__load__"):
    """DART 목록 행 → 감시대상+관심종류 공시만 캘린더용 이벤트로 (백필·monitor·earnings 공용)"""
    if watch == "__load__":
        watch = load_watchlist()
    events, seen = [], set()
    for r in rows:
        rno = r.get("rcept_no", "")
        title = (r.get("report_nm", "") or "").strip()
        if rno in seen or (watch is not None and r.get("stock_code") not in watch)                 or not any(k in title for k in REPORT_KEYWORDS):
            continue
        seen.add(rno)
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
    print(f"TLS={TLS_MODE} | 최근 {days}일")
    events = [e for e in collect_events(stored_rows(days)) if e["category"] not in CAL_EXCLUDE]

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
