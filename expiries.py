# -*- coding: utf-8 -*-
"""
선물·옵션 만기일 생성 (규칙 계산, API 불필요)
- KOSPI200 옵션: 매월 둘째 목요일
- KOSPI200 선물: 3·6·9·12월 둘째 목요일 (옵션과 동시만기 = 네 마녀의 날)
- 해당일이 휴장일(주말·공휴일)이면 직전 영업일로 조정
→ data/expiries.json
"""
import os, datetime
from base import DATA_DIR, save_json
from krx_cal import holiday_dates, default_years, prev_business_day

def second_thursday(year, month):
    d = datetime.date(year, month, 1)
    first_thu = d + datetime.timedelta(days=(3 - d.weekday()) % 7)  # 목=3
    return first_thu + datetime.timedelta(days=7)

def build(years=None):
    if years is None:
        years = default_years()
    hol = holiday_dates(years)
    events = []
    for y in years:
        for m in range(1, 13):
            raw = second_thursday(y, m)
            exp = prev_business_day(raw, hol)
            simul = m in (3, 6, 9, 12)
            events.append({
                "date": exp.strftime("%Y-%m-%d"),
                "type": "선·옵 동시만기" if simul else "옵션만기",
                "label": ("선물·옵션 동시만기 (네 마녀의 날)" if simul else "옵션 만기일"),
                "adjusted": exp != raw,
            })
    payload = {"generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "count": len(events), "events": events,
               "holidays": sorted(d.strftime("%Y-%m-%d") for d in hol)}   # 웹 캘린더 휴일(빨간 글자) 표시용
    save_json(os.path.join(DATA_DIR, "expiries.json"), payload)
    return payload

if __name__ == "__main__":
    p = build()
    print(f"만기일 {p['count']}건 생성")
    for e in p["events"]:
        if e["date"].startswith(str(datetime.date.today().year)):
            print(f"  {e['date']}  {e['type']}" + ("  (휴장→직전영업일 조정)" if e["adjusted"] else ""))
