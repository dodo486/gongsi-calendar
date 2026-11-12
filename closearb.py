# -*- coding: utf-8 -*-
"""종가 차익 — 마감 동시호가 수급으로 베이시스가 오늘 장중 평균과 달라진 종목.

대상: 감시목록(코스피200+코스닥150) 중 주식선물이 있는 종목, 선물은 만기가 오늘 이후인 가장 가까운 월물.

베이시스 % = (선물가 − 현물 기준가) / 현물 기준가. 현물 기준가는 동시호가 중엔 예상체결가, 그 밖엔 KRX 현재가
(15:30 이후 = 종가). 장중 베이시스는 정규장 1분마다 하나 — 선물이 그 분에 체결된 분만 쓴다. 폴러가 늦게 켜졌거나
껐다 켜면 빈 시간대를 KIS 1분봉(선물 종가 vs 현물 종가)으로 채우고, 그 뒤로는 실시간 값으로 잇는다.
괴리 = 지금 − 장중 평균, z = 괴리 / 장중 표준편차. 화면엔 장중 추이(5분 간격)를 같이 그린다.

시간대별로 받는 것 (거래일만)
  NXT 프리마켓   08:00~08:50  NXT 호가
  정규장         09:00~15:20  KRX 호가 + 선물
  마감 동시호가  15:20~15:31  KRX 호가(예상체결·잔량) + 선물  (랜덤엔드 30초까지)
  선물 마감까지  15:31~15:46  KRX 종가(한 번) + NXT 호가 + 선물
  NXT 애프터마켓 15:46~20:00  선물 종가(한 번) + NXT 호가
그 밖 시간엔 받지 않고 마지막 값을 그대로 둔다 — 다음 장 첫 시세가 들어오면 바뀐다.

기록 (data/closearb/YYYYMMDD.json)
  intraday    {종목: {HHMM: 베이시스 %}}   filled_until  장중 베이시스를 채운 마지막 HHMM
  marks.1520  동시호가 직전   marks.1530  KRX 종가   marks.1545  선물 종가   marks.nxt  NXT 마지막
  next.open / next.0930  다음 거래일 KRX 시가·09:30 가격 (종가가 얼마나 돌아왔나)

  python closearb.py   # 1회 받아서 data/closearb.json 갱신
"""
import os, json, time, datetime, statistics
from concurrent.futures import ThreadPoolExecutor
import md_feed
from fetch import DATA_DIR, save_json, load_watchlist
from expiries import second_thursday, prev_business_day
from krx_cal import holiday_dates

LIVE_PATH = os.path.join(DATA_DIR, "closearb.json")
LOG_DIR = os.path.join(DATA_DIR, "closearb")

PHASES = [
    ("NXT 프리마켓", "0800", "0850"),
    ("정규장", "0900", "1520"),
    ("마감 동시호가", "1520", "1531"),
    ("선물 마감까지", "1531", "1546"),
    ("NXT 애프터마켓", "1546", "2000"),
]
NXT_ONLY_SECONDS = 300   # NXT 호가만 받는 시간대의 주기
STATUS_SECONDS = 300     # 거래정지·투자경고·상하한가 갱신 주기 (KRX 시간대만)
IDLE_SECONDS = 30
MIN_SAMPLES = 20         # 장중 표본(분)이 이보다 적으면 z 를 내지 않는다
TREND_MINUTES = 5        # 화면 추이 간격
BASKET_MIN = 5           # 바스켓 추이 한 칸에 필요한 종목 수
BASKET_FLAT = 0.05       # 10분 변화가 이 %p 안이면 오름·내림에 안 센다


def phase_of(now):
    if now.weekday() >= 5 or now.date() in holiday_dates([now.year]):
        return None
    t = now.strftime("%H%M")
    for name, start, end in PHASES:
        if start <= t < end:
            return name
    return None


