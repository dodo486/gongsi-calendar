# -*- coding: utf-8 -*-
"""
선물 상하한가(가격제한폭 단계 도달) 수집기 — KRX KIND 파생상품시장 공시
- 소스: https://kind.krx.co.kr  (※ data.krx.co.kr 은 이 PC에서 차단되지만 kind 는 접근 가능)
- todaydisclosure(marketType=3=파생상품시장)에서 '가격제한폭 확대요건 도달' 공시만 수집
- 지수선물(코스피200선물·KRX300선물 등) + 주식선물(코스피200/코스닥150 편입 종목) 로 분류·필터
- data/limits.json 저장 → 웹 '상하한가' 탭이 읽음
- 실시간: monitor.py 가 폴링하며 신규 접수번호(acptNo)에 윈도우 토스트 알림
사용:
  python kind_limits.py            # 오늘 선물 가격제한폭 도달 공시 → data/limits.json
  python kind_limits.py 2026-05-06 # 특정일자
"""
import json, os, sys, re, datetime
from net import TLS_MODE
from base import DATA_DIR, save_json
import kind_client
import md_feed

LIMIT_KW = "가격제한폭 확대요건 도달"   # 이 문구가 든 파생 공시만 대상

def parse(html, watch_only=True):
    """가격제한폭 도달 공시 파싱 → 이벤트 리스트 (시간 내림차순)"""
    name2 = kind_client.name2code()   # 표시 보강용(필터엔 지수범례 사용)
    events = []
    for row in re.split(r"</tr>", html):
        if LIMIT_KW not in row:
            continue
        m_acpt = re.search(r"openDisclsViewer\('(\d+)'", row)
        m_title = re.search(r"openDisclsViewer\([^)]*\)\"\s*title='([^']+)'", row)
        m_time = re.search(r'class="first txc">\s*([0-9]{1,2}:[0-9]{2})', row)
        m_comp = re.search(r"companysummary_open\([^)]*\);\s*return false;\"\s*title='([^']+)'", row)
        if not (m_acpt and m_title):
            continue
        title = m_title.group(1).strip()
        markets = re.findall(r"alt='([^']+)'", row)   # 지수 범례(KOSPI200/KOSDAQ150/KRX300…)
        stage = int(re.search(r"(\d)단계", title).group(1)) if re.search(r"(\d)단계", title) else 0
        direction = "상승" if "상승" in title else ("하락" if "하락" in title else "")

        if m_comp:                                     # 주식선물 (개별종목)
            name = m_comp.group(1).strip()
            in_k200 = "KOSPI200" in markets
            in_kq150 = "KOSDAQ150" in markets
            if watch_only and not (in_k200 or in_kq150):
                continue                               # 관찰대상(코스피200/코스닥150) 외 제외
            market = "KOSPI200" if in_k200 else ("KOSDAQ150" if in_kq150 else "")
            code = name2.get(name, ("", ""))[0]
            kind = "주식선물"
        else:                                          # 지수선물 (회사칸 비어있음)
            name = re.sub(r"\s*\d단계.*$", "", title).strip()   # "코스피200선물 3단계…" → "코스피200선물"
            market = "지수"
            code = ""
            kind = "지수선물"

        events.append({
            "time": m_time.group(1) if m_time else "",
            "kind": kind, "name": name, "code": code, "market": market,
            "stage": stage, "direction": direction, "title": title,
            "rcept_no": m_acpt.group(1), "url": kind_client.VIEWER.format(m_acpt.group(1)),
            "markets": [x for x in markets if x in ("KOSPI200", "KOSDAQ150", "KRX300", "KTOP30", "V100")],
        })
    # 시간 내림차순(최신 먼저); 동시간이면 접수번호 큰 순
    events.sort(key=lambda e: (e["time"], e["rcept_no"]), reverse=True)
    return events

# --- 본문 '확대 예정시각' 파싱 (KIND 문서서버 3단계 로더 통과) ---
EXP_CACHE_PATH = os.path.join(DATA_DIR, "limits_exp_cache.json")  # rcept_no→확대예정시각 캐시
ENRICH_CAP = 40   # 한 번의 collect에서 새로 본문 조회할 최대 건수(나머지는 다음 주기에)

