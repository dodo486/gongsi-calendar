# -*- coding: utf-8 -*-
"""공용 기반 — 경로·json 원자 저장·감시종목·콘솔 UTF-8. API 키·네트워크 없이 import 된다."""
import json, os, sys

# 콘솔 UTF-8 출력 — base 를 import 하는 모든 모듈이 공유(각자 재선언 불필요)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
os.makedirs(DATA_DIR, exist_ok=True)

def save_json(path, obj, indent=1):
    """원자적 저장 — 쓰다 만 파일을 웹이 읽는 사고 방지 (tmp에 쓰고 교체). 큰 캐시는 indent=None 으로 작게."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)

_wl_cache = {"mtime": None, "data": None}
def load_watchlist():
    """감시종목(코스피200+코스닥150) {종목코드: {name, market, ...}}. 없으면 None(필터 없음)."""
    path = os.path.join(BASE, "watchlist.json")
    if not os.path.exists(path):
        print("  [!] watchlist.json 없음 — 전체 공시 표시(필터 없음)")
        return None
    mtime = os.path.getmtime(path)   # 파일이 바뀔 때만 다시 파싱 — 폴링마다(20초) 재파싱 방지
    if _wl_cache["mtime"] != mtime:
        data = json.load(open(path, encoding="utf-8")).get("stocks", {})
        _wl_cache["data"] = data      # data 를 먼저 세팅하고
        _wl_cache["mtime"] = mtime     # mtime 은 마지막 — 동시 호출 스레드가 None data 를 보는 race 방지
    return _wl_cache["data"]
