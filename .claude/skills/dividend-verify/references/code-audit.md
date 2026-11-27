# 코드 감사 방법

## 1. 구조 지도 먼저
`docs/배당예측-워크플로우.md` 2부(구조·정책 줄·현금 몫·금액 단계)를 읽고, 코드에서 각 단계 함수를 찾아 표로 만든다:
수집(ingest) → 사실(facts: slot_of·paid·annual_dps·final_of·last_annual·shares·cons_of·eps_of) →
재료(inputs: contexts·view·policy·cash_share·evidence·for_date·for_amount·samples) → 일정(predict.upcoming) →
날짜(date.judge·predict) · 금액(amount: 신호들·policy_levels·stated·from_annual·estimate) → 붙이기(predict.one) → 장부(book) → points·check·verify.

## 2. 중복 계산 찾기
같은 사실(이미 준 배당, 작년 DPS, 사업연도, 주식 수, 특별배당, 컨센, 단위 맞추기, 바닥)을 facts 조회 말고 직접 계산하는 곳을 grep 으로 찾는다.
값이 같아도 "두 곳"이면 적는다.

## 3. 경계면 교차 비교 (실제 데이터로)
`from derive.dividend.inputs import contexts, view` 로 재료 묶음을 만들고 종목 몇 개를 골라 손으로 따라간다:
- 결산 몫 + 그해 분기·중간 합 = 사업보고서 연간 DPS 인가 (final_of·paid·annual_dps 일관성)
- slot_of: 분기·중간은 결의일이 든 사업연도, 결산은 직전 결산기말, 예고는 다음 분기말 — 3분기보고서 q3_dps 와 공시 회차 합 대조
- shares: 배당결정 총액÷주당 vs 보고서 유통주식 수 — 우선주 포함 여부가 총액 기준과 맞는가
- stated: policy_levels 의 role(exact/level/range) → 고르기 → from_annual 에서 이미 준 배당을 두 번 빼거나 안 빼는 경로가 없는가
- estimate: 단위 맞추기(올림/반올림)와 바닥이 신호 값을 원래 뜻과 다르게 바꾸는 경우
- predict.upcoming: 결산 근거(evidence)로 만든 예측 건과 과거 회차 건이 겹치거나 빠지는가

## 4. 미래 정보 누설 (백테스트)
`view(ctx, cut)` 이 cut 뒤에 안 정보를 걸러내는지 칸마다 확인: rounds·notices·agms·statements·policies·cons·years(보고서 제출 기한)·share_counts·cash_flow·splits.
특히 policies 의 date(공시일) vs 사람 장부 줄, 사업연도 None → 공시한 해부터 규칙. verify 가 view 밖 ctx 를 쓰는 곳이 있는지.

## 5. 점검(check)이 독립인가
check 의 검사가 계산과 같은 함수로 같은 값을 다시 확인하는 "자기 검사"인지, 독립 출처(3분기보고서·컨센·원문)와 비교하는지 구분.

## 6. 돈 영향 매기기
결함마다 영향 종목의 지수배당포인트(`data/out/divpoints.json`의 items.points)와 DPS 차이를 적는다. 영향 0이면 낮은 순위.

## 쓰는 법 요령
- Windows Git Bash, `PYTHONIOENCODING=utf-8`, 작업 폴더 `D:\공시캘린더`(경로에 한글).
- 스크립트는 `_workspace/scripts/` 에. 원본 파일·data/store 수정 금지.
