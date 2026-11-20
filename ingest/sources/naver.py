# -*- coding: utf-8 -*-
"""네이버 증권 모바일 API 접속 — JSON 받기만 (해석 없음)"""
import json, urllib.request
from ingest.sources.net import build_ssl_context

_CTX = None
HDR = {"User-Agent": "Mozilla/5.0"}

def get(url):
    global _CTX
    if _CTX is None:
        _CTX = build_ssl_context()
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=10, context=_CTX) as r:
        return json.load(r)
