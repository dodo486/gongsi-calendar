# -*- coding: utf-8 -*-
"""증자 공시 파서 — 유상·무상·유무상증자결정 원문 → 증자구분별 필드 (접속 없음). 신주배정기준일 없는 구분은 뺀다(권리락 없음)"""
import re
from core.krx_cal import norm_date

RE_REC = re.compile(r"신주배정기준일\s*(\d{4}\D+\d{1,2}\D+\d{1,2})")
# 무상: "1주당 신주배정 주식수 보통주식 (주) 0.5" / 유상: "1주당 신주배정주식수 (주) 0.24" 둘 다 대응
RE_RATIO = re.compile(r"1주당\s*신주배정\s*주식수\s*(?:보통주식)?\s*\(주\)\s*([\d.]+)")
# 신주 상장(입고)예정일 — 무상 "신주의 상장 예정일" / 유상 "신주의 상장예정일" 둘 다.
# 라벨 바로 뒤 날짜만 잡으므로 정정공시의 정정전(라벨~날짜 사이에 사유텍스트) 은 건너뛰고 정정후가 잡힌다.
RE_LIST = re.compile(r"신주의\s*상장\s*예정일\s*(\d{4}\D+\d{1,2}\D+\d{1,2})")
# 신주인수권증서 상장(입고) 시작일 — 유상 주주배정 "신주인수권증서 상장(예정)기간 : YYYY.MM.DD ~ ..."
RE_RIGHTS = re.compile(r"신주인수권증서\s*상장(?:예정)?기간\s*[:：]?\s*(\d{4}\D+\d{1,2}\D+\d{1,2})")
RE_SECTIONS = re.compile(r"Ⅰ\.\s*유상증자\s*1\.(.*?)Ⅱ\.\s*무상증자\s*1\.(.*)")
# 유상 신주 발행가 — 확정 우선, 없으면 예정. 정정공시는 정정표 뒤에 본문이 오므로 마지막 값을 쓴다.
RE_ISSUE = re.compile(r"(확정|예정)발행가\s*보통주식\s*\(원\)\s*([\d,]+)")

def parse_cap(t, nm):
    """증자구분 → 필드. 유무상증자결정은 본문 Ⅰ(유상)·Ⅱ(무상)를 따로 파싱. 신주배정기준일 없는 구분은 뺀다."""
    if "유무상증자결정" in nm:
        m = RE_SECTIONS.search(t)
        parts = {"유상증자": m.group(1), "무상증자": m.group(2)} if m else {}
    else:
        parts = {"유상증자" if "유상증자결정" in nm else "무상증자": t}
    out = {}
    for typ, s in parts.items():
        m = RE_REC.search(s)
        if not m:
            continue
        r = RE_RATIO.search(s)
        ml = RE_LIST.search(s)
        mr = RE_RIGHTS.search(s)
        issue = dict(RE_ISSUE.findall(s)) if typ == "유상증자" else {}
        out[typ] = {"record_date": norm_date(m.group(1)),
                    "issue_price": int((issue.get("확정") or issue.get("예정") or "0").replace(",", "")),
                    "ratio": r.group(1) if r else "",
                    "listing_date": norm_date(ml.group(1)) if ml else "",   # 신주 상장(입고)일
                    # 신주인수권증서 상장(입고)일 — 유상 주주배정에만 존재, 무상 오탐 방지
                    "rights_date": norm_date(mr.group(1)) if mr and typ == "유상증자" else ""}
    return out
