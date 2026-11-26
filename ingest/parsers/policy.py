# -*- coding: utf-8 -*-
"""회사가 밝힌 배당 정책 숫자 → 정책 줄. 원문 한 번에 여섯 단계, 단계마다 한 함수:
  1 마디 나누기(clauses)  2 숫자 찾기(numbers)  3 종류 정하기(kind_of)  4 범위 말(bounds)  5 기간(period)  6 앞으로 할 일만(intent)
정책 줄 = {kind, slot, lo, hi, fy [첫, 끝], per, base, text}
  kind: dps 주당(원) · cash 현금배당 총액(원) · payout 배당성향(%) · return 총주주환원율(%, 자사주 포함)
        budget 총주주환원 금액(원, 자사주 포함) · buyback 자사주 매입 금액(원)
  slot: 연간·결산·중간·분기 · lo·hi: 하한·상한(같으면 그 값, 모르면 None) · fy: 기간(모르면 None — 공시한 해부터, facts 가 채움)
  per: 해 = 해마다 이 금액 · 기간 = 기간 전체 합("4년간 2.4조원", facts 가 해마다로 나눔) · years: 시작 해 없이 길이만("3년간", facts 가 공시한 해부터) · base: 연결·별도·None(안 밝힘, facts 가 회사 단위로)
TSR(총주주수익률)은 주가 상승까지 넣은 수익률이라 배당 정책이 아님 — 안 읽는다."""
import re

# ── 1 마디: 문장 끝·글머리표·번호마다 끊는다. 한 마디 = 숫자 하나의 뜻을 정하는 범위 ──
CUT = re.compile(r"(?<=[다요음함임됨])\s*\.(?!\d)|\.\s|\s[-–•□■○◦▶▲※*]\s|[①-⑳]|\s\(\d\)\s|\s\d\)\s|;|\n")

def clauses(t):
    """[(시작, 끝)] — 원문을 마디로"""
    cuts = [0] + [m.start() for m in CUT.finditer(t)] + [len(t)]
    return [(a, b) for a, b in zip(cuts, cuts[1:]) if b > a]

# ── 2 숫자: 비율(%), 금액(조·억), 주당(원) ──
PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%?\s*[~∼\-–]\s*(\d{1,3}(?:\.\d+)?)\s*%|(\d{1,3}(?:\.\d+)?)\s*%")
BIG_WON = re.compile(r"(?<![\d,.])(\d{1,3}(?:,\d{3}){3,})\s*원")   # "75,882,401,600원" — 총액
MONEY = re.compile(r"(?<![\d,.])(\d[\d,]*(?:\.\d+)?)\s*(조(?=\s*(?:원|\d[\d,]*\s*억|규모|이상|이내|이하|수준|가량|내외|\+|$))|천\s*억|억)\s*(?:(\d[\d,]*)\s*억)?\s*원?")   # 조(兆)는 뒤에 원·규모 등이 붙을 때만 — "정관 제54조, 55조"는 조항
WON = re.compile(r"(?<![\d,.])(\d{1,3}(?:,\d{3}){1,2}|\d{2,7})\s*원(?!\s*(?:이상의\s*)?(?:감액|적립))")
UNIT = {"조": 1e12, "천억": 1e11, "억": 1e8}

def numbers(t, a, b):
    """마디 [a, b) 안의 숫자 → [(종류 무리 pct·money·won, 시작, 끝, 값, 둘째 값)]. 연도·날짜 안의 숫자는 아님"""
    s, out, taken = t[a:b], [], []
    for m in PCT.finditer(s):
        lo, hi = (float(m[1]), float(m[2])) if m[1] else (float(m[3]), None)
        out.append(("pct", a + m.start(), a + m.end(), lo, hi))
        taken.append(m.span())
    for m in MONEY.finditer(s):
        v = float(m[1].replace(",", "")) * UNIT[re.sub(r"\s", "", m[2])] + (float(m[3].replace(",", "")) * 1e8 if m[3] else 0)
        out.append(("money", a + m.start(), a + m.end(), v, None))
        taken.append(m.span())
    for m in BIG_WON.finditer(s):
        out.append(("money", a + m.start(), a + m.end(), float(m[1].replace(",", "")), None))
        taken.append(m.span())
    for m in WON.finditer(s):
        if not any(x <= m.start() < y for x, y in taken) and not re.match(r"\s*(?:년|월|일|주|억|조)", s[m.end(1): m.end(1) + 2]):
            out.append(("won", a + m.start(), a + m.end(), float(m[1].replace(",", "")), None))
    return sorted(out, key=lambda x: x[1])

