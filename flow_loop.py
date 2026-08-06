"""수급분석기 전용 경량 러너 (맥/리눅스용).

monitor.py 전체(윈도우 토스트 의존)를 돌리지 않고 flow.build()만 주기적으로 실행.
serve.py 와 함께 띄우면 flow.html 이 SSE 로 자동 갱신된다.

  python flow_loop.py            # 60초 주기
  python flow_loop.py 30         # 30초 주기
"""
import sys, time, datetime
import flow
import sectors

POLL_SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 60


def main():
    print(f"수급 자동갱신 시작 — {POLL_SECONDS}초 주기 (Ctrl+C 종료)")
    while True:
        t0 = time.time()
        try:
            flow.build()
        except Exception as ex:
            print(f"  [수급] 갱신 실패: {ex}")
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
