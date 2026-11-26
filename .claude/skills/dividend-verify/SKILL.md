---
name: dividend-verify
description: 공시캘린더 배당 로직 검증 하네스 오케스트레이터. 배당일자·배당금 추정 코드(derive/dividend, ingest/parsers/dividend.py)에 실수가 없는지, 금액 추정이 최선인지, 정책 추출이 맞는지를 검증자 3명(코드 감사·추정 연구·추출 감사)으로 독립 검증하고 하나의 보고로 모은다. "배당 로직 검증", "배당 하네스", "배당금 추정 맞나", "배당 실수 없나 확인", "배당 검증 다시 실행/재실행", "추출만 다시", "이전 결과 기반으로 다시 확인", 배당 코드를 크게 고친 뒤 확인 요청이 오면 반드시 이 스킬을 쓴다. 배당 화면 문구 수정·단일 종목 값 질문(“SK스퀘어 얼마야”)에는 쓰지 않는다.
---

# 배당 로직 검증 하네스

배당 코드를 크게 고친 뒤, **고친 사람이 아닌 눈**으로 세 방향을 동시에 본다. 이 프로젝트의 과거 실수는 대부분 "같은 사실을 여러 곳에서 다르게 계산", "회사가 밝힌 숫자를 못 읽음", "검증이 이미 있는 행만 봄"이었다 — 세 검증자는 각각 이 셋을 맡는다.

## 실행 모드: 서브 에이전트 (팬아웃 → 팬인)
검증자끼리 대화할 필요가 없고 독립성이 핵심(서로의 결론에 끌려가지 않게)이라 서브 에이전트로 병렬 실행한다. 모든 호출에 `model: "opus"`, `run_in_background: true`.

| 검증자 | 정의 | 방법 | 산출물 |
|---|---|---|---|
| 코드 감사 | `.claude/agents/dividend-code-auditor.md` | `references/code-audit.md` | `_workspace/01_code-auditor_findings.md` |
| 추정 연구 | `.claude/agents/dividend-estimate-researcher.md` | `references/estimate-research.md` | `_workspace/02_estimate-researcher_results.md` |
| 추출 감사(두 번째 판독자, 묶음마다 1명) | `.claude/agents/dividend-parser-auditor.md` | `references/parser-audit.md` | `_workspace/03_parser-auditor_{묶음}.md` |

## Phase 0: 컨텍스트 확인
- `_workspace/` 없음 → 초기 실행.
- 있음 + "다시/재실행" → 기존 산출물을 읽게 하고(각 에이전트의 "이전 산출물이 있을 때"), 전후 비교.
- 있음 + "추출만 다시" 등 부분 요청 → 해당 검증자만.
- 새 코드 기준 전면 재검증 → `_workspace/`를 `_workspace_prev/`로 옮기고 초기 실행.

## Phase 1: 준비 (리더가 직접)
0. **전수 대조부터** — 빠뜨린 공시가 가장 위험하다. 들여오기 점검 결과 `store doc_coverage`(ingest/coverage.py, 공시를 다 받을 때마다 갱신)를 읽어
   `_workspace/00_coverage.md` 에 적는다: 수집 종류에 안 걸린 배당 공시, 안 받은 공시·옛 버전, 정책 줄로 안 잡힌 숫자, 정답 묶음 실패.
   하나라도 있으면 보고 맨 위에 올린다(검증자 결과보다 먼저).
   읽을 공시 목록 = 주주환원 공시 전부(처음) 또는 지난 실행 뒤 새로 들어온 공시 + 정책 줄로 안 잡힌 숫자가 있는 공시 + 계획서 발췌(excerpt) 전부(처음).
   이미 확인됨(`_workspace/verified.json` 의 접수번호 + 파서 버전)이면 다시 읽지 않는다. 지수배당포인트 영향 큰 종목부터, 약 50건씩 묶음.
1. 결과물이 최신 코드로 만들어졌는지 확인 — 아니면 배당 계산만 돌린다:
   `PYTHONIOENCODING=utf-8 python -c "from derive.registry import CALCS; import derive.run as R; R.CALCS=[c for c in CALCS if c.name in ('div_verify','div_book','divpoints','div_queue','dividend_check')]; R.run(['day'])"`
2. `git rev-parse --short HEAD`를 `_workspace/00_context.md`에 적는다(어느 코드를 검증했나).

## Phase 2: 병렬 검증
코드 감사·추정 연구 1명씩 + 추출 감사는 묶음마다 1명(묶음 목록 파일 경로를 넘김)을 한 메시지에서 동시에 띄운다(합쳐 10명 이내, 넘으면 두 차례로). 프롬프트에는 에이전트 정의 파일 경로, 방법 파일 경로, 산출물 경로, 금지 사항(코드·저장소 수정, 앱 재시작, DART 접속)을 넣는다. 결론을 미리 알려주지 않는다(확증 편향 방지).

## Phase 3: 종합 (리더)
1. 세 산출물을 읽는다. 서로 다른 검증자가 같은 원인을 짚으면 묶는다. 상충하면 지우지 말고 둘 다 적고 재현으로 판정한다.
2. **리더가 결함을 직접 재현**해 확정/의심을 다시 판정한다(검증자의 "확정"도 그대로 믿지 않는다).
3. `_workspace/04_summary.md` + 사용자 보고: 확정 결함(돈 영향 순) · 추정 개선 제안(백테스트 근거) · 추출 점수 · 못 한 검사.
4. 추출 감사에서 리더가 확정한 판정(원문 + 있어야 할 정책 줄)은 정답 묶음 `ingest/parsers/dividend_gold.json` 에 추가하고, 읽은 공시는 `_workspace/verified.json` 에 접수번호·파서 버전으로 남긴다 — 파서를 고칠 때마다 정답 묶음이 회귀 시험으로 돈다(들여오기 점검 4번).
   4-1. 추출 감사 판정을 `scripts/to_checked.py`로 `div_checked.json` 판독자 판정에 넣는다. 사람은 화면 확인 목록에서 판독자 다름·판독자 제안 줄을 판정한다.
5. 고칠지는 사용자에게 묻는다 — 이 하네스는 검증만 한다(정답 묶음 추가는 검증 기록이라 예외).

## 에러 핸들링
검증자 실패 → 1회 재실행, 또 실패하면 그 축은 "못 함"으로 보고에 명시하고 진행.

## 테스트 시나리오
- 정상: 배당 코드 개편 직후 "배당 로직 검증해줘" → 세 산출물 + 요약, 요약의 확정 결함마다 리더 재현 기록.
- 에러: 추정 연구자가 표본 부족으로 결론 못 냄 → "표본 부족"으로 보고, 나머지 둘로 진행.
- 부분: "추출만 다시" → 추출 감사만 재실행, 전후 점수 비교.
