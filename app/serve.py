# -*- coding: utf-8 -*-
"""로컬 웹서버 — web/ 화면 + data/out 결과를 브라우저에서 열어줌
+ /events (SSE): data/out/*.json 이 바뀌는 즉시 브라우저로 '변경' 이벤트를 밀어줌
  → 브라우저가 새로고침 없이 즉시 다시 로드 (배당·실적·상하한가 실시간 반영)
+ /api/earnfacts?code=XXXXXX[&rcept=접수번호]: 실적 상세(시총·컨센서스·분기 추이) JSON
+ /ops: 관제탑 — 서버에서 도는 일·시세 허브 상태 JSON (core.ops.snapshot, 화면 web/ops.html)
+ POST /api/div_check: 배당 확인 목록 사람 판정 저장(app.div_check) → 배당 계산 다시
"""
import http.server, os, time, json, re, threading
import urllib.parse

from core.paths import BASE, OUT_DIR, PORT, EARNINGS_PATH, load_json
from core import ops
from app import div_check
WEB = os.path.join(BASE, "web")

# 토스트 클릭 → 차트 열기 브로드캐스트용
_chart_lock = threading.Lock()
_chart_events = []      # /chart 요청으로 쌓이는 종목코드 (index+1 = seq)
_sse_count = 0          # 현재 연결된 대시보드(SSE) 수 — 0이면 폴백으로 이 탭에서 직접 오픈

def _alphasquare_url(code):
    """알파스퀘어 종목 차트 URL — 6자리 코드 그대로(코스피·코스닥 공통)."""
    return f"https://alphasquare.co.kr/home/stock-summary?code={code}"

def web_mtime():
    """화면 파일(web/ 아래 html·js·css, vendor 빼고) 최신 수정 시각 — 바뀌면 SSE로 reload 신호(열린 탭 자동 새로고침)."""
    latest = 0.0
    for root, dirs, files in os.walk(WEB):
        dirs[:] = [d for d in dirs if d != "vendor"]
        for n in files:
            if n.endswith((".html", ".js", ".css")):
                try:
                    latest = max(latest, os.path.getmtime(os.path.join(root, n)))
                except OSError:
                    pass
    return latest

def data_mtimes():
    """{data/*.json 파일명: mtime} — 바뀐 파일 이름을 화면에 알려 그 탭에 필요한 것만 다시 그리게 한다."""
    out = {}
    try:
        for n in os.listdir(OUT_DIR):
            if n.endswith(".json"):
                try:
                    out[n] = os.path.getmtime(os.path.join(OUT_DIR, n))
                except OSError:
                    pass
    except OSError:
        pass
    return out

