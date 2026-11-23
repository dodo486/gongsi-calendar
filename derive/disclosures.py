# -*- coding: utf-8 -*-
"""공시 탭 — 저장소의 DART 목록에서 감시종목(코스피200+코스닥150)의 관심 종류 공시 → data/out/disclosures.json
- 웹(index.html) 공시 탭이 이 JSON을 읽어 캘린더에 표시, monitor 가 실시간 갱신·토스트
- 실적 그리드(earnings)도 collect_events 로 '실적' 공시를 고른다
사용:
  python -m derive.disclosures 90   # 과거 90일 백필 (기존 파일과 병합)
"""
import sys
from core import store, universe
from core.krx_cal import iso, ymd, days_ago
from core.paths import DART_VIEW, save_json, load_json, stamp, DISCLOSURES_PATH

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

def stored_rows(days):
    """저장소의 DART 목록(들여오기가 채움) 중 최근 days 일 접수분"""
    since = ymd(days_ago(days))
    lst = store.load("dart_listing")
    return [r for ty in PBLNTF_TYPES for r in lst.get(ty, {}).get("rows", {}).values() if r["rcept_dt"] >= since]

def collect_events(rows, watch="__load__"):
    """DART 목록 행 → 감시대상+관심종류 공시만 캘린더용 이벤트로 (백필·monitor·earnings 공용)"""
    if watch == "__load__":
        watch = universe.stocks()
    events, seen = [], set()
    for r in rows:
        rno = r.get("rcept_no", "")
        title = (r.get("report_nm", "") or "").strip()
        if rno in seen or (watch is not None and r.get("stock_code") not in watch)                 or not any(k in title for k in REPORT_KEYWORDS):
            continue
        seen.add(rno)
        events.append({
            "name": r.get("corp_name", ""),
            "code": r.get("stock_code", ""),
            "market": universe.DART_MARKETS.get(r.get("corp_cls"), "KOSDAQ"),
            "title": title,
            "category": categorize(title),
            "date": iso(r.get("rcept_dt")),
            "rcept_no": rno,
            "url": DART_VIEW + rno,
            "flr": r.get("flr_nm", ""),
        })
    return events

def main(days=7):
    events = [e for e in collect_events(stored_rows(days)) if e["category"] not in CAL_EXCLUDE]

    # 기존 파일과 병합 — 짧은 기간 백필이 기존 이력을 지우지 않게 (rcept_no 기준 중복 제거)
    have = {e["rcept_no"] for e in events}
    events += [e for e in load_json(DISCLOSURES_PATH, {}).get("events", []) if e.get("rcept_no") not in have]
    events.sort(key=lambda e: e.get("date", ""), reverse=True)

    dates = [e["date"] for e in events if e.get("date")]
    payload = {
        "generated_at": stamp(),
        "range": {"bgn": min(dates), "end": max(dates)} if dates else {},
        "count": len(events),
        "events": events,
    }
    save_json(DISCLOSURES_PATH, payload)
    print(f"저장: {DISCLOSURES_PATH} ({len(events)}건)")

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    main(n)