# ── 3 종류: 숫자에서 가장 가까운 종류 말(앞 40자, 뒤 25자, 같은 마디 안) ──
KIND_WORDS = {
    "pct": [("payout", r"배당\s*성향"), ("return", r"(?:총\s*)?주주\s*환원\s*(?:[율률]|비중|비율)|환원\s*[율률]"), ("skip", r"TSR|총\s*주주\s*수익률|ROE|PBR|준수율|비율|수익률|증가율|성장률|이익률|지분|잉여\s*현금|FCF|배당\s*[률율]|시가\s*배당|주식\s*수|발행\s*주식"),
            ("ni", r"순이익|순익")],
    "money": [("buyback", r"(?:자사주|자기\s*주식)\S{0,4}\s*(?:매입|취득)|(?:매입|취득)\S{0,4}\s*(?:자사주|자기\s*주식)|^\s*원?\s*(?:규모의?\s*)?(?:추가\s*)?(?:매입|취득)(?!\s*가)"), ("skip", r"기부|보증|담보"), ("budget", r"(?:총\s*)?주주\s*환원|환원\s*(?:재원|규모)|현금\s*환원"), ("budget_income", r"배당\s*수입"),
              ("cash", r"현금\s*배당|배당\s*(?:금\s*)?총액|배당\s*(?:규모|재원)|배당금|배당"), ("skip", r"매출|영업\s*이익|순이익|투자|자본\s*준비금|이익\s*잉여금|차입|부채|자산|매각|인수|지분|유입|전입")],
    "won": [("dps", r"주당|DPS|1주|배당금|배당"), ("skip", r"가격|주가|시가|평균가|취득가|매입가|공모가|목표가")],   # 액면가는 policy_rows 가 따로(뒤 "액면배당률"은 배당금 설명)
}
RETURN_HINT = re.compile(r"자기\s*주식|자사주|주주\s*환원|환원\s*(?:재원|규모)|현금\s*배당\s*(?:또는|및|과|와|,)\s*(?:자기|자사)")

HEAD = re.compile(r"\s*(?:원\s*)?(?:규모|상당|수준|이상|이하|가량|내외)?\s*의\s")   # "1조 원 규모의 자기주식", "9.8조원 수준의 정규배당" — 뒤 말이 이 금액의 이름

def kind_of(t, a, b, n):
    """숫자 n 의 정책 종류 — 가장 가까운 종류 말(괄호 설명은 빼고 잼, "N원 규모의 X"·"X를 … N원으로 산정"이면 X). 없으면 None.
    "순이익의 N%"는 그 숫자의 말로 — 숫자 뒤 첫 말(배당 / 자사주·주주환원), 없으면 마디 처음부터 숫자까지 더 가까운 말"""
    group, s, e = n[0], n[1], n[2]
    head = group == "money" and HEAD.match(t, e)
    flat = lambda x: re.sub(r"\([^()]*\)", "", x)   # 거리는 괄호 설명("순이익(일회성 비경상이익 제외)의 25%")을 빼고 잼
    before, after = flat(t[max(a, s - 100): s])[-40:], flat(t[e: min(b, e + 60)])[:25]
    near = []
    for kind, rx in KIND_WORDS[group]:
        near += [(len(before) - m.end() + (40 if head else 0), kind) for m in re.finditer(rx, before)]   # 보통은 앞 말이 이름(말 끝에서 숫자까지)
        near += [(m.start() + (0 if head or m.start() <= 3 else 15), kind) for m in re.finditer(rx, after)]   # 뒤 말은 "규모의 X"이거나 숫자에 바로 붙으면("500억원 추가 매입") 앞 말만큼 가깝게
    w = t[max(a, s - 80): s]
    obj = [m for m in re.finditer(r"(?:을|를)\s", w) if len(w) - m.end() <= 45] if re.match(r"\s*원?\s*으로\s*(?:산정|설정|결정|책정|정하|확대|상향|증액)", t[e: e + 14]) else []
    name = " ".join(w[: obj[-1].start()].split()[-4:]) if obj else ""   # "재원 규모를 … 약 4천억원으로 산정" — 금액의 이름은 숫자 바로 앞 목적어
    named = [k for k, rx in KIND_WORDS[group] if re.search(rx, name)] if name else []
    if not near and not named:
        return None
    kind = named[0] if named else min(near)[1]
    if kind == "dps" and not re.search(r"배당|DPS", t[max(a, s - 40): min(b, e + 15)]):
        return None   # "주당 40,350원"처럼 배당 말이 없는 주당 금액(주가·취득가)
    if kind == "ni":   # "순이익의 N%" — 그 숫자의 말: 뒤("…를 현금배당" / "… 현금배당 또는 자기주식", "…를 주주환원") > 앞 가까운 말("배당 규모는" / "주주환원 재원")
        tail = after[:20]
        first = min([(m.start(), "return") for m in RETURN_HINT.finditer(tail)] + [(m.start(), "payout") for m in re.finditer(r"배당", tail)], default=None)
        if first:   # 숫자 뒤 첫 말("50%배당 + 9.5% 자사주"는 배당) — "주주에게 환원"은 배당만 말할 때도 써서 환원만으로는 총환원 아님
            return first[1]
        lead = flat(t[a: s])   # 마디 처음부터 숫자까지("2. 주주환원 규모 : … 순이익의 30% 이상")
        ret, div = [m.end() for m in RETURN_HINT.finditer(lead)], [m.end() for m in re.finditer(r"배당(?!\s*성향)", lead)]
        return "return" if ret and (not div or ret[-1] > div[-1]) else "payout"
    return None if kind == "skip" else kind

