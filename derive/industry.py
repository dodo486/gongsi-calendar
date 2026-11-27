# -*- coding: utf-8 -*-
"""인더스트리 액션 — 같이 움직이는 종목 묶음(굵은 묶음)과 오늘 그 묶음으로 몰린 돈.

묶기 (data/cache/industry_groups.json, REBUILD_DAYS 마다 직전 LOOKBACK 거래일 일봉으로 다시 만든다)
1. 소속표: 네이버 테마(md.themes)
2. 정제: 시장 베타를 뺀 일간 수익률이 같은 테마 나머지 평균과 상관 PRUNE 미만이면 그 테마에서 뺀다.
   3종목 미만으로 줄어든 테마는 버린다
3. 합치기: 테마 지수(정제 멤버 잔차 평균)끼리 평균연결 군집, 상관 MERGE 이상이면 한 묶음
4. 종목마다 잔차 상관이 가장 높은 묶음 하나에만 넣는다. 3종목 미만 묶음은 버린다
묶음 품질 기준(2026-11 측정, 앞 1년으로 묶고 뒤 1년): 묶음 안 잔차 상관 0.25 / 밖 0.00,
한 묶음 2종목+ +5% 날 덜 오른 멤버의 다음 날 시장 대비 +0.08% (무작위 묶음 -0.04%).

오늘 (data/out/industry_action.json): 묶음 종목을 시세 허브에 구독해 허브가 받을 때마다 묶음·세부 테마별
거래대금 합, 평균 등락, 상승 비율, 대장주(거래대금 1위). 등락률은 전일 종가 대비 — 시세는 SOURCE.
시장 조합(MARKETS)마다 그 시장 종목만 남기고 집계한다(scopes) — 묶음 자체는 시장과 무관.
네이버 소속표 원본은 날마다 data/cache/theme_snapshots/YYYYMMDD.json 으로 남긴다(사후 편입 없는 재검증용).

  python -m derive.industry          # 오늘 집계 1회 (묶음 파일이 없거나 오래됐으면 먼저 다시 묶음)
  python -m derive.industry rebuild  # 묶음 강제 재구성
"""
import os, re, sys, datetime
from itertools import combinations
import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, fcluster
from scipy.spatial.distance import squareform
from core import market as md_feed, ops
from core.krx_cal import ymd, days_ago
from core.fmt import pct
from core.paths import save_json, load_json, stamp, CACHE_DIR, OUT_DIR

INDUSTRY_ACTION_PATH = os.path.join(OUT_DIR, "industry_action.json")   # 화면 web/tabs/industry.js
GROUPS_PATH = os.path.join(CACHE_DIR, "industry_groups.json")
SNAP_DIR = os.path.join(CACHE_DIR, "theme_snapshots")

LOOKBACK = 250      # 묶을 때 쓰는 거래일 수
REBUILD_DAYS = 30   # 묶음 재구성 주기(일)
PRUNE = 0.25        # 테마 정제: 나머지 평균과의 잔차 상관 하한
MERGE = 0.6         # 테마 합치기: 테마 지수 상관 하한
MIN_SIZE = 3
UPKEEP_SECONDS = 60  # 하루 한 번 일(구독·소속표)을 챙기는 주기
SOURCE = {md_feed.PRE: md_feed.NXT_STOCK}   # 시간대: 등락률을 재는 시세 — 그 밖엔 KRX(개장 동시호가엔 예상체결가)
MARKETS = ("KOSPI", "KOSDAQ")   # 화면 시장 필터 — 켠 시장 조합마다 그 종목만으로 집계


def _residual(R):
    m = R.mean(axis=1)
    beta = R.apply(lambda x: x.cov(m) / m[x.notna()].var())
    return R - np.outer(m, beta.fillna(1))


def snapshot_themes(themes):
    os.makedirs(SNAP_DIR, exist_ok=True)
    path = os.path.join(SNAP_DIR, ymd(datetime.date.today()) + ".json")
    if themes and not os.path.exists(path):
        save_json(path, themes)


def build_groups():
    themes = md_feed.themes()
    if not themes:
        return None
    codes = [r["code"] for r in md_feed.market_snapshot() if r.get("kind") == "stock" and r["code"].isdigit()]
    start = ymd(days_ago(int(LOOKBACK * 1.5)))
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
    payload = {"generated_at": stamp(),
               "window": [str(R.index[0]), str(R.index[-1])],
               "params": {"lookback": LOOKBACK, "prune": PRUNE, "merge": MERGE},
               "theme_count": len(themes), "pruned_count": len(pruned), "groups": groups}
    save_json(GROUPS_PATH, payload)
    return payload


