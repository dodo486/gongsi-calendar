# -*- coding: utf-8 -*-
"""배당 공시 파서 — 원문 → 값 (접속 없음). 어떤 공시에 쓰는지는 ingest/registry.py"""
import re, datetime
from core.krx_cal import norm_date
from ingest.parsers.policy import policy_rows

DATE = r"(\d{4}[-.]\d{1,2}[-.]\d{1,2}|\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일)"

def norm_div_type(s):
    """배당구분 정규화 — 문서마다 ':' 포함, 표기 차이 등으로 병합 키가 갈라지는 것 방지"""
    s = re.sub(r"[^가-힣A-Za-z0-9]", "", s or "")
    for kw in ("중간", "분기", "결산", "임시", "특별"):
        if kw in s:
            return kw + "배당"
    return s

def pick(pattern, t):
    """첫 그룹 — 정정 공시는 앞의 정정사항 표에 정정 전 값이 먼저 나오고 본문(정정 후)이 뒤에 오므로 마지막 일치"""
    m = re.findall(pattern, t)
    return (m[-1] if "정정후" in t else m[0]) if m else ""

NOTE_SKIP = re.compile(r"종류주식|시가배당|지급\s*예정|제464조|제165조|감사위원|사외이사|독립이사|감사결과|감사\s*결과|승인\s*(?:과정|결과)|주식수|자기주식|자사주|우선주를\s*의미|미정|참석|변경될\s*수|변동될\s*수")

def parse_decision(t):
    """배당결정 — 금액·기준일·지급일·배당구분·배당금총액(원, 보통+종류) + 「기타 투자판단」 정책 문장(note)·고배당기업 과세특례 요건 충족을 밝혔나(tax_rule)"""
    ps = pick(r"1주당\s*배당금\(원\)\s*보통주식\s*([\d,]+)", t).replace(",", "")
    i = t.rfind("기타 투자판단")
    other = t[i: i + 1500].split("※")[0] if i >= 0 else ""
    return {"per_share": ps if ps not in ("", "-") else "",
            "record_date": norm_date(pick(r"배당기준일\s*" + DATE, t)),
            "pay_date": norm_date(pick(r"배당금지급\s*예정일자\s*" + DATE, t)),
            "div_type": norm_div_type(pick(r"배당구분\s*(\S+)", t)),
            "total": int(pick(r"배당금총액\s*\(원\)\s*([\d,]+)", t).replace(",", "") or 0),
            "note": [x.strip() for x in re.split(r"\s-\s|\s\d\.\s", other)[1:] if len(x.strip()) > 15 and not NOTE_SKIP.search(x)][:4],
            "tax_rule": bool(re.search(r"과세\s*특례|분리\s*과세|고배당\s*기업", other))}

def parse_record(t):
    return {"record_date": norm_date(pick(r"기준일\s*" + DATE, t)),   # "(기준일) 시작일" 은 날짜 아니라 자동 skip
            "div_type": norm_div_type(pick(r"배당구분\s*(\S+)", t))}

def parse_charter(t):
    """주총 「정관상 기준일 설정 및 변경 세부내역」 — 이익배당 기준일 칸 '-' = 정관 고정일 없음(이사회 지정) → O"""
    i = t.find("정관상 기준일 설정 및 변경")
    if i < 0:
        return None
    s = t[i:i + 800]
    m = re.search(r"결산배당\s*(\S+)\s*중간배당\s*(\S+)\s*분기배당\s*1분기\s*(\S+)\s*2분기\s*(\S+)\s*3분기\s*(\S+)", s)
    if m:
        return {"결산": "O" if m[1] == "-" else "X", "분기중간": "O" if all(x == "-" for x in m.groups()[1:]) else "X"}
    m = re.search(r"이익배당\s*\(\s*결산배당\s*\)\s*기준일\s*(\S+)\s*(\S+)", s)
    return {"결산": "O" if m[2] == "-" else "X"} if m else None

def parse_notice(t):
    i = t.find("기준일")
    months = [f"{m[1]}-{int(m[2]):02d}" for m in re.finditer(r"(\d{4})\s*년\s*(\d{1,2})\s*월", t[i:i + 800])] if i >= 0 else []
    fyq = re.search(r"(\d{4})\s*년\s*([1-3])\s*분기\s*배당", t)   # "2026년 2분기 배당여부 및 배당기준일을…" → 그 분기
    typ = ("분기배당" if fyq or re.search(r"분기\s*배당\s*기준일", t) else
           "중간배당" if re.search(r"중간\s*배당\s*기준일", t) else "결산배당")
    subsidiary = bool(re.search(r"종속회사인?\s*(?:\(주\)|㈜)?\s*\S{1,30}?(?:은|는)\s[^.]{0,120}배당(?:안|\s*계획|금|을)", t))   # 자회사가 주어인 배당 계획 공시
    return {"is_div": bool(re.search(r"배당\s*기준일", t)) and not subsidiary, "div_type": typ, "months": months,
            "fyq": [int(fyq[1]), int(fyq[2])] if fyq else None}

