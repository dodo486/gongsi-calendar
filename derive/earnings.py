# -*- coding: utf-8 -*-
"""
실적 공시 실시간 수집 + 현재 주가 등락률
- fetch.collect_events 로 '실적' 카테고리 공시만 골라 data/out/earnings.json 저장
- 오늘 공시 종목의 현재 등락률은 시세 허브(core.market) 최신값으로 부착
- 웹 결합뷰의 '실적 그리드'(상하한가와 배당 사이)가 이 파일을 읽음
사용:
  python -m derive.earnings            # 최근 30일 실적 공시 + 등락률 → data/out/earnings.json
  python -m derive.earnings 60         # 최근 N일
"""
import bisect, re, sys, datetime
from derive.disclosures import collect_events, stored_rows
from core.paths import save_json, load_json, stamp, EARNINGS_PATH
from core import store, universe
from core.krx_cal import iso, to_date, minutes, kst
from core.fmt import pct, signed_pct
from core import market as md_feed

RETAIN_DAYS = 30
QUOTE_CAP = 80          # 과거 공시일 등락률 조회 종목 상한
TICK_CAP = 30           # 1회 실행당 공시시각+분봉(전후5분) 신규 계산 상한
def _attach_quotes(events):
    """등락률 부착 — 과거 공시일은 '그 날짜'의 일별 변동률(확정, 재조회 안 함),
    오늘 공시는 실시간 등락률(폴러가 매 주기 갱신)"""
    today = iso(datetime.date.today())
    live = md_feed.get("실적", {md_feed.KRX_STOCK: [e["code"] for e in events if e.get("code") and e.get("date") == today]})[md_feed.KRX_STOCK]
    hist, n = {}, 0
    for e in events:
        c, d = e.get("code", ""), e.get("date", "")
        if not c:
            continue
        if d == today:                      # 오늘 공시 → 시세 허브 최신값
            q = live[c]
            e["price"] = q.get("price", "")
            e["rate"] = q.get("rate")
        else:                               # 과거 공시 → 공시일 당일 변동률로 고정
            if e.get("rate_fixed"):
                continue
            if c not in hist:
                hist[c] = md_feed.daily_rate_map(c) if n < QUOTE_CAP else {}
                n += 1
            r = hist[c].get(d)
            if r is not None:
                e["rate"] = r["rate"]
                e["rate_fixed"] = True      # 과거치는 불변 — 다음 갱신 때 스킵
    return events

def kind_disc_time(corp, day, market):
    """저장소의 KIND 당일공시 목록에서 해당 회사의 실적 공시 접수시각(HH:MM) (DART 목록엔 시각 없음)"""
    page = store.load("kind_feed").get(f"{day}|{ {m: k for k, m in universe.KIND_MARKETS.items()}.get(market, 'K2')}", [])
    return next((r["time"] for r in page if r["name"] == corp and "실적" in r["title"] and r["time"]), "")

def _tick_rates(bars, day, hhmm):
    """공시시각 기준 전5분/후5분 등락률(%) — bars = 그날 [(한국시각, 종가)] 오름차순, 15분 이내 분봉만 인정"""
    base = datetime.datetime.combine(to_date(day), datetime.time(*map(int, hhmm.split(":"))))
    times = [t for t, _ in bars]
    def px(dt):
        i = bisect.bisect_right(times, dt) - 1
        return bars[i][1] if i >= 0 and abs((times[i] - dt).total_seconds()) <= 900 else None
    p0 = px(base - datetime.timedelta(minutes=5))
    pt = px(base)
    p5 = px(base + datetime.timedelta(minutes=5))
    pre = pct(pt, p0)
    post = pct(p5, pt)
    return pre, post

def _attach_ticks(events):
    """과거 공시에 공시시각 + 발표 전후 반응 부착 (rcept_no별 영구 보존, _carry_over로 유지)
    - 장중(개장~마감, 개장·마감이 늦는 날은 그만큼) 공시 + 분봉 범위 내 → 전5분/후5분 (tick_kind=min)
    - 장후(15:30~) → 당일 등락률 + 익일 시가갭 (tick_kind=close_gap)
    - 장전(~09:00) → 전일 등락률 + 당일 시가갭 (tick_kind=open_gap)
    - 장중이지만 분봉 범위(약 6거래일) 밖 → 당일·익일 등락률 (tick_kind=day)
    """
    today = iso(datetime.date.today())
    cmap_cache, dmap_cache, n = {}, {}, 0
    for e in events:
        st = e.get("tick_state", "")
        if st and not st.startswith("wait:"):
            continue                       # 확정 상태 — 재계산 없음
        if st == "wait:" + today:
            continue                       # 익일 갭 대기 — 하루 1회만 재시도
        if e.get("date", "") >= today or n >= TICK_CAP:
            continue                       # 오늘 공시 / 상한 도달
        code, day = e.get("code", ""), e["date"]
        n += 1
        t = e.get("disc_time") or kind_disc_time(e.get("name", ""), day, e.get("market", ""))
        if not t:
            e["tick_state"] = "notime"
            continue
        e["disc_time"] = t
        at, (krx_open, krx_close) = minutes(t), md_feed.krx_hours(to_date(day))
        if code not in dmap_cache:
            dmap_cache[code] = md_feed.daily_rate_map(code)
        dmap = dmap_cache[code]
        dates = sorted(dmap)
        if day not in dmap:
            e["tick_state"] = "noday"
            continue
        i = dates.index(day)
        cur, prv = dmap[day], (dmap[dates[i-1]] if i > 0 else None)
        nxt = dmap[dates[i+1]] if i + 1 < len(dates) else None
        if krx_open <= at < krx_close:
            if code not in cmap_cache:
                cmap_cache[code] = sorted((kst(k), v) for k, v in md_feed.minute_closes(code).items())
            bars = [b for b in cmap_cache[code] if b[0].date() == to_date(day)]
            if bars:
                pre, post = _tick_rates(bars, day, t)
                e["pre5"], e["post5"], e["tick_kind"] = pre, post, "min"
            else:   # 분봉 범위 밖 옛 장중 공시 → 당일·익일 등락률
                e["pre5"], e["post5"], e["tick_kind"] = cur["rate"], (nxt["rate"] if nxt else None), "day"
        elif at >= krx_close:   # 장후 → 당일 등락 + 익일 시가갭
            e["pre5"] = cur["rate"]
            e["post5"] = pct(nxt["open"], cur["close"]) if nxt else None
            e["tick_kind"] = "close_gap"
            if nxt is None:          # 익일 미개장(최근 공시) — 내일 다시 계산
                e["tick_state"] = "wait:" + today
                continue
        else:                        # 장전 → 전일 등락 + 당일 시가갭
            e["pre5"] = prv["rate"] if prv else None
            e["post5"] = pct(cur["open"], prv["close"]) if prv else None
            e["tick_kind"] = "open_gap"
        e["tick_state"] = "ok" if (e.get("pre5") is not None or e.get("post5") is not None) else "nodata"
    return events

