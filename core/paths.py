# -*- coding: utf-8 -*-
"""공용 기반 — 경로·json 원자 저장·감시종목·콘솔 UTF-8. API 키·네트워크 없이 import 된다."""
import json, os, sys, time, datetime, threading

# 콘솔 UTF-8 출력 — base 를 import 하는 모든 모듈이 공유(각자 재선언 불필요)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE, "data")
OUT_DIR = os.path.join(DATA_DIR, "out")       # 계산 결과 — 화면·엑셀이 읽음
CACHE_DIR = os.path.join(DATA_DIR, "cache")   # 토큰·고유번호·시세 DB·알림 기록·조회 캐시
for _d in (OUT_DIR, CACHE_DIR):
    os.makedirs(_d, exist_ok=True)

# 계산 결과 파일 — 상수 이름 = 파일 이름. 쓰는 모듈·읽는 모듈 모두 여기서 가져간다
(CAPITAL_PATH, DISCLOSURES_PATH, DIV_BOOK_PATH, DIV_SOURCES_PATH, DIV_VERIFY_PATH,
 DIVIDEND_CHECK_PATH, DIVPOINTS_PATH, EARN_SCHED_PATH, EARNINGS_PATH, EXPIRIES_PATH, HALTS_PATH,
 KRX_ACTIONS_PATH, LIMITS_PATH) = (os.path.join(OUT_DIR, f"{n}.json") for n in (
    "capital", "disclosures", "div_book", "div_sources", "div_verify",
    "dividend_check", "divpoints", "earn_sched", "earnings", "expiries", "halts",
    "krx_actions", "limits"))

DIV_POLICY_PATH = os.path.join(BASE, "div_policy.json")   # 회사별 배당정책 장부 — 사람이 출처 보고 고치는 입력, 저장소에 커밋

PORT = 8777
URL = f"http://127.0.0.1:{PORT}/"   # 로컬 웹서버(app.serve)


def stamp(t=None):
    """결과 파일의 갱신 시각 'YYYY-MM-DD HH:MM:SS' (t 기본 지금)"""
    return (t or datetime.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")

def save_json(path, obj, indent=1):
    """원자적 저장 — 쓰다 만 파일을 웹이 읽는 사고 방지 (tmp에 쓰고 교체). 큰 캐시는 indent=None 으로 작게."""
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"   # 스레드·프로세스마다 다른 임시파일 — 동시에 써도 안 섞임
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
    for wait in (0.2, 0.5, 1, 2, 4):   # 윈도우: 다른 프로세스(읽는 쪽·백신)가 잠깐 열고 있으면 교체가 막힘 → 잠깐 뒤 다시
        try:
            return os.replace(tmp, path)
        except PermissionError:
            time.sleep(wait)
    os.replace(tmp, path)

def load_json(path, default):
    """없거나 못 읽으면 default. 저장소(core.store)처럼 깨진 파일을 빈 값으로 삼키면 안 되는 곳은 쓰지 않는다."""
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default

DART_VIEW = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
KIND_VIEW = "https://kind.krx.co.kr/common/disclsviewer.do?method=searchInitInfo&acptNo={}&docno="