# ── 4 범위 말: 숫자 바로 앞·뒤 ──
def bounds(t, n):
    """(하한, 상한) 또는 None(바뀌기 전 값 "A원에서", "1,000원 → 1,500원"의 앞)"""
    _, s, e, v, v2 = n
    before, after = t[max(0, s - 20): s], t[e: e + 16]
    if re.match(r"\s*%?\s*대?\s*(?:에서|→|->|⇒)", after) or re.match(r"\s*(?:이상\s*)?(?:확대|증액|인상|상향|증가|감소|축소)", after) or re.search(r"대비\s*$", before):
        return None   # 바뀌기 전 값, 늘린 폭("500원 확대")
    if v2 is not None:
        return v or None, v2   # "0~20%"의 0은 하한 없음
    if re.match(r"\s*(?:이상|\+|α|을?\s*(?:기본|최소)|으로\s*(?:상향|확대|증액|인상))", after) or re.search(r"최소(?!화)|최저|기본\s*배당", t[max(0, s - 60): s]) or re.match(r"\s*원?\s*(?:수준의?\s*)?안정\s*배당", after) \
            or re.search(r"(?:최소|최저)\s*배당금?\s*(?:설정)?\s*[:：]", t[max(0, s - 80): s]):   # "최저배당금 설정 : 주당 2,500원 - 결산배당 : 주당 1,000원"
        return v, None
    if re.match(r"\s*%?\s*대", after) and n[0] == "pct":
        return v, v + 9   # "10%대" = 10~19%
    if re.match(r"\s*(?:이내|이하|內|한도)", after) or re.search(r"최대[^.,%\d]{0,12}$", before):   # "20%까지 상향"은 목표라 상한 아님
        return None, v
    return v, v

# ── 5 기간 ──
YEAR = r"(FY\s*(?:20)?\d\d|20\d\d|['’‘]\d\d)"   # 2026 · '26 · FY2026 · FY26
FY_RANGE = re.compile(YEAR + r"\s*년?\s*(?:사업\s*연도|사업년도|회계\s*연도|회계년도)?\s*[~∼\-–]\s*(" + YEAR[1:-1] + r"|\d\d(?!\s*[\d%.,]))\s*년?")   # "2025~27" — "2025~30%"는 아님
FY_FROM = re.compile(r"(20\d\d)\s*(?:년|사업\s*연도|사업년도)\s*\S{0,6}\s*부터\s*(?:향후\s*(\d)\s*개?년|(20\d\d)\s*년)")
FY_UNTIL = re.compile(r"(20\d\d)\s*년\s*까지")
FY_SINCE = re.compile(YEAR + r"\s*(?:년|사업\s*연도|사업년도|회계\s*연도|회계년도)\s*(?:부터|이후)")
FY_ONE = re.compile(r"(?:" + YEAR + r"\s*(?:년|사업\s*연도|사업년도|회계\s*연도|회계년도)|(FY\s*(?:20)?\d\d))(\s*이후)?")   # "2027년", "FY2027"
SPAN = re.compile(r"(\d)\s*(?:년간|개년|개\s*사업연도)|향후\s*(\d)\s*년")

