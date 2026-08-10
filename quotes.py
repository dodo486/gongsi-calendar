# -*- coding: utf-8 -*-
"""네이버 시세·수급 조회 공용 모듈 — 공시캘린더/수급분석기 공용.
- quote_batch(codes)  : 여러 종목 실시간(거래대금·등락률·현재가·시총) 배치 조회(콤마, 최대 50/콜)
- investor_trend(code): 일별 투자자 순매수(외국인/기관/개인) — 당일 bizdate 는 장중 잠정
- stock_rate/index_rate: 개별종목/지수 등락률(부호%) — earnings 의 naver_rate 와 동일 파서
※ 기존 earnings.naver_rate·consensus.naver_trend 와 중복되나, 향후 그쪽을 이 모듈로 일원화 예정.
"""
import requests
import fetch  # import 시 make_opener()가 truststore로 ssl 전역 패치 → requests도 MITM 인증서 통과

HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://finance.naver.com/"}

# 연결 재사용(keep-alive) — TLS 검사 머신에서 매 호출 핸드셰이크 비용 제거가 핵심
_SESS = requests.Session()
_SESS.headers.update(HDR)
_ADAPTER = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=16)
_SESS.mount("https://", _ADAPTER)

def _get(url):
    r = _SESS.get(url, timeout=10)
    r.raise_for_status()
    return r.json()

def _num(x):
    """'5,577' / '+2.50' / '-2,078,706' / '46.67%' → float. 실패 시 None."""
    if x is None:
        return None
    try:
        return float(str(x).replace(",", "").replace("+", "").replace("%", "").strip())
    except Exception:
        return None

REALTIME = "https://polling.finance.naver.com/api/realtime/domestic/stock/{}"
CHUNK = 50

def quote_batch(codes):
    """{code: {name, price, rate(부호%), value(거래대금원), volume, mktcap}}. 콤마 배치로 빠르게."""
    out = {}
    for i in range(0, len(codes), CHUNK):
        part = [c for c in codes[i:i + CHUNK] if c]
        if not part:
            continue
        try:
            j = _get(REALTIME.format(",".join(part)))
        except Exception:
            continue
        for d in j.get("datas", []):
            code = d.get("itemCode") or d.get("symbolCode")
            if not code:
                continue
            sign = (d.get("compareToPreviousPrice") or {}).get("code", "3")  # 1상한2상승3보합4하락5하한
            mag = _num(d.get("fluctuationsRatio")) or 0.0
            rate = mag if sign in ("1", "2") else (-mag if sign in ("4", "5") else 0.0)
            out[code] = {
                "name": d.get("stockName", ""),
                "price": _num(d.get("closePriceRaw")),
                "rate": round(rate, 2),
                "value": _num(d.get("accumulatedTradingValueRaw")),   # 당일 누적 거래대금(원)
                "volume": _num(d.get("accumulatedTradingVolumeRaw")),
                "mktcap": _num(d.get("marketValueFullRaw")),
            }
    return out

TREND = "https://m.stock.naver.com/api/stock/{}/trend"

def investor_trend(code, n=20):
    """일별 투자자 순매수(주) 최신순 [{date, frgn, org, indi, close}]. 당일 bizdate 는 장중 잠정."""
    try:
        d = _get(TREND.format(code))
    except Exception:
        return []
    rows = d if isinstance(d, list) else []
    out = []
    for x in rows:
        out.append({
            "date": x.get("bizdate", ""),
            "frgn": int(_num(x.get("foreignerPureBuyQuant")) or 0),
            "org": int(_num(x.get("organPureBuyQuant")) or 0),
            "indi": int(_num(x.get("individualPureBuyQuant")) or 0),
            "close": int(_num(x.get("closePrice")) or 0),
        })
    return out[:n]

import re as _re
FRGN_PAGE = "https://finance.naver.com/item/frgn.naver?code={}&page={}"
WISE_COMP = "https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd={}"
_FLOAT_CACHE = {}   # code -> 유동주식수(거의 정적, 프로세스 캐시)

