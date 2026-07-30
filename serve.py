# -*- coding: utf-8 -*-
"""로컬 웹서버 — index.html + data/ 를 브라우저에서 열어줌"""
import http.server, os, webbrowser, threading

BASE = os.path.dirname(os.path.abspath(__file__))
PORT = 8777

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=BASE, **k)
    def log_message(self, *a):  # 콘솔 조용히
        pass

if __name__ == "__main__":
    os.chdir(BASE)
    url = f"http://localhost:{PORT}/index.html"
    print(f"공시캘린더 열림 → {url}\n(종료: Ctrl+C)")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    # 127.0.0.1 바인딩 — 같은 네트워크의 다른 기기에서 접속 못 하게 (개인 도구)
    with http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n종료")
