# -*- coding: utf-8 -*-
"""사실 저장소 — 종류마다 data/store/<종류>.json {키: 사실}. 공시 종류는 접수번호가 키, 주기 원천은 원천이 정한 키.
쓰는 쪽은 ingest 하나, 읽는 쪽은 derive. 파일이 바뀔 때만 다시 읽는다."""
import os, json, threading
from core.paths import DATA_DIR, save_json

DIR = os.path.join(DATA_DIR, "store")
os.makedirs(DIR, exist_ok=True)
_lock = threading.RLock()
_mem = {}

def _path(kind):
    return os.path.join(DIR, f"{kind}.json")

def load(kind):
    p = _path(kind)
    mt = os.path.getmtime(p) if os.path.exists(p) else None
    with _lock:
        if kind not in _mem or _mem[kind][0] != mt:
            _mem[kind] = (mt, json.load(open(p, encoding="utf-8")) if mt else {})
        return _mem[kind][1]

def save(kind, rows):
    with _lock:
        save_json(_path(kind), rows, indent=None)
        _mem[kind] = (os.path.getmtime(_path(kind)), rows)
