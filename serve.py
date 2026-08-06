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
INDEX_PATH = os.path.join(BASE, "index.html")
HTML_PAGES = [os.path.join(BASE, n) for n in ("index.html", "flow.html", "sectors.html")]
PORT = 8777

# 토스트 클릭 → 차트 열기 브로드캐스트용
_chart_lock = threading.Lock()
_chart_events = []      # /chart 요청으로 쌓이는 종목코드 (index+1 = seq)
_sse_count = 0          # 현재 연결된 대시보드(SSE) 수 — 0이면 폴백으로 이 탭에서 직접 오픈

def _tv_url(code):
    """TradingView 차트 URL — KRX:<코드> 는 코스피·코스닥 공통, interval=1 = 1분봉."""
    return f"https://www.tradingview.com/chart/?symbol=KRX:{code}&interval=1"

def html_mtime():
    """페이지 HTML 최신 수정 시각 — 바뀌면 SSE로 reload 신호(열린 탭 자동 새로고침)."""
    latest = 0.0
    for p in HTML_PAGES:
        try:
            latest = max(latest, os.path.getmtime(p))
        except OSError:
            pass
    return latest

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
        if route == "/api/flow":
            return self._serve_flow()
        if route == "/api/quotes":
            return self._serve_quotes()
        if route == "/chart":
            return self._serve_chart()
        return super().do_GET()

    def _serve_chart(self):
        """토스트 클릭 진입점 — 대시보드(SSE)가 떠 있으면 그쪽에 신호를 보내
        항상 같은 'tvchart' 탭을 재사용해 열게 하고, 없으면 이 응답이 직접 연다."""
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
            # 대시보드가 tvchart 탭을 재사용해 연다 → 이 임시 탭은 스스로 닫기 시도
            page = ("<!doctype html><meta charset=utf-8><title>차트</title>"
                    "<body style='font:14px sans-serif;background:#0b0e14;color:#8b95a5;padding:20px'>"
                    "차트를 여는 중… (이 탭은 닫으셔도 됩니다)"
                    "<script>setTimeout(function(){try{window.close();}catch(e){}},120);</script>")
        else:
            # 대시보드가 없으니 이 탭을 그대로 차트로 이동(폴백, 재사용은 안 됨)
            page = ("<!doctype html><meta charset=utf-8><title>차트</title>"
                    f"<script>location.replace({json.dumps(_tv_url(code))});</script>")
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_flow(self):
        """수급 종목 상세 — flow.detail(code) (일별 가격·거래량·투자자 + 신호)"""
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code = (qs.get("code") or [""])[0]
        if not re.fullmatch(r"[A-Za-z0-9]{6}", code):
            self.send_error(400)
            return
        try:
            import flow
            body = json.dumps(flow.detail(code), ensure_ascii=False).encode("utf-8")
        except Exception as ex:
            body = json.dumps({"error": str(ex)}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_quotes(self):
        """실시간 시세 배치 — codes=콤마구분 → {code:{rate,price}} (로스터 장중 등락률용)"""
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        raw = (qs.get("codes") or [""])[0]
        codes = [c for c in re.findall(r"\d{6}", raw)][:900]
        out = {}
        try:
            import quotes
            q = quotes.quote_batch(codes)
            out = {c: {"rate": v.get("rate"), "price": v.get("price")}
                   for c, v in q.items()}
        except Exception as ex:
            out = {"error": str(ex)}
        body = json.dumps(out, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
        last_html = html_mtime()
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
                    hm = html_mtime()
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
        finally:
            with _chart_lock:
                _sse_count -= 1

def _warm_quotes():
    """네이버 연결(keep-alive)을 미리 데워 첫 /api/flow 클릭의 콜드 핸드셰이크(수 초) 제거."""
    try:
        import quotes
        quotes.quote_batch(["005930"])       # polling.finance 핸드셰이크
        quotes.daily_price("005930", 1)      # m.stock 핸드셰이크
        quotes.investor_trend("005930", 1)
    except Exception:
        pass

if __name__ == "__main__":
    os.chdir(BASE)
    url = f"http://localhost:{PORT}/index.html"
    print(f"공시캘린더 열림 → {url}\n(종료: Ctrl+C)")
    threading.Thread(target=_warm_quotes, daemon=True).start()   # 백그라운드 연결 워밍업
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    # 127.0.0.1 바인딩 — 같은 네트워크의 다른 기기에서 접속 못 하게 (개인 도구)
    with http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n종료")