def _yy(s):
    d = re.sub(r"\D", "", s)
    return int(d) if len(d) == 4 else 2000 + int(d)

def years(t):
    """원문의 기간 표기 [(시작, 끝, [첫, 끝])] — 범위 안의 한 해, 날짜("2023년 11월", "2025-11-19")는 빼고"""
    out = []
    for rx, span in ((FY_RANGE, lambda m: [_yy(m[1]), _yy(m[2])]), (FY_FROM, lambda m: [int(m[1]), int(m[1]) + int(m[2]) - 1 if m[2] else int(m[3])]),
                     (FY_UNTIL, lambda m: [None, int(m[1])]), (FY_SINCE, lambda m: [_yy(m[1]), None]), (FY_ONE, lambda m: [_yy(m[1] or m[2]), None if m[3] else _yy(m[1] or m[2])])):
        for m in rx.finditer(t):
            fy = span(m)
            if fy[0] and fy[1] and not 0 <= fy[1] - fy[0] <= 10:
                continue   # "2025-11-19" 같은 날짜는 기간이 아님
            if not any(x <= m.start() < y for x, y, _ in out) and not re.match(r"\s*\d{1,2}\s*(?:월|분기\s*실적)", t[m.end(): m.end() + 10]):   # "2023년 11월 17일", "2022년 4분기 실적발표"는 날짜
                out.append((m.start(), m.end(), fy))
    return sorted(out)

def period(t, a, b, n, ys, head, sec):
    """숫자 n 의 기간 — 숫자 바로 뒤 괄호 > 마디 안 앞쪽 가장 가까운 표기 > 마디 안 뒤쪽 > 다음 마디 "적용시기" > 마디 제목의 기간 > 같은 □ 단락(sec 부터) 앞 마디들의 가장 가까운 계획 기간.
    다른 □ 단락의 기간은 안 물려받음("□ 정책 기간(2025~2027) …" 다음 "□ 조기 환원 - 40조원 자사주 취득 결정"은 그해 한 번)
    금액 바로 앞에 붙은 한 해("'26년 200억원")는 그 금액의 해"""
    _, s, e, _, _ = n
    after = next((y for x, _, y in ys if e <= x < b and re.fullmatch(r"\s*\(\s*", t[e: x])), None)   # "30%+ ('25~'27)"
    inside = [y for x, z, y in ys if a <= x and z <= s]
    later = [y for x, _, y in ys if e <= x < b and y[0] != y[1]]   # 숫자 뒤 한 해 표기는 대개 다른 말의 해 — 여러 해·부터·까지만
    applied = next((y for x, _, y in ys if b <= x < b + 40 and re.search(r"적용", t[b: x])), None)   # 다음 마디 "적용시기 : '26사업년도 이후"
    above = [y for x, _, y in ys if sec <= x < a and y[0] != y[1]]   # 위에서 물려받는 건 이 □ 단락 안의 계획 기간(여러 해·부터·까지)만 — 한 해 표기는 대개 실적 이름표(계획 제목의 한 해까지 물려받으면 판독자 대조 290→270)
    titled = [y for x, _, y in ys if head[0] <= x < head[1]]   # 이 마디 제목의 기간("2025년 사업연도 현금배당(계획) 관련" 아래 "1) 1주당 배당금: 800원")
    return after or (inside[-1] if inside else None) or (later[0] if later else None) or applied or (titled[-1] if titled else None) or (above[-1] if above else [None, None])

# ── 6 앞으로 할 일: 정책은 앞으로 할 배당 — 지난 실적·옛 정책·설명 표는 아님 ──
DONE = r"(?:했|하였|되었|됐|하여\s*왔|해\s*왔|완료|함)"
PAST = re.compile(r"(?:지급|실시|시행|집행|매입|취득|소각|달성|기록|이행|배당|환원|유지|증가|확대|상향|결의|체결|완료)\s*(?:을|를|이|가)?\s*(?:하|되)?\s*" + DONE
                  + r"|(?:이었|(?<!하)였)(?:습니다|으며|고|다)|진행\s*중|이행\s*완료|인정\s*받|선정|수상")   # 해낸 일(실행 동사 + 지난 꼴) — "산정·결정·수립·발표하였습니다"는 앞으로 할 일을 정한 것이라 아님
