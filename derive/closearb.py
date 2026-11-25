# -*- coding: utf-8 -*-
"""종가 차익 — 마감 동시호가 수급으로 베이시스가 오늘 장중 평균과 달라진 종목.

대상: 감시목록(코스피200+코스닥150) 중 주식선물이 있는 종목, 선물은 만기가 오늘 이후인 가장 가까운 월물.

베이시스 % = (선물가 − 현물 기준가) / 현물 기준가. 현물 기준가는 동시호가 중엔 예상체결가, 그 밖엔 KRX 현재가
(15:30 이후 = 종가). 장중 베이시스는 정규장 1분마다 하나 — 선물이 그 분에 체결된 분만 쓴다. 폴러가 늦게 켜졌거나
껐다 켜면 빈 시간대를 KIS 1분봉(선물 종가 vs 현물 종가)으로 채우고, 그 뒤로는 실시간 값으로 잇는다.
괴리 = 지금 − 장중 평균, z = 괴리 / 장중 표준편차. 화면엔 장중 추이(5분 간격)를 같이 그린다.

시세는 시세 허브(core.market)를 구독한다 — 시간대별로 무엇을 받는지는 허브의 SCHEDULE 이 정하고,
허브가 한 바퀴 받을 때마다 on_tick 으로 계산한다.

기록 (data/closearb/YYYYMMDD.json)
  intraday    {종목: {HHMM: 베이시스 %}}   filled_until  장중 베이시스를 채운 마지막 HHMM
  marks       MARKS 의 시점마다 {종목: 그때 값}
  next        다음 거래일 KRX 시가(krx_open)·NEXT_CHECK_AT 가격(krx_check) — 종가가 얼마나 돌아왔나

  python -m derive.closearb   # 1회 받아서 data/out/closearb.json 갱신
"""
import os, datetime, statistics
from core import market as md_feed, universe
from core.paths import save_json, load_json, stamp, CACHE_DIR, CLOSEARB_PATH
from core.expiries import expiry_of
from core.krx_cal import session_delay, iso, ymd, minutes, kst
from core.fmt import pct

LOG_DIR = os.path.join(CACHE_DIR, "closearb")

ND = 3                   # 베이시스·괴리 % 소수 자리
MIN_SAMPLES = 20         # 장중 표본(분)이 이보다 적으면 z 를 내지 않는다
TREND_MINUTES = 5        # 화면 추이 간격
BASKET_MIN = 5           # 바스켓 추이 한 칸에 필요한 종목 수
BASKET_FLAT = 0.05       # 10분 변화가 이 %p 안이면 오름·내림에 안 센다
Z_HOT = 3                # |z| 이 이상 = 튄 종목
Z_CALM = 2               # |z| 이 미만 = 잠잠
HOT_MAX = 12             # 튄 종목 카드 수
TREND_END = minutes(md_feed.KRX_CLOSE) - minutes(md_feed.KRX_OPEN)   # 추이 그래프 끝(개장부터 분)
NEXT_OPEN_AT = "0901"    # 다음 거래일 시가는 시가가 확정된 이 시각 뒤에 적는다
NEXT_CHECK_AT = "0930"   # 다음 거래일 이 시각 가격으로 종가가 얼마나 돌아왔나 본다
MARKS = {   # 기록 시점: 화면 이름
    "before_auction": "동시호가 직전", "auction_last": "동시호가 마지막", "krx_close": "KRX 종가",
    "fut_close": "선물 종가", "nxt_last": "NXT 마지막",
}


def front_futures(today):
    """{기초 종목코드: {code, month, multiplier}} — 만기일이 오늘 이후인 가장 가까운 월물.
    만기일 당일 월물은 15:20에 거래가 끝나 종가 차익에 못 쓰므로 다음 월물로 넘긴다."""
    out = {}
    for r in md_feed.stock_futures():
        if expiry_of(int(r["month"][:4]), int(r["month"][4:])) <= today:
            continue
        if r["underlying"] not in out or r["month"] < out[r["underlying"]]["month"]:
            out[r["underlying"]] = r
    return out


def _log_path(d):
    return os.path.join(LOG_DIR, ymd(d) + ".json")


def load_log(d):
    return load_json(_log_path(d), {"date": iso(d), "intraday": {}, "marks": {}, "next": {}})


def save_log(d, log):
    os.makedirs(LOG_DIR, exist_ok=True)
    save_json(_log_path(d), log)


