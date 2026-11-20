# -*- coding: utf-8 -*-
"""KRX 시장조치 — 저장소의 KIND 당일공시(유가·코스닥) 중 '시장본부/거래소 발신' 행
- 사이드카, 서킷브레이커, 매매거래정지/재개, 투자경고·위험·주의, 단기과열, 공매도과열 등 (파생 가격제한폭은 limits)
- data/out/krx_actions.json 저장 → 웹 상단 '시장조치' 리스트 + monitor 토스트
사용:
  python -m derive.actions            # 오늘 시장조치 → data/out/krx_actions.json
  python -m derive.actions 2026-08-04 # 특정일자
"""
import os, sys, re, datetime
from core.paths import save_json, OUT_DIR
from core import store, universe
from core.paths import KIND_VIEW

ACTIONS_PATH = os.path.join(OUT_DIR, "krx_actions.json")

def _classify(t):
    low = (t or "").lower().replace(" ", "")   # "Side car"/"사이드카"/"Sidecar" 표기 변형 흡수
    if "사이드카" in t or "sidecar" in low or "효력정지" in t: return "사이드카"
    if "서킷브레이커" in t or "일시중단" in t or "매매거래중단" in low or "circuitbreaker" in low: return "서킷브레이커"
    if "거래재개" in t: return "거래재개"
    if "매매거래정지" in t or "거래정지" in t: return "거래정지"
    if "투자위험" in t: return "투자위험"
    if "투자경고" in t: return "투자경고"
    if "투자주의" in t: return "투자주의"
    if "단기과열" in t: return "단기과열"
    if "공매도" in t and "과열" in t: return "공매도과열"
    if "기타시장안내" in t: return "시장안내"
    return "기타조치"

def _action(t):
    for k in ("해제", "발동", "지정", "재개", "정지", "연장"):
        if k in t:
            return k
    return ""

DUR_MIN = {"사이드카": 5, "서킷브레이커": 20}   # 발동 후 자동 해제까지(분) — 선물 가격제한폭 확대예정처럼 표시
def _until(kind, action, tm):
    """발동시각 + 지속시간 → 해제예정 시각(HH:MM). 자동해제 없는 종류/해제공시면 빈 문자열."""
    if action != "발동" or kind not in DUR_MIN or not tm:
        return ""
    try:
        h, m = map(int, tm.split(":"))
        t = h * 60 + m + DUR_MIN[kind]
        return f"{t // 60 % 24:02d}:{t % 60:02d}"
    except Exception:
        return ""

# 시장 전체 조치(특정 종목 아님) — 감시대상 필터와 무관하게 항상 유지·알림
_MARKET_WIDE = ("사이드카", "서킷브레이커")
SEVERITY = {"사이드카": "danger", "서킷브레이커": "danger", "거래정지": "danger", "거래재개": "ok",
            "투자경고": "warn", "투자위험": "warn", "투자주의": "warn", "단기과열": "warn", "공매도과열": "warn"}

def collect(sel_date=None):
    d = sel_date or datetime.date.today().strftime("%Y-%m-%d")
    codes = {re.sub(r"\s+", "", n): c for n, (c, _) in universe.watch_names().items()}   # 감시대상 종목명(공백 제거) → 코드
    events = []
    for k in ("kind_action", "kind_halt"):
        for no, r in store.load(k).items():
            if r["rcept_dt"] != d.replace("-", "") or not ("시장본부" in r["submitter"] or "거래소" in r["submitter"]):
                continue   # 시장본부/거래소 발신만 = 시장조치 (개별사 공시 제외)
            corp, title, tm = r["corp"], r["title"], r["time"]
            kind = _classify(title)
            if kind == "시장안내":
                continue   # 기타시장안내(NXT 등 루틴 안내)는 제외
            # 코스피200·코스닥150 종목만 — 사이드카·서킷브레이커(시장 전체 조치)는 예외로 항상 유지
            if kind not in _MARKET_WIDE and (not corp or re.sub(r"\s+", "", corp) not in codes):
                continue
            action = _action(title)
            until = _until(kind, action, tm)
            events.append({
                "time": tm, "market": r["market"], "kind": kind,
                "direction": "매수" if "매수" in title else ("매도" if "매도" in title else ""),
                "action": action, "until": until,
                "corp": corp, "code": codes.get(re.sub(r"\s+", "", corp), ""),
                "severity": SEVERITY.get(kind, "info"), "critical": kind in _MARKET_WIDE,
                "active_from": tm if until else "", "active_to": until,
                "title": (f"{corp} {title}" if corp else title), "submitter": r["submitter"],
                "rcept_no": no, "url": KIND_VIEW.format(no),
            })
    events.sort(key=lambda e: (e["time"], e["rcept_no"]), reverse=True)
    return events

def build(sel_date=None):
    evs = collect(sel_date)
    save_json(ACTIONS_PATH, {
        "date": sel_date or datetime.date.today().strftime("%Y-%m-%d"),
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(evs), "events": evs,
    })
    return evs

def main(sel_date=None):
    evs = build(sel_date)
    print(f"krx_actions.json 저장: {len(evs)}건")
    for e in evs:
        print(f"  {e['time']} {e['market']} [{e['kind']}] {e['action']} {e['title'][:50]}")

if __name__ == "__main__":
    sel = sys.argv[1] if len(sys.argv) > 1 and re.match(r"\d{4}-\d{2}-\d{2}", sys.argv[1]) else None
    main(sel)
