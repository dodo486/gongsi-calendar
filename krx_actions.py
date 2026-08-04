# -*- coding: utf-8 -*-
"""KRX 시장조치 실시간 수집 — KIND 당일공시 중 '시장본부/거래소 발신' 행
- 사이드카, 서킷브레이커, 매매거래정지/재개, 투자경고·위험·주의, 단기과열, 공매도과열, 기타시장안내 등
- 코스피=marketType 1 / 코스닥=marketType 2 (파생 가격제한폭은 kind_limits 가 전담)
- data/krx_actions.json 저장 → 웹 상단 '시장조치' 리스트 + monitor 토스트
사용:
  python krx_actions.py            # 오늘 시장조치 → data/krx_actions.json
  python krx_actions.py 2026-08-04 # 특정일자
"""
import json, os, sys, re, datetime, urllib.request, urllib.parse
from fetch import DATA_DIR, save_json, build_ssl_context, TLS_MODE

KIND_URL = "https://kind.krx.co.kr/disclosure/todaydisclosure.do"
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
       "Referer": KIND_URL, "X-Requested-With": "XMLHttpRequest",
       "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
VIEWER = "https://kind.krx.co.kr/common/disclsviewer.do?method=searchInitInfo&acptNo={}&docno="
ACTIONS_PATH = os.path.join(DATA_DIR, "krx_actions.json")
_CTX = None

def _classify(t):
    if "효력정지" in t or "Sidecar" in t: return "사이드카"
    if "서킷브레이커" in t or "일시중단" in t: return "서킷브레이커"
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

def _fetch(mt, sel_date):
    global _CTX
    if _CTX is None:
        _CTX = build_ssl_context()
    today = datetime.date.today().strftime("%Y-%m-%d")
    data = {"method": "searchTodayDisclosureSub", "currentPageSize": "500", "pageIndex": "1",
            "orderMode": "0", "orderStat": "D", "forward": "todaydisclosure_sub", "chose": "all",
            "todayFlag": "Y" if sel_date == today else "N", "selDate": sel_date,
            "marketType": mt, "searchCorpName": ""}
    req = urllib.request.Request(KIND_URL, data=urllib.parse.urlencode(data).encode(), headers=HDR)
    with urllib.request.urlopen(req, timeout=15, context=_CTX) as r:
        return r.read().decode("utf-8", "replace")

def collect(sel_date=None):
    d = sel_date or datetime.date.today().strftime("%Y-%m-%d")
    events = []
    for mt, market in (("1", "KOSPI"), ("2", "KOSDAQ")):
        try:
            html = _fetch(mt, d)
        except Exception:
            continue
        for row in re.split(r"</tr>", html):
            tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
            if len(tds) < 4:
                continue
            submitter = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", tds[3])).strip()
            if "시장본부" not in submitter and "거래소" not in submitter:
                continue   # 시장본부/거래소 발신만 = 시장조치 (개별사 공시 제외)
            m_title = re.search(r"title='([^']+)'", row)
            title = (m_title.group(1) if m_title else re.sub(r"<[^>]+>", " ", tds[2])).strip()
            m_time = re.search(r'([0-9]{1,2}:[0-9]{2})', tds[0])
            tm = m_time.group(1) if m_time else ""
            kind = _classify(title)
            if kind == "시장안내":
                continue   # 기타시장안내(NXT 등 루틴 안내)는 제외
            action = _action(title)
            m_acpt = re.search(r"openDisclsViewer\('(\d+)'", row)
            rno = m_acpt.group(1) if m_acpt else f"{d.replace('-','')}{market}{tm}{title[:16]}"
            events.append({
                "time": tm, "market": market, "kind": kind,
                "direction": "매수" if "매수" in title else ("매도" if "매도" in title else ""),
                "action": action, "until": _until(kind, action, tm),
                "title": title, "submitter": submitter,
                "rcept_no": rno,
                "url": VIEWER.format(m_acpt.group(1)) if m_acpt else "https://kind.krx.co.kr",
            })
    events.sort(key=lambda e: (e["time"], e["rcept_no"]), reverse=True)
    return events

def main(sel_date=None):
    evs = collect(sel_date)
    save_json(ACTIONS_PATH, {
        "date": sel_date or datetime.date.today().strftime("%Y-%m-%d"),
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(evs), "events": evs,
    })
    print(f"TLS={TLS_MODE} | krx_actions.json 저장: {len(evs)}건")
    for e in evs:
        print(f"  {e['time']} {e['market']} [{e['kind']}] {e['action']} {e['title'][:50]}")

if __name__ == "__main__":
    sel = sys.argv[1] if len(sys.argv) > 1 and re.match(r"\d{4}-\d{2}-\d{2}", sys.argv[1]) else None
    main(sel)