def parse_agm(t):
    return {"agm_date": pick(r"주주총회일자\s*(\d{4}-\d{2}-\d{2})", t) or pick(r"일시\s*(\d{4}-\d{2}-\d{2})", t), "charter": parse_charter(t),
            "regular": bool(re.search(r"구분\s*정기주주총회|정기주주총회\s*결과", t)),
            "rejected": bool(re.search(r"특별결의[^.]{0,100}?부결", t))}

def parse_split(t):
    """주식분할·병합결정 — 1주당 가액 전→후 비율 = 주식수 배수(분할 5,000→500 은 10, 병합 500→2,500 은 0.2), 신주 효력발생일"""
    m = re.search(r"1주당\s*가액\s*\(원\)\s*([\d,]+)\s+([\d,]+)", t)
    eff = re.search(r"효력발생일\s*(\d{4}-\d{2}-\d{2})", t)
    if not m or not eff:
        return {}
    before, after = (int(x.replace(",", "")) for x in m.groups())
    return {"factor": before / after, "effective": eff.group(1)} if before and after else {}

def parse_stock_div(t):
    """주식배당결정 — 1주당 배당주식수 r 이면 기준일 다음 날부터 주식 1주가 1+r 주(분할과 같은 모양: factor·effective)"""
    r = re.search(r"1주당\s*배당주식수\s*\(주\)\s*보통주식\s*([\d.]+)", t)
    rec = re.search(r"배당기준일\s*(\d{4}-\d{2}-\d{2})", t)
    if not r or not rec:
        return {}
    eff = datetime.date.fromisoformat(rec[1]) + datetime.timedelta(days=1)
    return {"factor": 1 + float(r[1]), "effective": eff.isoformat()}

YMD = r"(\d{4})\s*(?:년|\.|-)\s*(\d{1,2})\s*(?:월|\.|-)\s*(\d{1,2})\s*일?"

def parse_treasury(t):
    """자기주식 취득결정·취득신탁계약 체결·처분결정 — 배당 받는 주식 변화(delta: 취득 −, 처분 +, 보통주)와 기간 끝(end).
    자사주는 배당을 못 받으므로 사면 줄고 팔면 는다"""
    sold = re.search(r"처분예정주식\s*\(주\)\s*보통주식\s*([\d,]+)", t)
    n = sold or re.search(r"취득예정주식\s*\(주\)\s*보통주식\s*([\d,]+)", t)
    d = re.search(r"(?:처분예정기간|취득예상기간|계약기간)\s*시작일\s*" + YMD + r"\s*종료일\s*" + YMD, t)
    if not n or not d:
        return {}
    k = int(n[1].replace(",", ""))
    return {"delta": k if sold else -k, "end": f"{d[4]}-{int(d[5]):02d}-{int(d[6]):02d}"}

def parse_exdiv(t):
    """거래소 배당락 안내 — 배당락 실시일(보통주), 사유(배당락·중간(분기)배당락·주식배당)"""
    m = re.search(r"실시일.*?보통주식\s*A?\d{6}\s*[\d,]+\s*(\d{4}-\d{2}-\d{2})\s*([^0-9]{1,20}?배당락|\S+)", t)
    return {"ex_date": m[1], "reason": m[2]} if m else {}

def parse_return(t):
    """수시공시의무관련사항(공정공시) — 공시제목, 주주환원·배당 관련이면 내용과 정책 줄(policy_rows)"""
    m = re.search(r"공시제목\s*(.+?)\s*수시공시내용", t)
    subject = m[1].strip() if m else ""
    text = t[m.end(): m.end() + 2500].split("예정 공시 일시")[0].strip() if m and re.search(r"주주환원|배당", subject) else ""
    return {"subject": subject, "text": text, "rows": policy_rows(subject + " " + text) if text else []}