FUTURE = re.compile(r"예정|계획|목표|추진|할\s*것|하겠|방침|검토|지향|도입|추구|원칙|할\s*수\s*있|결정(?!\s*(?:하였|했|되었|됐))")   # 앞으로 할 일을 말하는 술어 — 범위 말(이상·수준·상향)과 다름
FORWARD = re.compile(FUTURE.pattern + r"|정책|유지|확대|상향|이상|이내|한도|수준|최소|최저|까지|부터|매년|향후|중장기")   # 마디가 정책을 말하나(술어 없는 글머리표)
HISTORY = re.compile(r"제\s*\d{1,3}\s*기|최근\s*\d\s*개?\s*(?:년|사업)|과거|지난|연평균|(?:실시|지급|결의|완료|시행|확정|발표)(?:한|된|했던|하였던)\s|기\s*(?:지급|시행|공시)|기존|종전|전년|작년|에\s*따르면")   # 숫자 바로 앞 — 지난 일·남의 공시 인용
REPORT = re.compile(r"이행\s*(?:현황|실적|내역)|실적|현황")   # 위 제목이 해낸 일을 보고하는 단락
OLD = re.compile(r"기존\s*(?:배당\s*)?정책|기존\s*[):]|이전\s*정책|종전|에서\s*제외|가결|부결|Approved|의안|승인의\s*건|결의일|배당\s*결정\s*[:'’」]")   # 마디 — 옛 정책·계산 제외 금액·주총 결과 표

def intent(t, a, b, n, head):
    """숫자 n 이 앞으로 할 배당을 말하나 — 숫자 바로 앞이 지난 일(HISTORY)이면 아님, 숫자 뒤 술어가 지난 일이 먼저면 아님, 앞으로 할 술어면 맞음.
    해낸 일을 보고하는 제목("이행 현황") 아래는 앞으로 할 술어가 있어야 함. 술어가 없으면(글머리표 명사구) 마디나 제목(head: "[주주환원 정책]",
    "현금배당(계획) 관련")에 정책 말(FORWARD)이 있나. 옛 정책·주총 결과 마디는 아님"""
    if OLD.search(t[a:b]) or HISTORY.search(t[max(a, n[1] - 45): n[1]]):
        return False
    tail = t[n[2]: b]
    p, f = PAST.search(tail), FUTURE.search(tail)
    if p and (not f or p.start() < f.start()) or PAST.search(t[a: n[1]]) and not FUTURE.search(t[a: b]):
        return False   # 숫자 뒤 술어가 지난 일, 또는 앞에서 지난 일을 말하고("2,300원을 지급하였으며, 이는 배당성향 25%") 앞으로 할 말이 없음
    if REPORT.search(head) and not f:
        return False
    return bool(f or FORWARD.search(t[a: n[1]]) or FORWARD.search(tail) or FORWARD.search(head) and not PAST.search(head))

SLOT = re.compile(r"(결산|중간|반기|분기)\s*배당")

def _slot(s):
    """주당 금액 바로 앞의 배당 종류 — 여러 종류를 묶어 말하면("분기 및 결산배당을 포함한") 연간"""
    if re.search(r"매\s*분기|분기\s*마다|분기\s*별", s):
        return "분기"
    m = list(SLOT.finditer(s))
    if not m or "," in s[m[-1].end():] or re.search(r"(?:분기|중간|반기)\s*(?:배당)?\s*(?:및|과|와|·|\+|/)\s*(?:결산|분기|중간|반기)|포함", s):
        return "연간"
    return {"반기": "중간"}.get(m[-1][1], m[-1][1])

LEVEL1, LEVEL2 = re.compile(r"\s*[□■\[【]"), re.compile(r"\s*(?:[①-⑳]|\d{1,2}\)|\(\d{1,2}\))")   # 마디 단계 1: □·[ ] · 2: 1)·(1)·①