def previous_log(today):
    """오늘 전 가장 최근 기록 (날짜, 기록) / 없으면 (None, None)."""
    try:
        names = sorted(n for n in os.listdir(LOG_DIR) if n.endswith(".json") and n[:8] < ymd(today))
    except OSError:
        return None, None
    if not names:
        return None, None
    d = datetime.datetime.strptime(names[-1][:8], "%Y%m%d").date()
    return d, load_log(d)


def _minute_of_day(hhmm):
    """개장부터 분"""
    return minutes(hhmm) - minutes(md_feed.KRX_OPEN)


def build_row(code, stock, fut_info, krx, book, nxt, fut, status, phase, before_auction, series, mktcap):
    auction = phase in (md_feed.OPEN_AUCTION, md_feed.AUCTION)
    spot = krx.get("price")   # 동시호가 중엔 예상체결가
    nxt_mid = (nxt["ask"] + nxt["bid"]) / 2 if nxt.get("ask") and nxt.get("bid") else nxt.get("price")
    basis = pct(fut.get("price"), spot, ND)
    series = series or {}
    vals = list(series.values())
    n = len(vals)
    mean = statistics.fmean(vals) if vals else None
    std = statistics.pstdev(vals) if vals else 0.0
    dev = round(basis - mean, ND) if basis is not None and mean is not None else None
    ago = (datetime.datetime.now() - datetime.timedelta(minutes=10)).strftime("%H%M")
    before = [hm for hm in series if hm <= ago]
    trend = {}
    for hm in sorted(series):
        trend[_minute_of_day(hm) // TREND_MINUTES * TREND_MINUTES] = round(series[hm], ND)
    vol_before = (before_auction or {}).get("fut_vol")
    vol_after = fut["volume"] - vol_before if fut.get("volume") is not None and vol_before is not None else None
    z = round(dev / std, 1) if dev is not None and n >= MIN_SAMPLES and std > 0 else None
    bid_q, ask_q = krx.get("total_bid_qty"), krx.get("total_ask_qty")
    return {
        "code": code, "name": stock["name"], "market": stock["market"], "mktcap": mktcap, "fut_code": fut_info["code"], "fut_month": fut_info["month"],
        "multiplier": fut_info["multiplier"],
        "status": status,
        "spot": spot, "spot_kind": "예상" if auction else ("종가" if phase in (md_feed.PRE, md_feed.FUT_CLOSE, md_feed.AFTER) else "현재"),
        "jump_pct": pct(spot, (before_auction or {}).get("spot"), ND),
        "imb_eok": round((bid_q - ask_q) * spot / 1e8, 1) if spot and bid_q is not None and ask_q is not None else None,
        "expected_qty": book.get("expected_qty") if phase == md_feed.AUCTION else None, "bid_qty": bid_q, "ask_qty": ask_q,
        "nxt": nxt_mid,
        "nxt_gap_pct": pct(spot, nxt_mid, ND),
        "fut": fut.get("price"), "fut_vol": fut.get("volume"),
        "fut_vol_after_auction": vol_after,
        "basis_pct": basis, "mean_pct": round(mean, ND) if mean is not None else None, "n": n,
        "dev_pct": dev,
        "chg10_pct": round(basis - series[max(before)], ND) if basis is not None and before else None,
        "dev_won": round(dev / 100 * spot * fut_info["multiplier"]) if dev is not None else None,
        "z": z,
        "level": "hot" if z is not None and abs(z) >= Z_HOT else "calm" if z is None or abs(z) < Z_CALM else "",
        "side": ("현물 매도 · 선물 매수" if dev < 0 else "현물 매수 · 선물 매도") if dev is not None else "",
        "no_fut_trade": vol_after == 0,
        "rank": 1000 + abs(z) if z is not None else abs(dev) if dev is not None else -1,
        "trend": sorted(trend.items()),
    }


def status_of(q, price):
    if not q:
        return ""
    if q.get("halted"):
        return "거래정지"
    if q.get("market_warn"):
        return "투자" + q["market_warn"]
    if q.get("vi"):
        return "VI"
    if price and q.get("limit_up") and price >= q["limit_up"]:
        return "상한가"
    if price and q.get("limit_down") and price <= q["limit_down"]:
        return "하한가"
    return ""


def _mark(rows):
    keep = ("spot", "fut", "fut_vol", "basis_pct", "mean_pct", "dev_pct", "imb_eok", "expected_qty", "bid_qty", "ask_qty",
            "nxt", "nxt_gap_pct")
    return {r["code"]: {k: r[k] for k in keep} for r in rows if r["spot"]}


class Monitor:
    def __init__(self):
        self.day = None
        self.special_day = None

    def subscribe(self, today):
        """오늘 쓸 월물로 시세 허브를 구독한다 — 현물·선물을 같은 순서로 넘겨 같은 종목끼리 같은 때 받게."""
        self.futs = front_futures(today)
        watch = universe.stocks() or {}
        self.watch = {c: v for c, v in watch.items() if c in self.futs}
        self.codes = sorted(self.watch)
        self.mktcap = {r["code"]: r.get("mktcap") for r in md_feed.market_snapshot() if r["code"] in self.watch}   # 하루 한 번 — 정렬용
        self.feeds = {md_feed.KRX_STOCK: self.codes, md_feed.KRX_FUT: [self.futs[c]["code"] for c in self.codes],
                      md_feed.KRX_STATUS: self.codes, md_feed.NXT_STOCK: self.codes, md_feed.KRX_BOOK: self.codes}
        md_feed.watch("종가 차익", self.on_tick, self.feeds)

    def new_day(self, now):
        self.day = now.date()
        self.subscribe(self.day)
        self.prev_day, self.prev = previous_log(self.day)
        self.log = load_log(self.day)
        self.log.setdefault("intraday", {})
        self.sampled_vol = {}
        self.regular_rows = []
        self.saved_minute = None
        self.fill_from_bars(now)

    def fill_from_bars(self, now):
        """filled_until 부터 지금(최대 마감 동시호가 시작)까지 빈 분을 1분봉으로 채운다. 이미 있는 분은 그대로 둔다."""
        since, until = self.log.get("filled_until", md_feed.KRX_OPEN), min(now.strftime("%H%M"), md_feed.AUCTION_START)
        if until <= since:
            return
        fcode = {self.futs[c]["code"]: c for c in self.codes}
        spot = md_feed.minute_bars_since(self.codes, since)
        fut = md_feed.minute_bars_since(list(fcode), since, futures=True)
        for fc, bars in fut.items():
            c = fcode[fc]
            closes = {f"{kst(b['dt']):%H%M}": b["close"] for b in spot.get(c, [])}
            series = self.log["intraday"].setdefault(c, {})
            for b in bars:
                hm = f"{kst(b['dt']):%H%M}"
                if b["vol"] > 0 and closes.get(hm) and since <= hm < until:
                    series.setdefault(hm, pct(b["close"], closes[hm], ND))
        self.log["filled_until"] = until
        save_log(self.day, self.log)

    def on_tick(self, phase, now):
        """허브가 한 바퀴 받을 때마다 — 기록·화면 파일을 갱신한다."""
        open_late, close_late = session_delay(now.date())
        if open_late or close_late:   # 장 시간이 다른 날 — 장중 기록(분 단위 HHMM)이 평소 장 시간 기준이라 기록하면 틀림 → 기록 안 하고 화면에 알림
            if self.special_day != now.date():
                live = load_json(CLOSEARB_PATH, {"rows": [], "marks": []})
                live.update(phase=f"특별 장 시간(개장 {open_late}분·마감 {close_late}분 늦음) — 오늘은 기록 안 함",
                            generated_at=stamp(now))
                save_json(CLOSEARB_PATH, live)
                self.special_day = now.date()
            return
        if self.day != now.date():
            self.new_day(now)
        self.krx, self.nxt = md_feed.latest(md_feed.KRX_STOCK), md_feed.latest(md_feed.NXT_STOCK)
        self.fut, self.status, self.book = (md_feed.latest(f) for f in (md_feed.KRX_FUT, md_feed.KRX_STATUS, md_feed.KRX_BOOK))
        marks = self.log["marks"]

        if phase == md_feed.AUCTION and "before_auction" not in marks and self.regular_rows:
            marks["before_auction"] = _mark(self.regular_rows)

        if phase == md_feed.REGULAR:
            for c in self.codes:
                k, f = self.krx.get(c, {}), self.fut.get(self.futs[c]["code"], {})
                if status_of(self.status.get(c), k.get("price")) or not k.get("price") or not f.get("price") \
                        or (f.get("volume") or 0) <= self.sampled_vol.get(c, 0):
                    continue
                self.log["intraday"].setdefault(c, {})[now.strftime("%H%M")] = pct(f["price"], k["price"], ND)
                self.sampled_vol[c] = f["volume"]
            self.log["filled_until"] = now.strftime("%H%M")

        if phase == md_feed.REGULAR and self.prev is not None:
            nxt_rec = self.prev.setdefault("next", {})
            if "krx_open" not in nxt_rec and now.strftime("%H%M") >= NEXT_OPEN_AT:
                nxt_rec["krx_open"] = {c: self.krx[c].get("open") for c in self.codes if c in self.krx}
                save_log(self.prev_day, self.prev)
            if "krx_check" not in nxt_rec and now.strftime("%H%M") >= NEXT_CHECK_AT:
                nxt_rec["krx_check"] = {c: self.krx[c].get("price") for c in self.codes if c in self.krx}
                save_log(self.prev_day, self.prev)

        rows = self.rows(phase)
        if phase == md_feed.REGULAR:
            self.regular_rows = rows
        if phase == md_feed.AUCTION:
            marks["auction_last"] = _mark(rows)
        if phase == md_feed.FUT_CLOSE and "krx_close" not in marks:
            marks["krx_close"] = _mark(rows)
        if phase == md_feed.AFTER and "fut_close" not in marks:
            marks["fut_close"] = _mark(rows)
        if phase in (md_feed.FUT_CLOSE, md_feed.AFTER):
            marks["nxt_last"] = {c: self.nxt[c].get("price") for c in self.codes if c in self.nxt}
        if phase != md_feed.PRE and self.saved_minute != now.strftime("%H%M"):
            save_log(self.day, self.log)
            self.saved_minute = now.strftime("%H%M")

        if not self.krx:
            return
        band = lambda vs: [round(q, ND) for q in statistics.quantiles(vs, n=4, method="inclusive")]
        baskets = {}
        for mkt in ("KOSPI", "KOSDAQ"):
            rs = [r for r in rows if r["market"] == mkt and r["mean_pct"] is not None]
            buckets = {}
            for r in rs:
                for m, v in r["trend"]:
                    buckets.setdefault(m, []).append(v - r["mean_pct"])
            devs = [r["dev_pct"] for r in rs if r["dev_pct"] is not None]
            chg = [r["chg10_pct"] for r in rs if r["chg10_pct"] is not None]
            baskets[mkt] = {
                "n": len(rs),
                "trend": [[m] + band(vs) for m, vs in sorted(buckets.items()) if len(vs) >= BASKET_MIN],
                "now": band(devs) if len(devs) >= BASKET_MIN else None,
                "up10": sum(c >= BASKET_FLAT for c in chg), "down10": sum(c <= -BASKET_FLAT for c in chg),
            }
        save_json(CLOSEARB_PATH, {
            "generated_at": stamp(now),
            "phase": phase,
            "marks": [label for k, label in MARKS.items() if k in marks],
            "prev_date": iso(self.prev_day) or None,
            "baskets": baskets,
            "z_hot": Z_HOT, "end_min": TREND_END, "now_min": min(TREND_END, _minute_of_day(now.strftime("%H%M"))),
            "hot_codes": [r["code"] for r in sorted((r for r in rows if r["level"] == "hot"), key=lambda r: -abs(r["z"]))[:HOT_MAX]],
            "rows": rows,
        })

    def rows(self, phase):
        before_auction = self.log["marks"].get("before_auction", {})
        out = []
        for c in self.codes:
            krx, fut = self.krx.get(c, {}), self.fut.get(self.futs[c]["code"], {})
            out.append(build_row(c, self.watch[c], self.futs[c], krx, self.book.get(c, {}), self.nxt.get(c, {}), fut,
                                 status_of(self.status.get(c), krx.get("price")), phase, before_auction.get(c),
                                 self.log["intraday"].get(c), self.mktcap.get(c)))
        return out


def start():
    Monitor().subscribe(datetime.date.today())


if __name__ == "__main__":
    now = datetime.datetime.now()
    phase = md_feed.phase_of(now)
    print(f"phase={phase}")
    if phase:
        m = Monitor()
        m.subscribe(now.date())
        md_feed.get("종가 차익(1회)", m.feeds)
        m.on_tick(phase, now)
    d = load_json(CLOSEARB_PATH, {})
    print(d["generated_at"], d["phase"], len(d["rows"]), "종목")