EXCERPT_WORDS = re.compile(r"배당|주주\s*환원|자기\s*주식|자사주")
EXCERPT_PAD, EXCERPT_MAX = 250, 12000
HIGH_DIV = re.compile(r"고배당기업\s*여부\s*(미해당|해당)")
HD_PAYOUT = re.compile(r"직전\s*사업연도\s*\((\d{4})\)\s*배당성향\s*\(%\)\s*(-?[\d.]+)")
HD_TOTAL = re.compile(r"직전\s*사업연도\s*\(\d{4}\)\s*이익배당금액\s*\(원\)\s*([\d,]+)")

def parse_plan(t):
    """밸류업·지배구조보고서 — 배당 절차 문장(날짜용 lines), 배당·환원·자사주 문단 발췌(excerpt), 발췌의 정책 줄(policy_rows), 새 양식 고배당기업 표(정책 줄 high_div)"""
    lines = []
    for m in re.finditer(r"배당\s*기준일|배당\s*절차|선\s*배당|배당액을?\s*먼저", t):
        s = t[max(0, m.start() - 80): m.end() + 120].strip()
        if not any(s[20:60] in x for x in lines):
            lines.append(s)
        if len(lines) >= 3:
            break
    body = re.sub(r"\.?xforms[^{]*\{[^}]*\}\s*", "", t)   # 원문 화면 서식(CSS) 빼고
    spans = []   # 배당·환원 문단 발췌 — 원문 전체 대신 다시 읽을 수 있게(전수 재확인·두 번째 판독자)
    for m in EXCERPT_WORDS.finditer(body):
        a, b = max(0, m.start() - EXCERPT_PAD), m.end() + EXCERPT_PAD
        if spans and a <= spans[-1][1]:
            spans[-1][1] = b
        else:
            spans.append([a, b])
    excerpt = " … ".join(body[a:b] for a, b in spans)[:EXCERPT_MAX]
    return {"lines": lines, "rows": plan_rows(excerpt), "excerpt": excerpt}

def plan_rows(excerpt):
    """계획서 발췌 → 정책 줄 + 고배당기업 표 줄. 발췌에서 읽어 파서를 고치면 원문을 다시 받지 않고 저장본으로 다시 읽는다.
    정정 공시는 정정 전 표가 앞에, 정정 후가 뒤에 와서 고배당 표는 마지막 것(pick 과 같은 규칙)"""
    last = lambda rx: (list(rx.finditer(excerpt)) or [None])[-1 if "정정" in excerpt else 0]
    hd, hp, ht = last(HIGH_DIV), last(HD_PAYOUT), last(HD_TOTAL)
    rows = policy_rows(excerpt)
    if hd:
        fy = int(hp[1]) + 1 if hp else None
        rows.append({"kind": "high_div", "slot": "연간", "on": hd[1] == "해당", "fy": [fy, fy],
                     "prev_payout": float(hp[2]) if hp else None, "prev_total": int(ht[1].replace(",", "")) if ht else None, "text": hd[0]})
    return rows

YM = r"(\d{4}\s*(?:\.|년)\s*\d{1,2}\s*월?|-)"
DT = r"(\d{4}\s*(?:\.|년)\s*\d{1,2}\s*(?:\.|월)\s*\d{1,2}\s*일?|-)"
ROW = re.compile(r"(결산배당|분기배당|중간배당)\s+" + YM + r"\s+([OX])\s+" + DT + r"\s+" + DT + r"\s+([OX])")
TABLE = re.compile(r"정관상\s*배당액\s*결정\s*기관\s*(\S+)\s+(\S+)\s+정관상\s*배당기준일을\s*배당액\s*결정\s*이후로\s*정할\s*수\s*있는지\s*여부\s*([OX-])\s*([OX-])")

def parse_policy(t):
    """정기보고서 「배당관련 예측가능성 제공에 관한 사항」 표1(정관·결정기관)·표2(회차) — 표가 없으면 정관 전문(결산만)"""
    m = TABLE.search(t)
    if m:
        rows = [[x[1], norm_date(x[4]), norm_date(x[5])] for x in ROW.finditer(t[m.end(): m.end() + 5000]) if x[3] == "O"]
        return {"organ": {"결산": m[1], "분기중간": m[2]}, "charter": {"결산": m[3], "분기중간": m[4]}, "rows": rows}
    if re.search(r"이사회\s*결의로\s*[^.]{0,30}?배당을\s*받을\s*주주를\s*확정하기\s*위한\s*기준일", t):
        return {"charter_text": "O"}
    if re.search(r"(?:배당|이익)[^.]{0,60}매\s*결산기\s*말?\s*현재의?\s*주주명부", t):
        return {"charter_text": "X"}
    return {}
