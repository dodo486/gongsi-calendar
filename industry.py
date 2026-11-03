# -*- coding: utf-8 -*-
"""인더스트리 액션 — 같이 움직이는 종목 묶음(굵은 묶음)과 오늘 그 묶음으로 몰린 돈.

묶기 (data/industry_groups.json, REBUILD_DAYS 마다 직전 LOOKBACK 거래일 일봉으로 다시 만든다)
1. 소속표: 네이버 테마(md.themes)
2. 정제: 시장 베타를 뺀 일간 수익률이 같은 테마 나머지 평균과 상관 PRUNE 미만이면 그 테마에서 뺀다.
   3종목 미만으로 줄어든 테마는 버린다
3. 합치기: 테마 지수(정제 멤버 잔차 평균)끼리 평균연결 군집, 상관 MERGE 이상이면 한 묶음
4. 종목마다 잔차 상관이 가장 높은 묶음 하나에만 넣는다. 3종목 미만 묶음은 버린다
묶음 품질 기준(2026-11 측정, 앞 1년으로 묶고 뒤 1년): 묶음 안 잔차 상관 0.25 / 밖 0.00,
한 묶음 2종목+ +5% 날 덜 오른 멤버의 다음 날 시장 대비 +0.08% (무작위 묶음 -0.04%).

오늘 (data/industry_action.json, 60초): 전 종목 스냅샷으로 묶음·세부 테마별 거래대금 합, 평균 등락,
상승 비율, 대장주(거래대금 1위).
네이버 소속표 원본은 날마다 data/theme_snapshots/YYYYMMDD.json 으로 남긴다(사후 편입 없는 재검증용).

  python industry.py          # 오늘 집계 1회 (묶음 파일이 없거나 오래됐으면 먼저 다시 묶음)
  python industry.py rebuild  # 묶음 강제 재구성
"""
import os, re, sys, json, datetime
import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, fcluster
from scipy.spatial.distance import squareform
import md_feed
import market_universe as mu
from fetch import DATA_DIR, save_json

GROUPS_PATH = os.path.join(DATA_DIR, "industry_groups.json")
ACTION_PATH = os.path.join(DATA_DIR, "industry_action.json")
SNAP_DIR = os.path.join(DATA_DIR, "theme_snapshots")

LOOKBACK = 250      # 묶을 때 쓰는 거래일 수
REBUILD_DAYS = 30   # 묶음 재구성 주기(일)
PRUNE = 0.25        # 테마 정제: 나머지 평균과의 잔차 상관 하한
MERGE = 0.6         # 테마 합치기: 테마 지수 상관 하한
MIN_SIZE = 3
CANDLE_HOUR = 18    # 이 시각 이후 하루 한 번 일봉 증분 수집


def _residual(R):
    m = R.mean(axis=1)
    beta = R.apply(lambda x: x.cov(m) / m[x.notna()].var())
    return R - np.outer(m, beta.fillna(1))


def snapshot_themes(themes):
    os.makedirs(SNAP_DIR, exist_ok=True)
    path = os.path.join(SNAP_DIR, datetime.date.today().strftime("%Y%m%d") + ".json")
    if themes and not os.path.exists(path):
        save_json(path, themes)


def build_groups():
    themes = md_feed.themes()
    if not themes:
        return None
    codes = [r["code"] for r in mu.fetch_all() if r.get("kind") == "stock" and r["code"].isdigit()]
    start = (datetime.date.today() - datetime.timedelta(days=int(LOOKBACK * 1.5))).strftime("%Y%m%d")
    rows = md_feed.closes_values(codes, start)
    close = pd.DataFrame({c: {d: px for d, px, _ in r} for c, r in rows.items() if r}).sort_index()
    R = (close.pct_change(fill_method=None) * 100).iloc[1:].tail(LOOKBACK)
    resid = _residual(R)
    live = set(R.columns[R.notna().sum() >= 60])

    pruned = {}
    for t in themes:
        mem = [c for c in dict.fromkeys(s["code"] for s in t["stocks"]) if c in live]
        if len(mem) < MIN_SIZE:
            continue
        sub = resid[mem]
        keep = [c for c in mem if sub[c].corr(sub.drop(columns=c).mean(axis=1), min_periods=60) >= PRUNE]
        if len(keep) >= MIN_SIZE:
            pruned[t["name"]] = keep
    names = list(pruned)
    tidx = pd.DataFrame({n: resid[pruned[n]].mean(axis=1) for n in names})
    d = np.clip(1 - tidx.corr().fillna(0).values, 0, 2)
    np.fill_diagonal(d, 0)
    lab = fcluster(linkage(squareform(d, checks=False), "average"), t=1 - MERGE, criterion="distance")
    clusters = {}
    for n, l in zip(names, lab):
        clusters.setdefault(l, []).append(n)

    cand = {}
    for ns in clusters.values():
        ns.sort(key=lambda n: -len(pruned[n]))
        cand[ns[0]] = (ns, sorted({c for n in ns for c in pruned[n]}))
    gidx = pd.DataFrame({k: resid[m].mean(axis=1) for k, (_, m) in cand.items()})
    owner = {}
    for k, (_, mem) in cand.items():
        for c in mem:
            cr = resid[c].corr(gidx[k], min_periods=60)
            if c not in owner or cr > owner[c][1]:
                owner[c] = (k, cr)
    members = {}
    for c, (k, _) in owner.items():
        members.setdefault(k, []).append(c)

    groups = []
    for k, (ns, _) in cand.items():
        mem = members.get(k, [])
        if len(mem) < MIN_SIZE:
            continue
        ms = set(mem)
        subs = [{"name": n, "codes": [c for c in pruned[n] if c in ms]} for n in ns]
        label = list(dict.fromkeys(re.sub(r"\(.*?\)", "", n).strip() for n in ns))[:2]
        groups.append({"name": " · ".join(label), "themes": ns,
                       "subs": [s for s in subs if len(s["codes"]) >= 2], "codes": sorted(mem)})
    payload = {"built_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "window": [str(R.index[0]), str(R.index[-1])],
               "params": {"lookback": LOOKBACK, "prune": PRUNE, "merge": MERGE},
               "theme_count": len(themes), "pruned_count": len(pruned), "groups": groups}
    save_json(GROUPS_PATH, payload)
    return payload