def _carry_over(evs):
    """재수집 시 이전 파일의 확정값(과거 등락률·공시시각·전후5분)을 rcept_no 기준으로 이어받음"""
    old = {e["rcept_no"]: e for e in load_json(EARNINGS_PATH, {}).get("events", [])}
    keep = ("rate", "rate_fixed", "disc_time", "pre5", "post5", "tick_state", "tick_kind", "price")
    for e in evs:
        o = old.get(e["rcept_no"])
        if o:
            for k in keep:
                if k in o and k not in e:
                    e[k] = o[k]
    return evs

def _dedupe_sort(evs):
    """접수번호 중복 제거 + 같은 날 같은 보고서의 [기재정정]/원본은 최신 접수분만 유지"""
    seen, base_seen, out = set(), set(), []
    for e in sorted(evs, key=lambda x: (x.get("date", ""), x.get("rcept_no", "")), reverse=True):
        if e["rcept_no"] in seen:
            continue
        base = (e.get("code", ""), e.get("date", ""),
                re.sub(r"^(\[[^\]]*\]\s*)+", "", e.get("title", "")))   # 제목 앞 [정정] 태그 제거
        if base in base_seen:
            continue   # 최신(정정)본이 이미 있음 — 원본 스킵
        seen.add(e["rcept_no"]); base_seen.add(base); out.append(e)
    return out

def collect(days=RETAIN_DAYS, watch="__load__"):
    if watch == "__load__":
        watch = universe.stocks()
    evs = [e for e in collect_events(stored_rows(days), watch=watch) if e.get("category") == "실적"]
    return _dedupe_sort(evs)

TICK_LABEL = {   # 공시 시간대별 전/후 지표 이름
    "min": ("발표 전5분", "후5분"),
    "close_gap": ("장후 공시 — 당일 등락", "익일 시가갭"),
    "open_gap": ("장전 공시 — 전일 등락", "당일 시가갭"),
    "day": ("당일 등락", "익일 등락"),
}

def _texts(evs):
    """화면 문구 — 캘린더 제목(발표 전→후 반응이 있으면 그것, 없으면 등락률), 공시시각·전후 반응 한 줄"""
    for e in evs:
        if e.get("pre5") is not None or e.get("post5") is not None:
            e["cal_title"] = f"{e['name']} ({signed_pct(e.get('pre5'))}→{signed_pct(e.get('post5'))})"
        else:
            e["cal_title"] = e["name"] + (f" ({signed_pct(e['rate'])})" if e.get("rate") is not None else "")
        if e.get("pre5") is not None or e.get("post5") is not None or e.get("disc_time"):
            l1, l2 = TICK_LABEL.get(e.get("tick_kind"), TICK_LABEL["min"])
            e["tick_text"] = f"⏱️ 공시 {e.get('disc_time') or '-'} · {l1} {signed_pct(e.get('pre5'))} · {l2} {signed_pct(e.get('post5'))}"
        else:
            e["tick_text"] = ""
    return evs

def main(days=RETAIN_DAYS):
    evs = collect(days)
    _carry_over(evs)     # 이전 파일의 확정값(과거 등락률·공시시각·전후5분) 이어받기
    _attach_quotes(evs)
    _attach_ticks(evs)   # 공시시각(KIND) + 발표 전5분/후5분(네이버 분봉, 최근 6거래일 한정)
    _texts(evs)
    save_json(EARNINGS_PATH, {
        "generated_at": stamp(),
        "count": len(evs), "events": evs,
    })
    got = sum(e.get("rate") is not None for e in evs)
    print(f"earnings.json 저장: {len(evs)}건 (등락률 {got})")

def refresh_quotes():
    """실적 목록 재수집 없이 등락률만 갱신(가벼움) — 폴러가 매 주기 호출"""
    d = load_json(EARNINGS_PATH, None)
    if d is None:
        return False
    _attach_quotes(d.get("events", []))
    _texts(d.get("events", []))
    d["generated_at"] = stamp()
    save_json(EARNINGS_PATH, d)
    return True

if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else RETAIN_DAYS
    main(days)
