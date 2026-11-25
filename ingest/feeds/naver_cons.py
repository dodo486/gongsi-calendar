# -*- coding: utf-8 -*-
"""네이버 컨센서스(E) — 과거 값을 안 주므로 매일 받아 값이 바뀐 날만 쌓는다 → store consensus
연간: {date, fy, dps, eps}(원) — 배당·실적 추정. 분기: {date, quarter "YYYYMM", ni 컨센 | ni_act 실제}(억원, 당기순이익) — 서프라이즈 판정"""
import datetime
from concurrent.futures import ThreadPoolExecutor
from core import store, universe
from ingest.sources.naver import get

def _rows(code, kind, titles, actual=None):
    """(기간 → {이름: 값}) — 컨센(E) 기간, actual 이면 실제 기간도 그 이름으로"""
    try:
        f = get(f"https://m.stock.naver.com/api/stock/{code}/finance/{kind}")["financeInfo"] or {}
    except Exception:
        return {}
    cons = {t["key"] for t in f.get("trTitleList") or [] if t.get("isConsensus") == "Y"}
    out = {}
    for r in f.get("rowList") or []:
        if r["title"] in titles:
            for k, c in r["columns"].items():
                v = ((c or {}).get("value") or "").replace(",", "")
                if v.lstrip("-").isdigit() and (k in cons or actual):
                    out.setdefault(k, {})[titles[r["title"]] if k in cons else actual] = int(v)
    return out

def _fetch(code):
    return code, _rows(code, "annual", {"주당배당금": "dps", "EPS": "eps"}), _rows(code, "quarter", {"당기순이익": "ni"}, "ni_act")

def run():
    data, today, n = dict(store.load("consensus")), datetime.date.today().isoformat(), 0
    with ThreadPoolExecutor(4) as ex:
        for code, annual, quarter in ex.map(_fetch, sorted(universe.div())):
            h = data.setdefault(code, [])
            for key, field, got in (("fy", lambda k: k[:4], annual), ("quarter", lambda k: k, quarter)):
                for k, vals in got.items():
                    prev = next((x for x in reversed(h) if x.get(key) == field(k)), None)
                    if not prev or any(prev.get(f) != v for f, v in vals.items()):
                        h.append({"date": today, key: field(k), **vals})
                        n += 1
    if n:
        store.save("consensus", data)
    return bool(n)
