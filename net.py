# -*- coding: utf-8 -*-
"""HTTPS 준비 (사내 보안 프로그램 TLS 검사 대응) — DART·KIND 등 외부 접속 공용.
import 하면 전역 opener 를 세팅한다: truststore → certifi → (DART_INSECURE_TLS=1 일 때만) 검증 해제."""
import os, ssl, urllib.request

def build_ssl_context():
    """단건 요청용 SSL 컨텍스트 — certifi 우선, 없으면 시스템 기본.
    DART_INSECURE_TLS=1 이면 검증 해제(최후 수단). truststore 는 재귀 이슈로 여기선 미사용."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
        if os.environ.get("DART_INSECURE_TLS") == "1":
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

def _install_opener():
    try:
        import truststore
        truststore.inject_into_ssl()
        return "truststore"
    except Exception:
        pass
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
        urllib.request.install_opener(urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)))
        return "certifi"
    except Exception:
        pass
    if os.environ.get("DART_INSECURE_TLS") == "1":
        # 사내 TLS 검사 등으로 정상 검증이 불가능할 때만 명시적으로 켜는 최후 수단
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        urllib.request.install_opener(urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)))
        print("[경고] DART_INSECURE_TLS=1 — TLS 인증서 검증 없이 접속합니다")
        return "unverified"
    return "default"  # 시스템 기본 인증서로 검증 (실패 시 DART_INSECURE_TLS=1 안내됨)

TLS_MODE = _install_opener()
