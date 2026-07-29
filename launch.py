# -*- coding: utf-8 -*-
"""
공시캘린더 원클릭 런처
- 폴러(monitor.py) + 웹서버(serve.py)를 함께 실행
- 이미 실행 중이면(포트 8777 응답) 중복 실행 대신 브라우저만 연다
- 바탕화면 아이콘 / 시작프로그램(부팅 자동실행)에서 이 파일을 호출
"""
import os, sys, subprocess, webbrowser, urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable.replace("pythonw.exe", "python.exe")  # 자식은 콘솔 있는 python.exe로
PORT = 8777

def already_running():
    """정식 HTTP 요청으로 서버 생존 확인 (bare connect는 단일요청 서버를 막을 수 있어 지양)"""
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/index.html", timeout=1.5)
        return True
    except Exception:
        return False

def _minimized():
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 6  # SW_MINIMIZE
    return si

def main():
    if already_running():
        webbrowser.open(f"http://localhost:{PORT}/index.html")
        return
    flags = subprocess.CREATE_NEW_CONSOLE
    subprocess.Popen([PY, "monitor.py"], cwd=BASE, creationflags=flags, startupinfo=_minimized())
    subprocess.Popen([PY, "serve.py"], cwd=BASE, creationflags=flags, startupinfo=_minimized())
    # serve.py 가 브라우저를 자동으로 연다

if __name__ == "__main__":
    main()
