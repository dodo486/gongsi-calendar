# -*- coding: utf-8 -*-
"""지수배당포인트용 기준 ETF PDF(구성종목) + 기초지수 종가 수집 → data/index_pdf.json

지수별 기준 ETF (사용자 지정, 2026-09-29):
  코스피200  = KODEX 200        (삼성자산운용 API, fId 2ETF01)
  코스닥150  = KODEX 코스닥150  (삼성자산운용 API, fId 2ETF54)
  KRX300     = TIGER KRX300     (미래에셋 PDF 엑셀, ksdFund KR7292160009)
지수 종가는 세 지수 모두 TIGER 기준가 표의 '기초지수 종가'에서 가져온다
(네이버는 코스닥150·KRX300 지수를 제공하지 않음, KRX 데이터시스템은 사내망 차단).

⚠️ 삼성자산운용(samsungfund.com)은 Cloudflare 가 연속 요청을 차단한다 → 하루 1회만 받고,
   실패하면 직전 성공본을 그대로 둔다(STALE 표시). 재시도는 RETRY_MIN 분 간격.

  python -m ingest.feeds.index_pdf          # 오늘분이 없을 때만 수집 (--force: 무조건 재수집)
"""
import sys, re, datetime
from ingest.sources import net  # 사내망 TLS opener 세팅
import requests
from core.paths import save_json, load_json
from core.krx_cal import iso, ymd, days_ago
from core.fmt import num

from core.universe import INDICES, PDF_PATH
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"}
TIGER = "https://investments.miraeasset.com/tigeretf/ko/product/search/detail"
KODEX = "https://www.samsungfund.com/api/v1/kodex/product-pdf/{fid}.do?gijunYMD={ymd}"
PUBLISH_HHMM = "0830"   # 이 시각 전에 받은 PDF는 전일분일 수 있어 이후 한 번 더 받는다
RETRY_MIN = 30


def _is_stock(code):
    """PDF 행 중 주식만 — 6자리 단축코드(신규 영숫자 코드 0126Z0 포함). 원화예금(KRD...)·선물 등 제외."""
    return len(code) == 6 and code[:2] != "KR"

def _kodex_pdf(fid):
    r = requests.get(KODEX.format(fid=fid, ymd=datetime.date.today().strftime("%Y.%m.%d")), headers={**UA, "Referer": "https://www.samsungfund.com/etf/product/view.do?id=" + fid}, timeout=20)
    r.raise_for_status()
    d = r.json()["pdf"]
    rows = [{"code": x["itmNo"], "name": x["secNm"], "qty": num(x["applyQ"]), "value": num(x["evalA"])}
            for x in d["list"] if _is_stock(x.get("itmNo") or "")]
    return d["gijunYMD"], rows

def _tiger_html_table(url, data):
    r = requests.post(url, data=data, headers=UA, timeout=30)
    r.raise_for_status()
    txt = r.content.decode("utf-8", "replace")
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", txt, re.S | re.I):
        cells = [re.sub(r"<[^>]+>|&nbsp;", " ", c).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
        if cells:
            out.append([re.sub(r"\s+", " ", c) for c in cells])
    return out

def _tiger_pdf(fund):
    rows = _tiger_html_table(TIGER + "/pdf/excel.do",
                             {"ksdFund": fund, "startDate": "", "endDate": "", "period": "", "fixDate": "", "order": ""})
    out = []
    for c in rows:   # No, 종목코드, 종목명, 수량, 평가금액, 비중, 1주수익률
        if len(c) >= 5 and c[0].isdigit() and _is_stock(c[1]):
            out.append({"code": c[1], "name": c[2], "qty": num(c[3]), "value": num(c[4])})
    return ymd(datetime.date.today()), out   # TIGER 엑셀엔 기준일 표기 없음 → 수집일

def _tiger_index_close(fund):
    """TIGER 기준가 표 → 오늘 이전 가장 최근 영업일의 기초지수 종가 (PDF 평가가격과 같은 날)."""
    today = datetime.date.today()
    rows = _tiger_html_table(TIGER + "/price/excel.do",
                             {"ksdFund": fund, "startDate": ymd(days_ago(14, today)),
                              "endDate": ymd(today), "period": "", "fixDate": "", "order": ""})
    best = None
    for c in rows:   # 날짜, 시장가, 등락, 기준가, 등락, 과세기준가, 지수종가, 등락
        m = re.match(r"(\d{4})\.(\d{2})\.(\d{2})$", c[0]) if c else None
        if not m or len(c) < 7:
            continue
        day = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        if day < iso(today) and num(c[6]) and (best is None or day > best[0]):
            best = (day, num(c[6]))
    if not best:
        raise RuntimeError("기초지수 종가 없음")
    return best

def load():
    return load_json(PDF_PATH, {"indices": {}})

def _stale(ix, now):
    """오늘 PUBLISH_HHMM 이후에 성공한 적 없으면 재수집 대상 (단 직전 실패 후 RETRY_MIN 분은 쉼)."""
    ok, tried = ix.get("fetched_at", ""), ix.get("tried_at", "")
    pub = now.strftime("%Y-%m-%d ") + PUBLISH_HHMM[:2] + ":" + PUBLISH_HHMM[2:]
    if ok >= pub or (now.strftime("%H%M") < PUBLISH_HHMM and ok[:10] == iso(now)):
        return False
    if tried and tried > ok:
        last = datetime.datetime.strptime(tried, "%Y-%m-%d %H:%M:%S")
        if (now - last).total_seconds() < RETRY_MIN * 60:
            return False
    return True

def ensure(force=False):
    """지수별로 필요할 때만 수집. 실패한 지수는 직전 성공본 유지 + error 기록. 반환: payload"""
    payload = load()
    ixs = payload.setdefault("indices", {})
    now = datetime.datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    changed = False
    for cfg in INDICES:
        ix = ixs.get(cfg["key"], {})
        if not force and ix and not _stale(ix, now):
            continue
        ix["tried_at"] = stamp
        try:
            pdf_date, rows = _kodex_pdf(cfg["fid"]) if cfg["src"] == "kodex" else _tiger_pdf(cfg["fund"])
            if len(rows) < 50:
                raise RuntimeError(f"PDF 종목수 이상({len(rows)})")
            lvl_date, lvl = _tiger_index_close(cfg["level_fund"])
            ix.update({k: cfg[k] for k in ("name", "etf", "etf_code")})
            ix.update({"pdf_date": pdf_date, "rows": rows, "index_close": lvl, "index_date": lvl_date,
                       "fetched_at": stamp, "error": ""})
            print(f"  [PDF] {cfg['etf']} {len(rows)}종목 · {cfg['name']} {lvl_date} {lvl:,.2f}")
        except Exception as ex:
            ix["error"] = f"{type(ex).__name__}: {ex}"[:200]
            print(f"  [PDF] {cfg['etf']} 수집 실패 — 직전본 유지: {ix['error']}")
        ixs[cfg["key"]] = ix
        changed = True
    if changed:
        payload["generated_at"] = stamp
        save_json(PDF_PATH, payload)
    return payload

def codes():
    """세 PDF 구성종목 합집합 — 배당 수집 대상 확장용 (없으면 빈 집합)."""
    return {r["code"] for ix in load().get("indices", {}).values() for r in ix.get("rows", [])}

if __name__ == "__main__":
    p = ensure(force="--force" in sys.argv)
    for k, ix in p["indices"].items():
        print(k, ix.get("etf"), len(ix.get("rows", [])), ix.get("index_date"), ix.get("index_close"), ix.get("error") or "OK")