def _load_groups():
    try:
        with open(GROUPS_PATH, encoding="utf-8") as f:
            g = json.load(f)
        age = datetime.datetime.now() - datetime.datetime.strptime(g["built_at"], "%Y-%m-%d %H:%M:%S")
        return g, age.days >= REBUILD_DAYS
    except Exception:
        return None, True


def _agg(codes, snap):
    rs = [snap[c] for c in codes if c in snap]
    if not rs:
        return None
    rates = [r.get("rate") or 0 for r in rs]
    value = sum(r.get("value") or 0 for r in rs)
    lead = max(rs, key=lambda r: r.get("value") or 0)
    return {"value": value, "rate": round(sum(rates) / len(rates), 2),
            "up": sum(1 for x in rates if x > 0), "n": len(rs),
            "lead": {"code": lead["code"], "name": lead["name"], "rate": lead.get("rate"), "value": lead.get("value")}}


def build_action():
    g, stale = _load_groups()
    if stale:
        g = build_groups() or g
    if not g:
        return None
    snap = {r["code"]: r for r in mu.fetch_all()}
    out = []
    for grp in g["groups"]:
        a = _agg(grp["codes"], snap)
        if not a:
            continue
        a["name"] = grp["name"]
        a["themes"] = grp["themes"]
        a["subs"] = sorted(({"name": s["name"], **_agg(s["codes"], snap)} for s in grp["subs"]
                            if _agg(s["codes"], snap)), key=lambda s: -s["value"])
        ms = [snap[c] for c in grp["codes"] if c in snap]
        a["members"] = [{"code": m["code"], "name": m["name"], "market": m.get("market", ""),
                         "rate": m.get("rate"), "value": m.get("value")}
                        for m in sorted(ms, key=lambda m: -(m.get("value") or 0))]
        out.append(a)
    out.sort(key=lambda a: -a["value"])
    payload = {"generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "groups_built_at": g["built_at"], "window": g["window"],
               "total_value": sum(r.get("value") or 0 for r in snap.values() if r.get("kind") == "stock"),
               "groups": out}
    save_json(ACTION_PATH, payload)
    return payload


_candle_day = None


def daily_upkeep():
    """하루 한 번: 소속표 스냅샷, 장 마감 뒤 일봉 증분 수집."""
    global _candle_day
    snapshot_themes(md_feed.themes())
    now = datetime.datetime.now()
    if now.hour >= CANDLE_HOUR and now.weekday() < 5 and _candle_day != now.date():
        codes = [r["code"] for r in mu.fetch_all() if r.get("kind") == "stock"]
        md_feed.refresh_candles(codes)
        _candle_day = now.date()


if __name__ == "__main__":
    if sys.argv[1:] == ["rebuild"]:
        g = build_groups()
        print(f"{g['built_at']} 테마 {g['theme_count']} → 정제 {g['pruned_count']} → 묶음 {len(g['groups'])} "
              f"(일봉 {g['window'][0]}~{g['window'][1]})")
    p = build_action()
    print(f"{p['generated_at']} 묶음 {len(p['groups'])}개 (묶은 날 {p['groups_built_at']})")
    for a in p["groups"][:10]:
        print(f"  {a['name']:<20} {a['value'] / 1e8:>9,.0f}억 {a['rate']:+.2f}% 상승 {a['up']}/{a['n']} 대장 {a['lead']['name']}")
