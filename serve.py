# -*- coding: utf-8 -*-
"""로컬 웹서버 — index.html + data/ 를 브라우저에서 열어줌
+ /events (SSE): data/*.json 이 바뀌는 즉시 브라우저로 '변경' 이벤트를 밀어줌
  → 브라우저가 새로고침 없이 즉시 다시 로드 (배당·실적·상하한가 실시간 반영)
+ /api/earnfacts?code=XXXXXX[&rcept=접수번호]: 실적 상세(시총·컨센서스·분기 추이) JSON
"""
import http.server, os, time, json, re, webbrowser, threading
import urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
PORT = 8777

def data_signature():
    """data/*.json 의 (파일명→mtime) 최대값 — 하나라도 바뀌면 값이 커진다."""
    latest = 0.0
    try:
        for n in os.listdir(DATA_DIR):
            if n.endswith(".json"):
                try:
                    m = os.path.getmtime(os.path.join(DATA_DIR, n))
                    if m > latest:
                        latest = m
                except OSError:
                    pass
    except OSError:
        pass
    return latest

class Handler(http.server.SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"   # SSE 스트리밍용 (keep-alive)

    def __init__(self, *a, **k):
        super().__init__(*a, directory=BASE, **k)

    def log_message(self, *a):  # 콘솔 조용히
        pass

    def end_headers(self):
        # 브라우저가 index.html/JS/JSON 을 캐시해 옛 버전 보는 것 방지 (항상 최신 제공)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self):
        route = self.path.split("?")[0]
        if route == "/events":
            return self._serve_sse()
        if route == "/api/earnfacts":
            return self._serve_earnfacts()
        return super().do_GET()

    def _serve_earnfacts(self):
        """실적 상세 — consensus.facts (네이버+DART, 캐시 30분)"""
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code = (qs.get("code") or [""])[0]
        rcept = (qs.get("rcept") or [""])[0]
        if not re.fullmatch(r"\d{6}", code) or not re.fullmatch(r"\d{0,14}", rcept):
            self.send_error(400)
            return
        try:
            import consensus
            body = json.dumps(consensus.facts(code, rcept), ensure_ascii=False).encode("utf-8")
        except Exception as ex:
            body = json.dumps({"error": str(ex)}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_sse(self):
        """Server-Sent Events — data/ 변경을 감지해 즉시 push. 클라이언트는 EventSource로 수신."""
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
        except Exception:
            return
        last = data_signature()
        # 접속 즉시 한 번 트리거 — 최신 상태로 맞춤
        try:
            self.wfile.write(b"retry: 3000\ndata: hello\n\n")
            self.wfile.flush()
        except Exception:
            return
        beat = 0
        while True:
            time.sleep(1)
            try:
                sig = data_signature()
                if sig > last:
                    last = sig
                    self.wfile.write(f"data: {sig}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    beat = 0
                else:
                    beat += 1
                    if beat >= 15:   # 15초마다 heartbeat — 끊긴 연결 감지/유지
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        beat = 0
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                break   # 브라우저가 닫음(새로고침/이동) — 스레드 종료, EventSource가 자동 재접속

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
