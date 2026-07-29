# 📅 공시캘린더 — DART 실시간 공시 트래커

코스피200 + 코스닥150 전 종목(~348종목)의 DART 공시를 **실시간 폴링으로 감지**하고,
**윈도우 토스트 알림**(클릭 → DART 원문) + **웹 캘린더**로 정리해주는 개인 트레이딩 도구.

> DART는 푸시(웹소켓)가 없어서, `list.json`을 1분마다 폴링해 새 접수번호를 잡아냅니다.

## 주요 기능

- **관심 종목만 타겟팅** — 코스피200(네이버 실시간) + 코스닥150(스냅샷), `watchlist.json`
- **9종 공시 분류** — 유상/무상증자, 자사주, 메자닌(CB/BW/EB), 감자, 합병/분할, 최대주주변경, 실적, 배당
- **실시간 알림** — 1분 폴링, 새 공시 윈도우 토스트(클릭 시 DART 원문)
- **웹 캘린더 2탭** — 공시 캘린더 / 배당 캘린더(완전 분리)
- **배당 캘린더** — 원문 파싱으로 1주당 배당금 + 기준일 추출, **잔고확정일(기준일 T-2 영업일)** 에 "회사명(금액)" 표시. 한국 공휴일·근로자의날·연말폐장 반영
- **선물·옵션 만기일** — 규칙 계산(둘째 목요일, 3·6·9·12월 동시만기, 휴장시 직전영업일)
- **원클릭 실행** — 바탕화면 아이콘 + 부팅 자동시작

## 구성

| 파일 | 역할 |
|---|---|
| `fetch.py` | DART 공시 목록 수집·분류 (증자=유형B, 거래소=유형I) |
| `dividends.py` | 배당금·기준일 원문 파싱, T-2 잔고확정일 계산, 증분 반영(upsert) |
| `expiries.py` | 선물·옵션 만기일 규칙 계산 |
| `get_watchlist.py` | 코스피200(네이버) + 코스닥150(스냅샷) → `watchlist.json` |
| `monitor.py` | 실시간 폴러(1분), 새 공시 감지·알림·캘린더 반영 |
| `serve.py` / `index.html` | 웹 캘린더 (FullCalendar, http://localhost:8777) |
| `launch.py` | 원클릭 런처(폴러+웹, 중복실행 방지) |

## 설치 & 실행

1. Python 3 + 패키지: `pip install numpy holidays winotify pillow`
2. **DART API 키 발급** ([opendart.fss.or.kr](https://opendart.fss.or.kr)) → `dart_key.txt.example` 를 `dart_key.txt` 로 복사 후 키 입력
3. 감시대상 생성: `python get_watchlist.py`
4. 실행: `python launch.py` (또는 바탕화면 아이콘)

```
python launch.py         # 폴러 + 웹 한 번에
python fetch.py 90       # 과거 90일 공시 백필
python dividends.py 90   # 배당 데이터 추출
python monitor.py        # 실시간 폴러만
python serve.py          # 웹 캘린더만 (localhost:8777)
```

## 환경 참고

- 이 도구를 개발한 PC 네트워크에선 **KRX(data.krx.co.kr) 자동접근이 차단**됨 → 코스피200은 네이버, 코스닥150은 임시 스냅샷 사용
- 배당은 DART 정형 API가 없어 원문(document.xml) 텍스트 파싱으로 처리

자세한 결정 기록·변경 이력은 [PROJECT.md](PROJECT.md) 참고.
