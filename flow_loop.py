"""섹터 로테이션 경량 러너 (맥/리눅스용).

monitor.py 전체(윈도우 토스트 의존)를 돌리지 않고 sectors.build()만 주기적으로 실행.
serve.py 와 함께 띄우면 sectors.html 이 SSE 로 자동 갱신된다.

  python flow_loop.py            # 90초 주기
  python flow_loop.py 60         # 60초 주기
"""
import sys, time, datetime
import sectors

POLL_SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 90


def main():
    print(f"섹터 로테이션 자동갱신 시작 — {POLL_SECONDS}초 주기 (Ctrl+C 종료)")
    while True:
        t0 = time.time()
        try:
            sectors.build()
        except Exception as ex:
            print(f"  [섹터] 갱신 실패: {ex}")
        dt = time.time() - t0
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"  [{stamp}] 갱신 완료 ({dt:.1f}s) → {POLL_SECONDS}s 후 재실행")
        time.sleep(max(1, POLL_SECONDS - dt))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n종료")