def _load_groups():
    g = load_json(GROUPS_PATH, {})
    if not g.get("generated_at"):
        return None, True
    return g, (datetime.datetime.now() - datetime.datetime.strptime(g["generated_at"], "%Y-%m-%d %H:%M:%S")).days >= REBUILD_DAYS


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


_now = {"groups": None, "names": {}, "day": None}   # 구독한 묶음, 종목 이름·시장, 구독한 날


def subscribe():
    """묶음 종목을 시세 허브에 구독한다 — 묶음 파일이 없거나 오래됐으면 먼저 다시 묶는다. 구독한 {피드: 코드들}."""
    g, stale = _load_groups()
    if stale:
        g = build_groups() or g
    if not g:
        return {}
    codes = sorted({c for grp in g["groups"] for c in grp["codes"]})
    names = {r["code"]: {"code": r["code"], "name": r["name"], "market": r.get("market", "")}
             for r in md_feed.market_snapshot() if r["code"] in set(codes)}
    _now.update(groups=g, names=names, day=datetime.date.today())
    feeds = {md_feed.KRX_STOCK: codes, md_feed.NXT_STOCK: codes}
    md_feed.watch("인더스트리", lambda phase, now: build_action(phase), feeds)
    return feeds


def _action(groups, snap):
    out = []
    for grp in groups:
        a = _agg(grp["codes"], snap)
        if not a:
            continue
        subs = [{"name": s["name"], **x} for s in grp["subs"] if (x := _agg(s["codes"], snap))]
        ms = [snap[c] for c in grp["codes"] if c in snap]
        a.update(name=grp["name"], themes=grp["themes"], subs=sorted(subs, key=lambda s: -s["value"]),
                 members=sorted(ms, key=lambda m: -(m.get("value") or 0)))
        out.append(a)
    return sorted(out, key=lambda a: -a["value"])


def build_action(phase):
    g, names = _now["groups"], _now["names"]
    feed = SOURCE.get(phase, md_feed.KRX_STOCK)
    snap = {c: {**names[c], "rate": pct(q["price"], q.get("prev_close")), "value": q.get("value")}
            for c, q in md_feed.latest(feed).items() if c in names and q.get("price")}
    scopes = {"+".join(ms): _action(g["groups"], {c: s for c, s in snap.items() if s["market"] in ms})
              for n in range(1, len(MARKETS) + 1) for ms in combinations(MARKETS, n)}
    payload = {"generated_at": stamp(), "source": f"{phase or '장 밖'} · {feed}",
               "groups_built_at": g["generated_at"], "window": g["window"], "scopes": scopes}
    save_json(INDUSTRY_ACTION_PATH, payload)
    return payload


def start():
    """앱이 부른다 — 하루 한 번 일을 챙기며 계속 돈다(시세는 허브 구독으로 들어온다)."""
    ops.every("인더스트리 묶음", UPKEEP_SECONDS, daily_upkeep)


def daily_upkeep():
    """하루 한 번: 시세 허브 구독(묶음이 오래됐으면 다시 묶고), 소속표 스냅샷."""
    if _now["day"] != datetime.date.today():
        subscribe()
    snapshot_themes(md_feed.themes())


if __name__ == "__main__":
    if sys.argv[1:] == ["rebuild"]:
        g = build_groups()
        print(f"{g['generated_at']} 테마 {g['theme_count']} → 정제 {g['pruned_count']} → 묶음 {len(g['groups'])} "
              f"(일봉 {g['window'][0]}~{g['window'][1]})")
    md_feed.get("인더스트리(1회)", subscribe())
    p = build_action(md_feed.phase_of(datetime.datetime.now()))
    gs = p["scopes"]["+".join(MARKETS)]
    print(f"{p['generated_at']} 묶음 {len(gs)}개 (묶은 날 {p['groups_built_at']})")
    for a in gs[:10]:
        print(f"  {a['name']:<20} {a['value'] / 1e8:>9,.0f}억 {a['rate']:+.2f}% 상승 {a['up']}/{a['n']} 대장 {a['lead']['name']}")