def fetch_expand_time(rcept_no):
    """공시 본문에서 '확대 예정시각'(HH:MM:SS) 추출."""
    try:
        clean = kind_client.body_text(rcept_no)
        e = re.search(r"확대\s*예정시각\s*([0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)", clean)
        return e.group(1) if e else ""
    except Exception:
        return ""

def _load_exp_cache():
    if os.path.exists(EXP_CACHE_PATH):
        try:
            return json.load(open(EXP_CACHE_PATH, encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _save_exp_cache(cache):
    cutoff = (datetime.date.today() - datetime.timedelta(days=7)).strftime("%Y%m%d")
    save_json(EXP_CACHE_PATH, {k: v for k, v in cache.items() if k[:8] >= cutoff})

RATE_CAP = 40   # 한 번의 collect에서 등락률 조회할 최대 종목수(주식선물+지수선물)

def _index_code(name):
    """지수선물 종목명 → 네이버 지수코드. 코스피200=KPI200 / 코스닥150=KQI150 (그 외 미지원)."""
    n = (name or "").replace(" ", "").upper()
    if "코스피200" in n or "KOSPI200" in n:
        return "KPI200"
    if "코스닥150" in n or "KOSDAQ150" in n:
        return "KQI150"
    return ""

def _attach_rates(events, cap=RATE_CAP):
    """가격제한폭 이벤트에 기초자산 현재 등락률(부호 %) 부착.
    주식선물 → 기초주식 등락률 / 지수선물 → 해당 지수(코스피200·코스닥150) 등락률."""
    n = 0
    for e in events:
        if n >= cap:
            break
        if e.get("kind") == "주식선물" and e.get("code"):
            q = md_feed.stock_rate(e["code"]); n += 1
        elif e.get("kind") == "지수선물" and _index_code(e.get("name", "")):
            q = md_feed.index_rate(_index_code(e["name"])); n += 1
        else:
            continue   # KRX300선물 등 지원 지수 외 → 등락률 없음
        if q and q.get("rate") is not None:
            e["rate"] = q["rate"]

def collect(sel_date=None, watch_only=True, enrich=True, enrich_cap=ENRICH_CAP):
    """가격제한폭 도달 공시 수집 + 본문 '확대 예정시각' 보강(캐시, 신규분만 조회) + 기초주식 등락률."""
    events = parse(kind_client.feed(sel_date or datetime.date.today().strftime("%Y-%m-%d"), "3"), watch_only)
    if enrich:
        cache = _load_exp_cache()
        fetched, changed = 0, False
        for e in events:
            rc = e["rcept_no"]
            if rc in cache:
                e["expand_time"] = cache[rc]
            elif fetched < enrich_cap:
                t = fetch_expand_time(rc); fetched += 1
                if t:
                    cache[rc] = t; changed = True
                e["expand_time"] = t
            # 캡 초과분은 expand_time 미설정 → 다음 주기에 채움
        if changed:
            _save_exp_cache(cache)
    _attach_rates(events)   # 주식선물 → 기초주식 실시간 등락률(매 주기 갱신)
    return events

def main():
    sel = sys.argv[1] if len(sys.argv) > 1 and re.match(r"\d{4}-\d{2}-\d{2}", sys.argv[1]) else None
    evs = collect(sel, enrich_cap=300)   # 수동 실행: 가능한 만큼 본문 보강
    payload = {
        "date": sel or datetime.date.today().strftime("%Y-%m-%d"),
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(evs), "events": evs,
    }
    save_json(os.path.join(DATA_DIR, "limits.json"), payload)
    idx = sum(e["kind"] == "지수선물" for e in evs)
    stk = sum(e["kind"] == "주식선물" for e in evs)
    s3 = sum(e["stage"] >= 3 for e in evs)
    exp = sum(bool(e.get("expand_time")) for e in evs)
    print(f"TLS={TLS_MODE} | limits.json 저장: {len(evs)}건 "
          f"(지수선물 {idx} · 주식선물 {stk} · 3단계 {s3} · 확대예정시각 {exp})")

if __name__ == "__main__":
    main()