def front_futures(today):
    """{기초 종목코드: {code, month, multiplier}} — 만기일이 오늘 이후인 가장 가까운 월물.
    만기일 당일 월물은 15:20에 거래가 끝나 종가 차익에 못 쓰므로 다음 월물로 넘긴다."""
    hol = holiday_dates([today.year, today.year + 1])
    out = {}
    for r in md_feed.stock_futures():
        y, m = int(r["month"][:4]), int(r["month"][4:])
        if prev_business_day(second_thursday(y, m), hol) <= today:
            continue
        if r["underlying"] not in out or r["month"] < out[r["underlying"]]["month"]:
            out[r["underlying"]] = r
    return out


def _log_path(d):
    return os.path.join(LOG_DIR, d.strftime("%Y%m%d") + ".json")


def load_log(d):
    try:
        return json.load(open(_log_path(d), encoding="utf-8"))
    except (OSError, ValueError):
        return {"date": d.strftime("%Y-%m-%d"), "intraday": {}, "marks": {}, "next": {}}


def save_log(d, log):
    os.makedirs(LOG_DIR, exist_ok=True)
    save_json(_log_path(d), log)


def previous_log(today):
    """오늘 전 가장 최근 기록 (날짜, 기록) / 없으면 (None, None)."""
    try:
        names = sorted(n for n in os.listdir(LOG_DIR) if n.endswith(".json") and n[:8] < today.strftime("%Y%m%d"))
    except OSError:
        return None, None
    if not names:
        return None, None
    d = datetime.datetime.strptime(names[-1][:8], "%Y%m%d").date()
    return d, load_log(d)


def _pct(a, b):
    return round((a - b) / b * 100, 3) if a and b else None


def _minute_of_day(hhmm):
    return (int(hhmm[:2]) - 9) * 60 + int(hhmm[2:])


