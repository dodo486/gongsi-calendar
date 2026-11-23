# -*- coding: utf-8 -*-
"""매매거래정지·거래재개 — 저장소의 KIND 정지/해제 공시(본문: 정지일시·사유·만료일시·해제일시) + DART 결정공시 상장예정일

- watchlist(코스피200+코스닥150) 종목만 남김 (노이즈 컷)
- 재개(해제) 예정일:
    · 만료가 '장종료시까지'(당일정지) → 익영업일 (확정)
    · 만료가 구체적 날짜 → 그 날짜 (확정)
    · 만료가 '변경상장/신주' 등 다중일 → DART 결정공시의 '상장예정일' 당겨옴 (예정)
    · KIND '정지해제' 공시가 이미 나온 종목 → 그 해제일로 확정(예정 딱지 제거)
- data/out/halts.json 저장 → 웹 공시 캘린더가 '🚫정지 시작'·'✅거래재개' 2점 이벤트로 표시
사용:
  python -m derive.halts            # 최근 정지/재개 → data/out/halts.json
"""
import re
from core import store, universe
from core.paths import KIND_VIEW, save_json, stamp, HALTS_PATH
from core.krx_cal import next_bday, iso, ymd, days_ago

SCAN_DAYS = 50                       # 정지/해제 공시 스캔 기간(일)
RELIST_DAYS = 150                    # 재개 예정일을 찾을 결정공시 기간(일)

def collect():
    name2 = universe.watch_names()
    since = ymd(days_ago(SCAN_DAYS))
    halts, releases = {}, {}   # code→halt / code→[해제일]
    for rcept, r in sorted(store.load("kind_halt").items(), key=lambda x: (x[1]["rcept_dt"], x[0]), reverse=True):   # 최신 먼저
        nm, title = r["name"], r["title"]
        if nm not in name2 or r["rcept_dt"] < since:   # watchlist 필터
            continue
        code, mk = name2[nm]
        is_release = ("정지해제" in title) or ("및정지해제" in title) or                      (("해제" in title) and ("정지" not in title.replace("정지해제", "")))
        if is_release:   # 해제 공시 본문의 해제일시(없으면 접수일) — 재개 확정일
            releases.setdefault(code, []).append(r["release"] or iso(r["rcept_dt"]))
            continue
        if code in halts:   # 정지(또는 예고) 공시 — 종목별 최신 1건만
            continue
        halts[code] = {"name": nm, "code": code, "market": mk, "reason": r["reason"], "reason_full": r["reason_full"],
                       "halt_date": r["halt_date"], "expire": r["expire"], "rcept_no": rcept, "url": KIND_VIEW.format(rcept),
                       "pre": "예고" in title}
    relist_since = ymd(days_ago(RELIST_DAYS))
    relist = {}   # 종목 → 분할·병합·감자·회사분할 결정공시의 상장예정일 (최신 공시)
    for k in ("split", "reduction"):
        for no, d in store.load(k).items():
            if d.get("listing_date") and d["rcept_dt"] >= relist_since and (d["code"] not in relist or no > relist[d["code"]][0]):
                relist[d["code"]] = (no, d["listing_date"])
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
            if code in relist:
                resume, confirmed = relist[code][1], False
        h["resume_date"] = resume
        h["resume_confirmed"] = confirmed
        # 관련성 필터: 재개가 과거로 3일 넘게 지난 건 제외(이미 풀림)
        if resume and resume < iso(days_ago(3)):
            continue
        h["marks"] = _marks(h)
        out.append(h)

    out.sort(key=lambda x: (x.get("halt_date") or "9999", x["name"]))
    return out

def _marks(e):
    """화면 캘린더 표시 — 정지 시작·거래재개(확정/예정) 두 점"""
    out = []
    if e.get("halt_date"):
        out.append({"type": "", "date": e["halt_date"], "title": f"🚫 {e['name']} 정지·{e.get('reason') or ''}", "cls": "halt-stop"})
    if e.get("resume_date"):
        out.append({"type": "", "date": e["resume_date"], "title": f"✅ {e['name']} 재개{'' if e['resume_confirmed'] else '(예정)'}",
                    "cls": "halt-resume" if e["resume_confirmed"] else "halt-resume halt-tent"})
    return out

def main():
    evs = collect()
    payload = {
        "generated_at": stamp(),
        "count": len(evs), "events": evs,
    }
    save_json(HALTS_PATH, payload)
    conf = sum(e["resume_confirmed"] for e in evs)
    print(f"halts.json 저장: {len(evs)}건 (재개확정 {conf} · 예정 {len(evs)-conf})")
    for e in evs:
        rr = e["resume_date"] or "미정"
        tag = "" if e["resume_confirmed"] else "(예정)"
        print(f"  {e['name']}({e['code']},{e['market']}) 정지 {e['halt_date']} · {e['reason']} → 재개 {rr}{tag}")

if __name__ == "__main__":
    main()
