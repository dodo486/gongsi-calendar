"""섹터/테마 수급 집계 (P2+P3) — 시총 편향 없는 로테이션 포착.

두 지표를 '둘 다' 산출해 사용자가 실전 검증 후 선택:
  · 회전율(turnover)     = 오늘 거래대금 / 시총           → "시총 대비 활발도" (정적)
  · 상대거래대금(relvol)  = 오늘 거래대금 / 최근평균 거래대금  → "평소 대비 몇 배" (변화·선행)

집계 단위: WICS 표준업종 + 커스텀 테마(로봇·AI·원전 등, WICS 가로지름).
동일가중 평균(중소형주도 동등) — 절대 거래대금 점유율 방식의 대형주 편향 제거.

출력:
  data/sectors_flow.json      최신 스냅샷(UI용)
  data/dayval_cache.json      종목별 과거 일별 거래대금(하루 1회 갱신 — relvol 분모)
  data/sector_history.jsonl   섹터 스냅샷 시계열(append) — 장중/일봉 Δ 계산용

  python sectors.py           # 1회 집계 → sectors_flow.json (+ 히스토리 append)
  python sectors.py --rebuild # 과거 거래대금 캐시 강제 재구축
"""
import sys, os, json, datetime
from concurrent.futures import ThreadPoolExecutor
import quotes
import market_universe as mu
import get_sectors
import supply
from fetch import DATA_DIR, save_json

SECTORS_PATH = os.path.join(DATA_DIR, "sectors.json")
OUT_PATH = os.path.join(DATA_DIR, "sectors_flow.json")
DAYVAL_PATH = os.path.join(DATA_DIR, "dayval_cache.json")
HIST_PATH = os.path.join(DATA_DIR, "sector_history.jsonl")

JO = 1e12
UNIVERSE_N = 800       # 수급 유니버스: 전체시장 거래대금 상위 N
RELVOL_DAYS = 10       # 상대거래대금 분모: 최근 N영업일 평균(당일 제외)
LEAD_MAX_RET = 5.0     # 선행/후행 경계(최근 등락률) — flow.py와 동일 관점
MIN_N = 2              # 섹터 최소 종목수(집계 노이즈 컷)
NOLIMIT_MAX = 30.5     # 가격제한폭(±30%) 초과 등락률 = 정리매매·신규상장 첫날 등 → 섹터 노이즈로 제외
TREND_N = 80           # 순매수(외국인/기관) 부착 종목 수(거래대금 상위)


ETF_THEMES_PATH = os.path.join(DATA_DIR, "etf_themes.json")