def build_row(code, stock, fut_info, krx, nxt, fut, status, phase, mark1520, series):
    auction = phase == "마감 동시호가" and krx.get("expected_price")
    spot = krx.get("expected_price") if auction else krx.get("price")
    nxt_mid = (nxt["ask"] + nxt["bid"]) / 2 if nxt.get("ask") and nxt.get("bid") else nxt.get("price")
    basis = _pct(fut.get("price"), spot)
    series = series or {}
    vals = list(series.values())
    n = len(vals)
    mean = statistics.fmean(vals) if vals else None
    std = statistics.pstdev(vals) if vals else 0.0
    dev = round(basis - mean, 3) if basis is not None and mean is not None else None
    ago = (datetime.datetime.now() - datetime.timedelta(minutes=10)).strftime("%H%M")
    before = [hm for hm in series if hm <= ago]
    trend = {}
    for hm in sorted(series):
        trend[_minute_of_day(hm) // TREND_MINUTES * TREND_MINUTES] = round(series[hm], 3)
    vol1520 = (mark1520 or {}).get("fut_vol")
    bid_q, ask_q = krx.get("total_bid_qty"), krx.get("total_ask_qty")
    return {
        "code": code, "name": stock["name"], "market": stock["market"], "fut_code": fut_info["code"], "fut_month": fut_info["month"],
        "multiplier": fut_info["multiplier"],
        "status": status,
        "spot": spot, "spot_kind": "예상" if auction else ("종가" if phase in ("NXT 프리마켓", "선물 마감까지", "NXT 애프터마켓") else "현재"),
        "jump_pct": _pct(spot, (mark1520 or {}).get("spot")),
        "imb_eok": round((bid_q - ask_q) * spot / 1e8, 1) if spot and bid_q is not None and ask_q is not None else None,
        "nxt": nxt_mid,
        "nxt_gap_pct": _pct(spot, nxt_mid),
        "fut": fut.get("price"), "fut_vol": fut.get("volume"),
        "fut_vol_after1520": fut["volume"] - vol1520 if fut.get("volume") is not None and vol1520 is not None else None,
        "basis_pct": basis, "mean_pct": round(mean, 3) if mean is not None else None, "n": n,
        "dev_pct": dev,
        "chg10_pct": round(basis - series[max(before)], 3) if basis is not None and before else None,
        "dev_won": round(dev / 100 * spot * fut_info["multiplier"]) if dev is not None else None,
        "z": round(dev / std, 1) if dev is not None and n >= MIN_SAMPLES and std > 0 else None,
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
    keep = ("spot", "fut", "fut_vol", "basis_pct", "mean_pct", "dev_pct", "imb_eok", "nxt", "nxt_gap_pct")
    return {r["code"]: {k: r[k] for k in keep} for r in rows if r["spot"]}


class Monitor:
    def __init__(self):
        self.day = None
        self.krx, self.nxt, self.fut, self.status = {}, {}, {}, {}
        self.status_at = self.nxt_at = 0.0

    def new_day(self, today):
        self.day = today
        self.futs = front_futures(today)
        watch = load_watchlist() or {}
        self.watch = {c: v for c, v in watch.items() if c in self.futs}
        self.codes = sorted(self.watch)
        self.prev_day, self.prev = previous_log(today)
        self.log = load_log(today)
        self.log.setdefault("intraday", {})
        self.sampled_vol = {}
        self.fill_from_bars(datetime.datetime.now())

    def fill_from_bars(self, now):
        """filled_until 부터 지금(최대 15:20)까지 빈 분을 1분봉으로 채운다. 이미 있는 분은 그대로 둔다."""
        since, until = self.log.get("filled_until", "0900"), min(now.strftime("%H%M"), "1520")
        if until <= since:
            return
        fcode = {self.futs[c]["code"]: c for c in self.codes}
        spot = md_feed.minute_bars_since(self.codes, since)
        fut = md_feed.minute_bars_since(list(fcode), since, futures=True)
        kst = lambda dt: f"{(int(dt[11:13]) + 9) % 24:02d}{dt[14:16]}"
        for fc, bars in fut.items():
            c = fcode[fc]
            closes = {kst(b["dt"]): b["close"] for b in spot.get(c, [])}
            series = self.log["intraday"].setdefault(c, {})
            for b in bars:
                hm = kst(b["dt"])
                if b["vol"] > 0 and closes.get(hm) and since <= hm < until:
                    series.setdefault(hm, (b["close"] - closes[hm]) / closes[hm] * 100)
        self.log["filled_until"] = until
        save_log(self.day, self.log)

    def step(self, now):
        """한 번 받고 기록·화면 파일을 갱신한다. 다음 호출까지 쉴 초를 돌려준다."""
        phase = phase_of(now)
        if phase is None:
            return IDLE_SECONDS
        if self.day != now.date():
            self.new_day(now.date())
        marks = self.log["marks"]
        nxt_only = phase in ("NXT 프리마켓", "NXT 애프터마켓") and "1545" in marks

        if phase == "마감 동시호가" and "1520" not in marks and self.krx:
            marks["1520"] = _mark(self.rows(phase="정규장"))
        krx_due = phase in ("정규장", "마감 동시호가") or (phase == "선물 마감까지" and "1530" not in marks)
        fut_due = phase in ("정규장", "마감 동시호가", "선물 마감까지") or (phase == "NXT 애프터마켓" and "1545" not in marks)
        if krx_due and fut_due:
            with ThreadPoolExecutor(max_workers=8) as pool:
                pairs = pool.map(lambda c: (md_feed.orderbook([c], "KRX"), md_feed.futures_quote([self.futs[c]["code"]])), self.codes)
                for k, f in pairs:
                    self.krx.update(k)
                    self.fut.update(f)
        elif krx_due:
            self.krx.update(md_feed.orderbook(self.codes, "KRX"))
        elif fut_due:
            self.fut.update(md_feed.futures_quote([self.futs[c]["code"] for c in self.codes]))
        if phase in ("NXT 프리마켓", "선물 마감까지", "NXT 애프터마켓") and time.time() - self.nxt_at >= (NXT_ONLY_SECONDS if nxt_only else 0):
            self.nxt.update(md_feed.orderbook(self.codes, "NXT"))
            self.nxt_at = time.time()
        if phase in ("정규장", "마감 동시호가") and time.time() - self.status_at >= STATUS_SECONDS:
            self.status.update(md_feed.quote(self.codes))
            self.status_at = time.time()

        if phase == "정규장":
            for c in self.codes:
                k, f = self.krx.get(c, {}), self.fut.get(self.futs[c]["code"], {})
                if status_of(self.status.get(c), k.get("price")) or not k.get("price") or not f.get("price") \
                        or (f.get("volume") or 0) <= self.sampled_vol.get(c, 0):
                    continue
                self.log["intraday"].setdefault(c, {})[now.strftime("%H%M")] = (f["price"] - k["price"]) / k["price"] * 100
                self.sampled_vol[c] = f["volume"]
            self.log["filled_until"] = now.strftime("%H%M")

        if phase == "정규장" and self.prev is not None:
            nxt_rec = self.prev.setdefault("next", {})
            if "open" not in nxt_rec and now.strftime("%H%M") >= "0901":
                nxt_rec["open"] = {c: self.krx[c].get("open") for c in self.codes if c in self.krx}
                save_log(self.prev_day, self.prev)
            if "0930" not in nxt_rec and now.strftime("%H%M") >= "0930":
                nxt_rec["0930"] = {c: self.krx[c].get("price") for c in self.codes if c in self.krx}
                save_log(self.prev_day, self.prev)

        rows = self.rows(phase)
        if phase == "선물 마감까지" and "1530" not in marks:
            marks["1530"] = _mark(rows)
        if phase == "NXT 애프터마켓" and "1545" not in marks:
            marks["1545"] = _mark(rows)
        if phase in ("선물 마감까지", "NXT 애프터마켓"):
            marks["nxt"] = {c: self.nxt[c].get("price") for c in self.codes if c in self.nxt}
        if phase in ("정규장", "마감 동시호가", "선물 마감까지", "NXT 애프터마켓"):
            save_log(self.day, self.log)

        if not self.krx:
            return IDLE_SECONDS
        band = lambda vs: [round(q, 3) for q in statistics.quantiles(vs, n=4, method="inclusive")]
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
        save_json(LIVE_PATH, {
            "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "phase": phase,
            "marks": sorted(k for k in marks if k != "nxt"),
            "prev_date": self.prev_day.strftime("%Y-%m-%d") if self.prev_day else None,
            "baskets": baskets,
            "rows": rows,
        })
        return NXT_ONLY_SECONDS if nxt_only else 0

    def rows(self, phase):
        mark1520 = self.log["marks"].get("1520", {})
        out = []
        for c in self.codes:
            krx, fut = self.krx.get(c, {}), self.fut.get(self.futs[c]["code"], {})
            out.append(build_row(c, self.watch[c], self.futs[c], krx, self.nxt.get(c, {}), fut,
                                 status_of(self.status.get(c), krx.get("price")), phase, mark1520.get(c),
                                 self.log["intraday"].get(c)))
        return out


def loop():
    m = Monitor()
    while True:
        try:
            wait = m.step(datetime.datetime.now())
        except Exception as ex:
            print(f"  [종가 차익] 갱신 실패: {ex}")
            wait = IDLE_SECONDS
        time.sleep(wait)


if __name__ == "__main__":
    m = Monitor()
    print(f"phase={phase_of(datetime.datetime.now())}")
    m.step(datetime.datetime.now())
    d = json.load(open(LIVE_PATH, encoding="utf-8"))
    print(d["generated_at"], d["phase"], len(d["rows"]), "종목")