class Handler(http.server.SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"   # SSE 스트리밍용 (keep-alive)

    def __init__(self, *a, **k):
        super().__init__(*a, directory=WEB, **k)

    def translate_path(self, path):
        """화면은 web/, 계산 결과는 /data/out/ → data/out (저장소·캐시는 내보내지 않음)"""
        p = urllib.parse.unquote(path.split("?")[0])
        if p.startswith("/data/out/"):
            return os.path.join(OUT_DIR, *[x for x in p[len("/data/out/"):].split("/") if x not in ("", ".", "..")])
        return super().translate_path(path)

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
        if route == "/chart":
            return self._serve_chart()
        if route == "/ops":
            return self._send(json.dumps(ops.snapshot(), ensure_ascii=False), "application/json")
        return super().do_GET()

    def do_POST(self):
        if self.path.split("?")[0] != "/api/div_check":
            return self.send_error(404)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8"))
            return self._send(json.dumps(div_check.record(body), ensure_ascii=False), "application/json")
        except (ValueError, KeyError) as ex:
            return self.send_error(400, explain=str(ex))   # 상태줄은 latin-1 이라 한글 설명은 본문으로

    def _send(self, text, ctype):
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_chart(self):
        """토스트 클릭 진입점 — 대시보드(SSE)가 떠 있으면 그쪽에 신호를 보내
        항상 같은 'alphachart' 탭을 재사용해 열게 하고, 없으면 이 응답이 직접 연다."""
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code = (qs.get("code") or [""])[0]
        if not re.fullmatch(r"\d{6}", code):
            self.send_error(400)
            return
        with _chart_lock:
            has_client = _sse_count > 0
            if has_client:
                _chart_events.append(code)
        if has_client:
            # 대시보드가 alphachart 탭을 재사용해 연다 → 이 임시 탭은 스스로 닫기 시도
            page = ("<!doctype html><meta charset=utf-8><title>차트</title>"
                    "<body style='font:14px sans-serif;background:#0b0e14;color:#8b95a5;padding:20px'>"
                    "차트를 여는 중… (이 탭은 닫으셔도 됩니다)"
                    "<script>setTimeout(function(){try{window.close();}catch(e){}},120);</script>")
        else:
            # 대시보드가 없으니 이 탭을 그대로 차트로 이동(폴백, 재사용은 안 됨)
            page = ("<!doctype html><meta charset=utf-8><title>차트</title>"
                    f"<script>location.replace({json.dumps(_alphasquare_url(code))});</script>")
        self._send(page, "text/html")

    def _serve_earnfacts(self):
        """실적 상세 — earn_detail.view (판정·수치·경고·수급·추이·뉴스, 원자료는 네이버+DART 캐시 30분)"""
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code = (qs.get("code") or [""])[0]
        rcept = (qs.get("rcept") or [""])[0]
        if not re.fullmatch(r"\d{6}", code) or not re.fullmatch(r"\d{0,14}", rcept):
            self.send_error(400)
            return
        try:
            from derive import earn_detail
            from ingest import earn_facts
            ev = None
            if rcept:
                earn = load_json(EARNINGS_PATH, {}).get("events", [])
                ev = next((e for e in earn if e.get("rcept_no") == rcept), {"date": ""})
            body = json.dumps(earn_detail.view(earn_facts.facts(code, rcept), ev), ensure_ascii=False)
        except Exception as ex:
            body = json.dumps({"error": str(ex)})
        self._send(body, "application/json")

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
        last = data_mtimes()
        last_html = web_mtime()
        # 접속 즉시 한 번 트리거 — 최신 상태로 맞춤
        try:
            self.wfile.write(b"retry: 3000\ndata: hello\n\n")
            self.wfile.flush()
        except Exception:
            return
        global _sse_count
        with _chart_lock:
            _sse_count += 1
            last_chart = len(_chart_events)   # 접속 이전의 차트 요청은 무시(백로그 방지)
        beat = 0
        try:
            while True:
                time.sleep(1)
                try:
                    hm = web_mtime()
                    if hm > last_html:   # index.html 이 바뀜 → 열린 탭에 새로고침 지시
                        last_html = hm
                        self.wfile.write(b"event: reload\ndata: 1\n\n")
                        self.wfile.flush()
                        beat = 0
                    # 토스트 클릭으로 들어온 차트 열기 요청을 대시보드로 push
                    with _chart_lock:
                        pending = _chart_events[last_chart:]
                        last_chart = len(_chart_events)
                    for c in pending:
                        self.wfile.write(f"event: openchart\ndata: {c}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        beat = 0
                    now = data_mtimes()
                    changed = [n for n, m in now.items() if m > last.get(n, 0)]
                    if changed:
                        last = now
                        self.wfile.write(f"data: {','.join(changed)}\n\n".encode("utf-8"))
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
        finally:
            with _chart_lock:
                _sse_count -= 1

def run():
    """웹서버 기동(블로킹). 기동·창 열기는 launch.py 만 한다 — 여기서는 monitor 와 한 프로세스로 스레드에서 호출된다."""
    os.chdir(BASE)
    print(f"공시캘린더 웹서버 → http://127.0.0.1:{PORT}/")
    # 127.0.0.1 바인딩 — 같은 네트워크의 다른 기기에서 접속 못 하게 (개인 도구)
    with http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler) as httpd:
        httpd.serve_forever()