def load_sector_map():
    with open(SECTORS_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_etf_constituents():
    """ETF코드 → {name, codes:[...]}. get_etf_themes.py 산출물의 etfs 섹션."""
    if not os.path.exists(ETF_THEMES_PATH):
        return {}
    try:
        d = json.load(open(ETF_THEMES_PATH, encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for ec, info in d.get("etfs", {}).items():
        codes = [c["code"] for c in info.get("constituents", [])]
        if codes:
            out[ec] = {"name": info.get("name", ec), "codes": codes}
    return out


# ── relvol 분모 캐시: 종목별 과거 일별 거래대금(당일 제외) 평균 ────────────
def _today():
    return datetime.date.today().strftime("%Y%m%d")


def build_dayval_cache(codes, force=False):
    """종목별 최근 거래대금 평균(당일 제외)을 캐시. 날짜 바뀌면 전체 재구축,
    같은 날엔 미커버 종목만 증분 조회(유니버스 확장 대응)."""
    cache = {}
    if os.path.exists(DAYVAL_PATH) and not force:
        try:
            cache = json.load(open(DAYVAL_PATH, encoding="utf-8"))
        except Exception:
            cache = {}
    avgs = cache.get("avg", {}) if cache.get("date") == _today() and not force else {}
    missing = [c for c in codes if c not in avgs]
    if not missing:
        return {"date": _today(), "avg": avgs}

    print(f"  [relvol] 거래대금 캐시 {'재구축' if not avgs else '증분'} — {len(missing)}종목 daily_price 조회…")

    def _one(code):
        dp = quotes.daily_price(code, n=RELVOL_DAYS + 2)  # 최신→과거
        # 당일(첫 항목) 제외, 과거 RELVOL_DAYS일 거래대금(종가×거래량) 평균
        vals = [(r["close"] or 0) * (r["vol"] or 0) for r in dp[1:1 + RELVOL_DAYS]]
        vals = [v for v in vals if v > 0]
        avg = sum(vals) / len(vals) if vals else None
        return code, avg

    with ThreadPoolExecutor(max_workers=8) as ex:
        for code, avg in ex.map(_one, missing):
            if avg:
                avgs[code] = avg
    out = {"date": _today(), "avg": avgs}
    save_json(DAYVAL_PATH, out)
    print(f"  [relvol] 캐시 완료 — {len(avgs)}종목")
    return out


# ── 집계 ────────────────────────────────────────────────────────────────
def _agg(items, keyfn):
    """items(종목 레코드) → {key: 집계}. keyfn: 레코드→[키...] (테마는 다중)."""
    buckets = {}
    for r in items:
        for key, name, kind in keyfn(r):
            b = buckets.setdefault(key, {
                "name": name, "kind": kind, "codes": [],
                "turn": [], "relvol": [], "rate": [],
                "netbuy_val": 0.0, "mktcap": 0.0, "lead": 0, "lag": 0,
            })
            b["codes"].append(r["code"])
            if r["turn"] is not None:
                b["turn"].append(r["turn"])
            if r["relvol"] is not None:
                b["relvol"].append(r["relvol"])
            if r["rate"] is not None:
                b["rate"].append(r["rate"])
            if r.get("netbuy_val"):
                b["netbuy_val"] += r["netbuy_val"]
            b["mktcap"] += r["mktcap"] or 0
            if r.get("relvol") and r["relvol"] >= 2.0:  # 평소 2배↑ 터진 종목만 선행/후행 카운트
                if (r["rate"] or 0) <= LEAD_MAX_RET:
                    b["lead"] += 1
                else:
                    b["lag"] += 1
    return buckets


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _finalize(buckets):
    out = []
    for key, b in buckets.items():
        n = len(b["codes"])
        if n < MIN_N:
            continue
        turn = sum(b["turn"]) / len(b["turn"]) if b["turn"] else None
        relvol = _median(b["relvol"]) if b["relvol"] else None   # 중앙값(이상치 완화)
        rate = sum(b["rate"]) / len(b["rate"]) if b["rate"] else None
        netbuy_pct = (b["netbuy_val"] / b["mktcap"] * 100) if b["mktcap"] else None
        breadth = round((b["lead"] + b["lag"]) / n * 100, 1)  # 평소2배↑ 터진 종목 비율
        out.append({
            "key": key, "name": b["name"], "kind": b["kind"], "n": n,
            "turnover": round(turn, 3) if turn is not None else None,
            "relvol": round(relvol, 2) if relvol is not None else None,
            "rate": round(rate, 2) if rate is not None else None,
            "netbuy_pct": round(netbuy_pct, 4) if netbuy_pct is not None else None,
            "breadth": breadth, "lead": b["lead"], "lag": b["lag"],
            "codes": b["codes"],
        })
    return out


def build():
    # 전체시장 거래대금 상위 N = 수급 유니버스 (live 데이터 한 소스에서 확보)
    rows_live = mu.top_universe(UNIVERSE_N)
    codes = [r["code"] for r in rows_live]

    # 섹터 분류 보장 — 유니버스에 새 종목(급등 진입 등) 있으면 증분 크롤
    stocks = load_sector_map()["stocks"]
    if any(c not in stocks for c in codes):
        get_sectors.classify({r["code"]: r["name"] for r in rows_live})
        stocks = load_sector_map()["stocks"]

    dayval = build_dayval_cache(codes)["avg"]

    # 거래대금 상위 TREND_N 종목에만 순매수(외국인/기관) 부착 (전체 콜은 과중)
    trend_codes = codes[:TREND_N]   # rows_live 는 거래대금 내림차순
    netbuy = {}

    def _tr(code):
        t = quotes.investor_trend(code, n=1)
        return code, (t[0] if t else None)
    with ThreadPoolExecutor(max_workers=8) as ex:
        for code, t in ex.map(_tr, trend_codes):
            if t:
                netbuy[code] = t

    # 종목 레코드
    recs = []
    for d in rows_live:
        code = d["code"]
        val, mc = d.get("value"), d.get("mktcap")
        if not val or not mc:
            continue
        rt = d.get("rate")
        if rt is not None and abs(rt) > NOLIMIT_MAX:   # 정리매매·신규상장 등 제한폭 없는 종목 → 섹터 노이즈 제외
            continue
        s = stocks.get(code, {})
        avg = dayval.get(code)
        nb = netbuy.get(code)
        netbuy_val = (nb["frgn"] + nb["org"]) * d["price"] if (nb and d.get("price")) else None
        recs.append({
            "code": code, "name": d["name"], "market": d.get("market", ""),
            "themes": s.get("themes", []),
            "rate": d.get("rate"), "value": val, "mktcap": mc,
            "turn": round(val / mc * 100, 3),
            "relvol": round(val / avg, 2) if avg else None,
            "netbuy_val": netbuy_val,
        })

    # 테마 집계(다중 태그)
    themes = _finalize(_agg(recs, lambda r: [(t, t, "theme") for t in r["themes"]]))
    # ETF 바스켓 집계 — 개별 ETF 구성종목 수급 롤업
    etf_map = load_etf_constituents()
    code2etf = {}
    for ec, info in etf_map.items():
        for c in info["codes"]:
            code2etf.setdefault(c, []).append((ec, info["name"]))
    etfs = _finalize(_agg(recs, lambda r: [(ec, nm, "etf") for ec, nm in code2etf.get(r["code"], [])]))

    # 테마별 메타 + ETF→테마 매핑
    theme_meta, etf_theme_of = {}, {}
    if os.path.exists(ETF_THEMES_PATH):
        try:
            ejson = json.load(open(ETF_THEMES_PATH, encoding="utf-8"))
            for th, v in ejson.get("themes", {}).items():
                theme_meta[th] = {"etf_count": v.get("etf_count", 0),
                                  "etfs": v.get("etfs", []),
                                  "member_count": v.get("member_count", {})}
            for ec, info in ejson.get("etfs", {}).items():
                etf_theme_of[ec] = info.get("themes", [])
        except Exception:
            pass

    # ETF를 섹터(테마)로 묶기 — 낱개 ETF가 아니라 '섹터별 ETF 클러스터'로 집계
    grp = {}
    etf_by_code = {e["key"]: e for e in etfs}
    # 개별 ETF 등락률은 '구성종목 평균'이 아니라 ETF 본체의 실제 등락률(실시간 시세)로 교체.
    # (동일가중 구성종목 평균은 소형 급등주를 과대반영 → 실제 ETF 등락률과 크게 어긋남)
    try:
        _eq = quotes.quote_batch(list(etf_by_code))
        for ec, e in etf_by_code.items():
            lr = (_eq.get(ec) or {}).get("rate")
            if lr is not None:
                e["rate"] = lr
    except Exception:
        pass
    for ec, e in etf_by_code.items():
        for th in etf_theme_of.get(ec, []):
            if th in ("AI", "인터넷플랫폼", "지주"):   # 광범위 테마 제외
                continue
            g = grp.setdefault(th, {"etfs": [], "rv": [], "rt": [], "tn": []})
            g["etfs"].append(e)
            if e["relvol"] is not None:
                g["rv"].append(e["relvol"])
            if e["rate"] is not None:
                g["rt"].append(e["rate"])
            if e["turnover"] is not None:
                g["tn"].append(e["turnover"])
    etf_groups = []
    for th, g in grp.items():
        if not g["etfs"]:
            continue
        etf_groups.append({
            "key": th, "name": th, "kind": "etfgroup", "n": len(g["etfs"]),
            "relvol": round(_median(g["rv"]), 2) if g["rv"] else None,
            "rate": round(sum(g["rt"]) / len(g["rt"]), 2) if g["rt"] else None,
            "turnover": round(sum(g["tn"]) / len(g["tn"]), 3) if g["tn"] else None,
            "netbuy_pct": None, "breadth": 0, "lead": 0, "lag": 0,
            "codes": theme_meta.get(th, {}).get("etfs", []) and [],  # 사용 안함
            "etfs": [{"code": x["key"], "name": x["name"], "relvol": x["relvol"],
                      "rate": x["rate"], "n": x["n"]}
                     for x in sorted(g["etfs"], key=lambda x: -(x["relvol"] or 0))],
        })

    # 섹터별 전체 구성종목 로스터 — 그 섹터 ETF들의 PDF를 다 합쳐 나래비(공통도순)
    rec_by_code = {r["code"]: r for r in recs}
    theme_roster = {}
    if os.path.exists(ETF_THEMES_PATH):
        try:
            ejson2 = json.load(open(ETF_THEMES_PATH, encoding="utf-8"))

            def _wt(w):
                try:
                    return float(str(w).replace("%", "").replace(",", ""))
                except Exception:
                    return None
            acc = {}   # theme -> code -> {name, cnt, wts[]}
            for ec, info in ejson2.get("etfs", {}).items():
                ths = [t for t in info.get("themes", []) if t not in ("AI", "인터넷플랫폼", "지주")]
                if not ths:
                    continue
                for c in info.get("constituents", []):
                    code = c.get("code"); nm = c.get("name"); w = _wt(c.get("weight"))
                    if not code:
                        continue
                    for th in ths:
                        a = acc.setdefault(th, {}).setdefault(code, {"name": nm, "cnt": 0, "wts": []})
                        a["cnt"] += 1
                        if w is not None:
                            a["wts"].append(w)
            for th, cd in acc.items():
                etfn = theme_meta.get(th, {}).get("etf_count", 0)
                lst = []
                for code, a in cd.items():
                    rec = rec_by_code.get(code)
                    lst.append({
                        "code": code, "name": a["name"], "cnt": a["cnt"], "etf_count": etfn,
                        "weight": round(sum(a["wts"]) / len(a["wts"]), 2) if a["wts"] else None,
                        "relvol": rec["relvol"] if rec else None,
                        "rate": rec["rate"] if rec else None,
                        "turn": rec["turn"] if rec else None,
                        "inuniv": rec is not None,
                    })
                lst.sort(key=lambda x: (-x["cnt"], -(x["weight"] or 0)))
                theme_roster[th] = lst
        except Exception:
            pass

    # ── 수급 신호(선행매집·외인/기관 순매수)로 '초기유입' 판정 ─────────────
    # 로스터 구성종목의 investor_trend(하이브리드 캐시) → 종목별 선행/후행 + 섹터 집계
    roster_codes = sorted({x["code"] for lst in theme_roster.values() for x in lst})
    try:
        supply.refresh(roster_codes)
    except Exception as ex:
        print(f"  [수급] refresh 실패: {ex}")
    sup_by_sector = {}   # key -> {lead_cnt, lag_cnt, netbuy_pct(외인)}
    for key, lst in theme_roster.items():
        lc = la = 0
        nb = mc = 0.0
        for x in lst:
            s = supply.signal(x["code"])
            if not s:
                x["supply"] = None
                continue
            x["supply"] = {
                "poc": s["poc"], "above": s["above"], "accum_days": s["accum_days"],
                "lead": s["lead"], "lag": s["lag"], "frgn_today": s["frgn_today"],
                "buy_cases": s["buy_cases"], "buy_from": s["buy_from"], "buy_to": s["buy_to"],
            }
            if s["lead"]:
                lc += 1
            if s["lag"]:
                la += 1
            rp = rec_by_code.get(x["code"])
            ft = s["frgn_today"]
            if rp and rp.get("price") and rp.get("mktcap") and ft is not None:
                nb += ft * rp["price"]   # 외국인 순매수 금액
                mc += rp["mktcap"]
        sup_by_sector[key] = {
            "lead_cnt": lc, "lag_cnt": la,
            "netbuy_pct": round(nb / mc * 100, 4) if mc else None,
        }

    def _apply_supply(sec):
        s = sup_by_sector.get(sec["key"])
        if not s:
            return
        sec["lead_cnt"] = s["lead_cnt"]; sec["lag_cnt"] = s["lag_cnt"]
        if s["netbuy_pct"] is not None:
            sec["netbuy_pct"] = s["netbuy_pct"]
        rv = sec.get("relvol") or 0
        rate = sec.get("rate") or 0
        damp = 1.0 if rate <= LEAD_MAX_RET else 0.3
        sec["inflow_score"] = round(rv * sec["lead_cnt"] * damp, 2)
        sec["inflow"] = (rv >= 1.0 and sec["lead_cnt"] >= 2 and rate <= LEAD_MAX_RET)
    for t in themes:
        _apply_supply(t)
    for g in etf_groups:
        _apply_supply(g)

    # 섹터 랭킹의 '등락률' = 구성종목 평균 등락률(로스터=ETF 구성종목 전체 기반)
    def _roster_avg(key):
        ros = theme_roster.get(key, [])
        rr = [x["rate"] for x in ros if x.get("rate") is not None]
        return round(sum(rr) / len(rr), 2) if rr else None
    # ETF 클러스터 등락률은 위에서 '실제 ETF 등락률 평균'으로 이미 산출됨(roster평균 덮어쓰기 안 함).
    # 테마(WICS/커스텀) 랭킹만 구성종목 동일가중 평균 등락률 사용.
    for t in themes:
        v = _roster_avg(t["key"])
        if v is not None:
            t["rate"] = v

    payload = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "universe": len(recs),
        "themes": sorted(themes, key=lambda s: -(s["relvol"] or 0)),
        "etfs": sorted(etf_groups, key=lambda s: -(s["relvol"] or 0)),
        "theme_meta": theme_meta,
        "theme_roster": theme_roster,
        "stocks": {r["code"]: r for r in recs},
    }
    save_json(OUT_PATH, payload)

    # 히스토리 append(섹터 요약만 — 종목 제외해 경량)
    snap = {
        "t": payload["generated_at"],
        "themes": {s["key"]: {"turnover": s["turnover"], "relvol": s["relvol"],
                              "rate": s["rate"], "netbuy_pct": s["netbuy_pct"]}
                   for s in themes},
    }
    with open(HIST_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(snap, ensure_ascii=False) + "\n")

    print(f"집계 완료: 테마 {len(themes)} · ETF클러스터 {len(etf_groups)} · {len(recs)}종목 → {OUT_PATH}")
    return payload


if __name__ == "__main__":
    if "--rebuild" in sys.argv:
        codes = [r["code"] for r in mu.top_universe(UNIVERSE_N)]
        build_dayval_cache(codes, force=True)
    build()
