# -*- coding: utf-8 -*-
"""KIND 공시 본문 파서 — 선물 가격제한폭 확대 예정시각, 매매거래정지·해제 (접속 없음)"""
import re

def parse_limit(t):
    e = re.search(r"확대\s*예정시각\s*([0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)", t)
    return {"expand_time": e.group(1) if e else ""}

def _short_reason(reason, extra):
    """정지사유(+기타사유) → 짧은 라벨."""
    txt = (extra or "") + " " + (reason or "")
    for kw in ("주식분할", "액면분할", "주식병합", "액면병합", "무상증자", "유상증자", "감자",
               "회사분할", "합병", "영업양수도", "주식교환", "조회공시", "불성실공시",
               "상장적격성", "실질심사", "정리매매", "투자자 보호", "투자유의"):
        if kw.replace(" ", "") in txt.replace(" ", ""):
            return kw
    r = (reason or "").strip()
    return (r[:20] + "…") if len(r) > 20 else (r or "매매거래정지")

def parse_halt(t):
    """정지 공시 → 정지일시·사유·만료일시 / 해제 공시 → 해제일시"""
    tc = t.replace(" ", "")
    hd = re.search(r"정지일시(\d{4}-\d{2}-\d{2})", tc)
    exp = re.search(r"만료일시([^0-9]*?(?:\d{4}-\d{2}-\d{2})?[가-힣]*)", tc)
    sy = re.search(r"정지사유(.*?)3\.정지기간", tc)
    et = re.search(r"기타사유[:：]?\s*([가-힣A-Za-z0-9]+)", tc)
    rel = re.search(r"해제일시(\d{4}-\d{2}-\d{2})", tc)
    reason_full = (sy.group(1) if sy else "").strip()
    return {"halt_date": hd.group(1) if hd else "", "reason": _short_reason(reason_full, et.group(1) if et else ""),
            "reason_full": reason_full[:60], "expire": (exp.group(1) if exp else "").strip(), "release": rel.group(1) if rel else ""}
