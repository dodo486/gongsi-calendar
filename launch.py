# -*- coding: utf-8 -*-
"""
공시캘린더 원클릭 런처 — 바탕화면 '공시캘린더' 아이콘 하나가 이 파일을 호출
- 서버가 안 떠 있으면(포트 8777 무응답) 워커 1개를 최소화 콘솔로 기동:
  `python launch.py --run` = 웹서버(serve)는 스레드, 폴러(monitor)는 메인 — **한 프로세스·한 창** (2026-09-29 통합)
- 서버가 응답할 때까지 기다린 뒤 대시보드를 크롬 앱창(--app)으로 연다 (크롬 없으면 기본 브라우저)
- 이미 떠 있으면 중복 기동 없이 창만 연다. 창도 이미 있으면 새로 열지 않고 맨 앞으로 가져온다
- 서버 기동·창 열기는 이 파일만 한다 (serve.py 는 창을 열지 않음)
- 끄기: 최소화된 콘솔 창 1개를 닫으면 웹서버·폴러가 함께 꺼진다
"""
import os, sys, time, shutil, threading, subprocess, webbrowser, urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable.replace("pythonw.exe", "python.exe")  # 워커는 콘솔 있는 python.exe로
PORT = 8777
URL = f"http://127.0.0.1:{PORT}/"
CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]

def already_running():
    """정식 HTTP 요청으로 서버 생존 확인 (bare connect는 단일요청 서버를 막을 수 있어 지양).
    바쁠 때 한 번 느리게 답해도 '꺼짐'으로 오판해 워커를 이중 기동하지 않게 2회 시도."""
    for _ in range(2):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/index.html", timeout=4)
            return True
        except Exception:
            pass
    return False

def _spawn_opts():
    """윈도우: 최소화된 새 콘솔 창 / 맥·리눅스: 백그라운드 프로세스"""
    if os.name != "nt":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 6  # SW_MINIMIZE
    return {"creationflags": subprocess.CREATE_NEW_CONSOLE, "startupinfo": si}

def find_window():
    """열려 있는 대시보드 크롬 앱창(제목 '📅 공시캘린더') 핸들, 없으면 None"""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = []
    def check(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        if buf.value == "Chrome_WidgetWin_1" and user32.IsWindowVisible(hwnd):
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value.endswith("공시캘린더"):
                found.append(hwnd)
                return False
        return True
    user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(check), 0)
    return found[0] if found else None

def open_window():
    chrome = next((p for p in CHROME_PATHS if os.path.exists(p)), None) or shutil.which("chrome")
    if chrome and os.name == "nt":
        hwnd = find_window()
        if not hwnd:   # 창이 이미 있으면 새로 열지 않고 그 창을 앞으로
            # ShellExecute(탐색기와 같은 경로)로 실행 — Popen 으로 띄우면 런처(pythonw)가 바로 끝나면서
            # 기존 크롬에 '새 앱창' 요청을 넘기기 전에 자식이 정리돼 창이 안 뜨는 일이 있었음 (2026-09-29)
            os.startfile(chrome, "open", f"--app={URL}")
            for _ in range(20):   # 창이 뜰 때까지 (최대 ~10초)
                time.sleep(0.5)
                hwnd = find_window()
                if hwnd:
                    break
        if hwnd:   # 다른 창 뒤에 깔리지 않게 — 최소화돼 있으면 복원 후 맨 앞으로
            import ctypes
            user32 = ctypes.windll.user32
            user32.ShowWindow(hwnd, 9)   # SW_RESTORE
            # 윈도우는 입력을 받지 않은 프로세스의 SetForegroundWindow 를 막는다 → Alt 를 눌렀다 떼서 허용받음
            user32.keybd_event(0x12, 0, 0, 0)
            user32.keybd_event(0x12, 0, 2, 0)
            user32.SetForegroundWindow(hwnd)
    elif chrome:
        subprocess.Popen([chrome, f"--app={URL}"])
    else:
        webbrowser.open(URL + "index.html")

def run_worker():
    """한 프로세스에서 웹서버(데몬 스레드) + 폴러(메인) 실행. 폴러가 끝나거나 창을 닫으면 둘 다 종료."""
    os.chdir(BASE)
    if already_running():   # 이미 다른 워커가 8777을 잡고 있으면 폴러 중복(토스트 2번) 방지 위해 종료
        print("이미 실행 중 — 종료"); return
    if os.name == "nt":
        os.system("title 공시캘린더 (웹서버+폴러)")
    import serve, monitor
    threading.Thread(target=serve.run, daemon=True, name="serve").start()
    monitor.main()

def main():
    if not already_running():
        subprocess.Popen([PY, "launch.py", "--run"], cwd=BASE, **_spawn_opts())
        for _ in range(40):   # 서버 기동 대기 (최대 ~20초)
            if already_running():
                break
            time.sleep(0.5)
    open_window()

if __name__ == "__main__":
    run_worker() if "--run" in sys.argv else main()