def float_shares(code):
    """유동가능주식수 = 발행주식수 × 유동비율. (wisereport, 거의 정적 → 캐시)
    실패 시 None."""
    if code in _FLOAT_CACHE:
        return _FLOAT_CACHE[code]
    try:
        t = _SESS.get(WISE_COMP.format(code), timeout=10).content.decode("utf-8", "replace")
        m = _re.search(r"발행주식수/유동비율[^0-9]*([\d,]+)\s*주\s*/\s*([\d.]+)\s*%", t)
        if m:
            issued = float(m.group(1).replace(",", ""))
            ratio = float(m.group(2))
            fs = int(issued * ratio / 100)
            _FLOAT_CACHE[code] = fs
            return fs
    except Exception:
        pass
    _FLOAT_CACHE[code] = None
    return None

def frgn_daily(code, pages=2):
    """일별 외국인·기관 순매매(주) — 데스크톱 소스(페이지당 ~21일, 모바일 10일 한계 극복).
    [{date(YYYYMMDD), close, frgn, org}] 최신→과거. 부호 포함."""
    out = []
    for pg in range(1, pages + 1):
        try:
            r = _SESS.get(FRGN_PAGE.format(code, pg), timeout=10)
            html = r.content.decode("euc-kr", "replace")
        except Exception:
            break
        for row in _re.split(r'onMouseOver="mouseOver\(this\)"', html)[1:]:
            tds = _re.findall(r"<td[^>]*>(.*?)</td>", row, _re.S)
            if len(tds) < 7:
                continue
            txt = [_re.sub(r"<[^>]+>", "", c).replace(",", "").strip() for c in tds]
            dt = txt[0].replace(".", "")
            if len(dt) != 8 or not dt.isdigit():
                continue
            row = {"date": dt, "close": int(_num(txt[1]) or 0),
                   "org": int(_num(txt[5]) or 0), "frgn": int(_num(txt[6]) or 0)}
            if len(txt) > 8:                       # 보유주수·보유율(외국인)
                row["hold_qty"] = int(_num(txt[7]) or 0)
                row["hold_pct"] = _num(txt[8])
            out.append(row)
    return out

PRICE = "https://m.stock.naver.com/api/stock/{}/price?pageSize={}&page=1"

def daily_price(code, n=40):
    """일별 OHLC·거래량 [{date, open, high, low, close, vol}] 최신→과거.
    ※ 이 API의 등락률 부호는 신뢰 불가 → 등락률은 호출측에서 종가대비 재계산할 것."""
    try:
        d = _get(PRICE.format(code, n))
    except Exception:
        return []
    out = []
    for it in (d if isinstance(d, list) else []):
        dt = (it.get("localTradedAt") or "").replace("-", "")
        if not dt:
            continue
        out.append({"date": dt, "close": _num(it.get("closePrice")),
                    "open": _num(it.get("openPrice")), "high": _num(it.get("highPrice")),
                    "low": _num(it.get("lowPrice")),
                    "vol": _num(it.get("accumulatedTradingVolume"))})
    return out

def _one_rate(url):
    try:
        d = (_get(url).get("datas") or [{}])[0]
        ratio = d.get("fluctuationsRatio")
        if ratio is None:
            return {}
        sign = (d.get("compareToPreviousPrice") or {}).get("code", "3")
        mag = _num(ratio) or 0.0
        rate = mag if sign in ("1", "2") else (-mag if sign in ("4", "5") else 0.0)
        return {"price": d.get("closePrice", ""), "rate": round(rate, 2), "sign": sign}
    except Exception:
        return {}

def stock_rate(code):
    return _one_rate(f"https://polling.finance.naver.com/api/realtime/domestic/stock/{code}")

def index_rate(code):
    """code: KPI200(코스피200)/KQI150(코스닥150)."""
    return _one_rate(f"https://polling.finance.naver.com/api/realtime/domestic/index/{code}")
