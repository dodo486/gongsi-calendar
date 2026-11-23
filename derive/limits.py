# -*- coding: utf-8 -*-
"""
선물 상하한가(가격제한폭 단계 도달) — 저장소의 KIND 파생상품시장 '가격제한폭 확대요건 도달' 공시
- 지수선물(코스피200선물·KRX300선물 등) + 주식선물(코스피200/코스닥150 편입 종목) 로 분류·필터
- data/out/limits.json 저장 → 웹 '상하한가' 탭이 읽음
- 실시간: monitor.py 가 폴링하며 신규 접수번호(acptNo)에 윈도우 토스트 알림
사용:
  python -m derive.limits            # 오늘 선물 가격제한폭 도달 공시 → data/out/limits.json
  python -m derive.limits 2026-05-06 # 특정일자
"""
import sys, re, datetime
from core import store, universe
from core.paths import KIND_VIEW, save_json, stamp, LIMITS_PATH
from core import market as md_feed
from core.krx_cal import iso, ymd


def parse(rows, watch_only=True):
    """가격제한폭 도달 공시 행 → 이벤트 리스트 (시간 내림차순)"""
    name2 = universe.watch_names()   # 표시 보강용(필터엔 지수범례 사용)
    events = []
    for no, r in rows:
        title, markets = r["title"], r["indices"]   # 지수 범례(KOSPI200/KOSDAQ150/KRX300…)
        stage = int(re.search(r"(\d)단계", title).group(1)) if re.search(r"(\d)단계", title) else 0
        direction = "상승" if "상승" in title else ("하락" if "하락" in title else "")
        if r["name"]:                                  # 주식선물 (개별종목)
            name = r["name"]
            in_k200 = "KOSPI200" in markets
            in_kq150 = "KOSDAQ150" in markets
            if watch_only and not (in_k200 or in_kq150):
                continue                               # 관찰대상(코스피200/코스닥150) 외 제외
            market = "KOSPI200" if in_k200 else ("KOSDAQ150" if in_kq150 else "")
            code = name2.get(name, ("", ""))[0]
            kind = "주식선물"
        else:                                          # 지수선물 (회사칸 비어있음)
            name = re.sub(r"\s*\d단계.*$", "", title).strip()   # "코스피200선물 3단계…" → "코스피200선물"
            market, code, kind = "지수", "", "지수선물"
        events.append({
            "time": r["time"], "kind": kind, "name": name, "code": code, "market": market,
            "stage": stage, "direction": direction, "title": title,
            "rcept_no": no, "url": KIND_VIEW.format(no), "expand_time": r.get("expand_time", ""),
            "markets": [x for x in markets if x in ("KOSPI200", "KOSDAQ150", "KRX300", "KTOP30", "V100")],
        })
    events.sort(key=lambda e: (e["time"], e["rcept_no"]), reverse=True)   # 최신 먼저, 동시간이면 접수번호 큰 순
    return events

def _index_code(name):
    """지수선물 종목명 → 시세 허브 지수 코드(universe.INDICES 의 quote_code). 없으면 ''"""
    n = (name or "").replace(" ", "").upper()
    return next((ix["quote_code"] for ix in universe.INDICES
                 if ix.get("quote_code") and (ix["name"].upper() in n or ix["key"].upper() in n)), "")

def _attach_rates(events):
    """가격제한폭 이벤트에 기초자산 현재 등락률(부호 %) 부착 — 시세 허브 최신값.
    주식선물 → 기초주식 등락률 / 지수선물 → 해당 지수(코스피200·코스닥150) 등락률. KRX300선물 등 그 밖 지수는 없음."""
    live = md_feed.get("상하한가", {
        md_feed.KRX_STOCK: [e["code"] for e in events if e["kind"] == "주식선물" and e["code"]],
        md_feed.KRX_INDEX: [_index_code(e["name"]) for e in events if e["kind"] == "지수선물" and _index_code(e["name"])],
    })
    for e in events:
        q = live[md_feed.KRX_STOCK].get(e["code"]) if e["kind"] == "주식선물" else live[md_feed.KRX_INDEX].get(_index_code(e["name"]))
        if q and q.get("rate") is not None:
            e["rate"] = q["rate"]

def collect(sel_date=None, watch_only=True):
    """저장소의 오늘 가격제한폭 도달 공시(본문 확대 예정시각 포함) + 기초자산 실시간 등락률 + '지금 걸림' 구간"""
    day = ymd(sel_date or datetime.date.today())
    events = parse(((no, r) for no, r in store.load("kind_limit").items() if r["rcept_dt"] == day), watch_only)
    _attach_rates(events)   # 주식선물 → 기초주식 실시간 등락률(매 주기 갱신)
    for e in events:
        e["emphasis"] = e["stage"] >= 3
        e["active_from"], e["active_to"] = _hot_window(e.get("time", ""), e.get("expand_time", ""))
    return events

HOT_NO_EXPAND_MIN = 5   # 확대예정 시각을 아직 못 읽었으면 도달 뒤 5분을 '지금 걸림'으로

def _hot_window(tm, expand):
    """'지금 걸림' 구간 — 도달 시각 ~ 확대예정 시각(없으면 도달 + 5분). 앱 알림·화면 공통."""
    if not re.match(r"\d{1,2}:\d{2}", tm or ""):
        return "", ""
    if expand:
        return tm[:5], expand[:5]
    h, m = map(int, tm[:5].split(":"))
    t = h * 60 + m + HOT_NO_EXPAND_MIN
    return tm[:5], f"{t // 60 % 24:02d}:{t % 60:02d}"


def build(sel=None):
    evs = collect(sel)
    save_json(LIMITS_PATH, {"date": sel or iso(datetime.date.today()),
                            "generated_at": stamp(), "count": len(evs), "events": evs})
    return evs

def main():
    sel = sys.argv[1] if len(sys.argv) > 1 and re.match(r"\d{4}-\d{2}-\d{2}", sys.argv[1]) else None
    evs = build(sel)
    idx = sum(e["kind"] == "지수선물" for e in evs)
    stk = sum(e["kind"] == "주식선물" for e in evs)
    s3 = sum(e["stage"] >= 3 for e in evs)
    exp = sum(bool(e.get("expand_time")) for e in evs)
    print(f"limits.json 저장: {len(evs)}건 "
          f"(지수선물 {idx} · 주식선물 {stk} · 3단계 {s3} · 확대예정시각 {exp})")

if __name__ == "__main__":
    main()