def policy_rows(t):
    """원문 → 정책 줄 목록(원문 순서)"""
    t = re.sub(r"\s+", " ", t)
    ys, rows, heads, sec = years(t), [], {}, 0   # heads: 단계 → (시작, 끝) 그 단계의 마지막 제목 마디 · sec: 지금 □ 단락 시작
    for a, b in clauses(t):
        c = t[a:b]
        found = [n for n in numbers(t, a, b) if not any(x <= n[1] < z for x, z, _ in ys if a <= x < b)]
        lv = 1 if LEVEL1.match(c) or not found and not re.match(r"\s*[-–•·]", c) else 2 if LEVEL2.match(c) else 3   # 숫자 없는 제목 줄도 1, "-" 글머리표·숫자 있는 문장은 3
        ha, hb = next((heads[k] for k in range(lv - 1, 0, -1) if k in heads), (0, 0))
        head = t[ha:hb]
        split = re.search(r"분할", c) and [n for n in found if n[0] == "money"][:1]   # "1,100억원 : '26년 400억원, '27년 700억원 분할 실행" — 뒤 금액들은 앞 금액의 나눔
        seen = []   # 이 마디에서 읽은 (숫자, 종류)
        for n in found:
            if not intent(t, a, b, n, head) or split and n[0] == "money" and n is not split[0]:
                continue
            kind = kind_of(t, a, b, n)
            if not kind:
                continue
            slot = _slot(t[max(a, n[1] - 40): n[1]]) if kind == "dps" else "연간"
            if any(k == (kind, slot) and ("(" in t[m[2]: n[1]] and ")" not in t[m[2]: n[1]] or re.search(r"[:：]", t[m[2]: n[1]])) for m, k in seen):
                continue   # 같은 종류·칸 금액 뒤 괄호 안은 그 금액의 나눔("주당 20,000원 (실적 반영 10,000원)"), 쌍점 뒤는 목표의 실적("500원 이상 유지 : 배당금 680원")
                           # — 다른 종류·칸("3.7조원(배당 2.4조원)", "1,300원(중간배당 600원)")은 읽음
            seen.append((n, (kind, slot)))
            lohi = bounds(t, n)
            if not lohi or kind == "dps" and re.search(r"액면|주식\s*수|발행|주당\s*금액|1주의\s*금액", t[max(a, n[1] - 30): n[2]]):
                continue
            lo, hi = lohi
            if lo == hi and (re.match(r"\s*(?:원|%)?\s*(?:입니다|이며|이고|이었|으로\s*(?:결정|확정|책정))", t[n[2]: n[2] + 14])
                             or n[0] != "pct" and re.match(r"\s*(?:을|를)\s*(?:현금\s*)?(?:배당|지급)", t[n[2]: n[2] + 12]) and not FUTURE.search(t[n[2]: b])
                             or n[0] == "pct" and lo % 1 and not FUTURE.search(t[n[2]: b])):
                continue   # 정한 값·실적("1주당 배당금은 1,040원입니다", "주당 750원을 지급", "연결현금배당성향: 51.3%") — 정책은 범위 말이나 둥근 목표
            fy = period(t, a, b, n, ys, (ha, hb), sec)
            if kind == "budget_income":   # "배당수입 약 6.2조원 … 배당수입의 N%" — 그 비율만큼이 총환원
                rate = list(re.finditer(r"(?<!특별)배당\s*수입의\s*(\d{1,3})\s*%\s*(이상)?", t[n[2]: n[2] + 400]))
                if not rate:
                    continue
                k = float(rate[-1][1]) / 100
                kind, lo, hi = "budget", lo * k if lo else None, None if rate[-1][2] else hi * k if hi else None
            span = SPAN.search(t[max(a, n[1] - 30): n[1]])
            length = int(span[1] or span[2]) if span else None
            if length and fy[0] and not fy[1]:
                fy = [fy[0], fy[0] + length - 1]
            yearly = re.search(r"연간|매년|해마다|연\s|각\s*(?:연도|사업연도)|연도별", t[max(a, n[1] - 30): n[1]])
            whole = kind in ("cash", "budget", "buyback") and (fy[0] and fy[1] and fy[1] > fy[0] or length and length > 1) and not yearly   # 여러 해에 걸친 금액은 "연간·매년"이 없으면 기간 합("4개년 현금환원 3.7조원")
            row = {"kind": kind, "slot": slot, "lo": lo, "hi": hi, "fy": fy,
                   "per": "기간" if whole else "해", **({"years": length} if length and not fy[0] else {}), "text": t[max(a, n[1] - 40): min(b, n[2] + 20)].strip()}
            if kind in ("payout", "return"):
                row["base"] = next((x for x, rx in (("별도", r"별도|개별"), ("연결", r"연결")) if re.search(rx, c)), None)
            rows.append(row)
        if re.match(r"\s*[□■]", c):
            sec = a
        if lv < 3 and re.search(r"[가-힣A-Za-z]", c):   # 이 마디를 다 읽은 뒤 아래 단계 마디들의 제목으로
            heads = {k: v for k, v in heads.items() if k < lv} | {lv: (a, b)}
    return rows
