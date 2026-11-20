# -*- coding: utf-8 -*-
"""기업행위 결정공시 파서 — 분할·병합·감자·회사분할의 신주 상장예정일(거래정지 재개 예정일) (접속 없음)"""
import re
from core.krx_cal import norm_date

def parse_relist(t):
    m = re.search(r"상장예정일\s*(\d{4}\D+\d{1,2}\D+\d{1,2})", t.replace(" ", ""))
    return {"listing_date": norm_date(m.group(1)) if m else ""}
