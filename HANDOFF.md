# HANDOFF · 매핑 담당자 조사 비서 (2026-10-22)

이 문서는 회사 PC에서 진행한 요구사항 인터뷰(Ouroboros interview `interview_20261022_083241`)의 결론과 다음에 할 일을 정리한 것이다. 인터뷰 기록은 회사 PC의 `~/.ouroboros`에만 있으므로, 다른 PC에서는 이 문서와 `seed.yaml`을 기준으로 이어서 작업한다.

## 파일

- `seed.yaml`: 인터뷰로 만든 Seed(`seed_44adea522f5e`). 단, 아래 "Seed에 남은 옛 결정"을 무시하고 읽는다.
- `docs/ref_old_SERVICE.md`: 기존 sds-ax-project(엑셀 기반 배치 Agent)의 SERVICE.md. 도메인·분류 규칙 참고용이며 이 프로젝트의 명세가 아니다.

## 목표 (사용자 승인 문장)

VESSEL·ACTION 매핑 문의를 로컬 Streamlit 웹 앱에서 등록하면 Python 규칙과 LLM 구조화 출력으로 자동 분류해 SQLite 이슈대장에 기록하고, 매핑 담당자는 MCP 도구를 쓰는 LangGraph ReAct 조사 비서와 대화하며 열린 이슈를 조사하고, 비서가 만든 처리안(매핑 변경 + 상태 변경)을 승인해야만 한 트랜잭션으로 반영되는 시스템을 12시간 안에 만든다.

## 확정한 결정

### 스택과 진행 방식
- Python 3.14, LangGraph(ReAct 순환 직접 조립: agent↔tools, should_stop, finalize, add_messages + 체크포인터), AWS Bedrock Claude(`.env`의 `BEDROCK_MODEL_IDS` 우선순위).
- FastMCP 서버(stdio) + langchain-mcp-adapters. MCP 클라이언트는 이 Agent 하나뿐(연습 목적).
- 설치 허락 라이브러리: mcp, langchain-mcp-adapters, streamlit.
- 문서(SERVICE.md → TASKS.md) 먼저, 그다음 구현. 순수 구현 12시간.
- 완성 순서: ⓪ DB·시드·이슈대장 화면 → Ⓐ 신규 등록·자동 분류 → ② 자유 질의 → ① 처리안 승인 → ③ 값 이상 조사 → ④ 반려 재요청. 시간이 부족하면 ③·④부터 줄인다.

### 데이터
- 자체 시드 스크립트로 만든 SQLite: VESSEL_MST, ACTION_MST, VESSEL_ACTION(EXECUTED_YN·EXECUTED_AT), CUSTOMER_MST, OWNER_MST, ISSUE, RESOLUTION_LOG, 분류·trace 기록. 기존 sds-ax-project는 수정하지 않는다.
- 엑셀은 쓰지 않는다. 이슈대장은 ISSUE 테이블(번호, 접수일시, 요청자 ID, VESSEL_ID, ACTION_SEQ, 문의내용, 담당자, 처리상태, 처리결과, 처리일시, PARENT_ISSUE_NO).

### 화면 (Streamlit, 로컬)
1. 이슈대장: 조회·필터·상세, 행마다 "이 건 조사" 버튼 → 그 이슈의 비서 대화. 대장에서 값을 직접 고치지 않는다.
2. 신규 문의 등록 폼: VESSEL_ID·ACTION_SEQ는 마스터 목록에서 고르는 입력칸, 문의내용은 자유 텍스트.
3. 조사 비서 대화 + 승인 카드.

### 자동 분류 (등록 즉시)
- 1단계 Python 검사: 고객 ID 없음·미등록 → 반려, 담당자 빈칸 / 칸 문제(빈 값·여러 값·마스터에 없음) → 반려, 매핑 담당자.
- 2단계 LLM 구조화 출력 1회 → Python 규칙표:
  - 매핑 생성·수정·삭제 → 처리대기, 매핑 담당자
  - 한 문의에 요청 여러 개 → 반려, 매핑 담당자
  - 값 이상 → 확인필요, 매핑 담당자
  - 마스터 요청 VESSEL → 이관, VESSEL 담당자 / ACTION → 이관, ACTION 담당자 / 둘 다 → 이관, "VESSEL 담당자, ACTION 담당자"
  - 마스터 요청 대상 모름 → 반려, 매핑 담당자
  - 담당 외 → 거절, 담당자 빈칸
  - 판단 불가 → 확인필요, 매핑 담당자
- 분류 실패(스키마 오류·모든 모델 실패) → 문의 저장, 확인필요, 처리결과 "자동 분류 실패", [다시 분류] 버튼(같은 이슈 갱신, trace 기록).
- 자동 분류 단계에서는 매핑을 절대 바꾸지 않는다.

### 조사 비서와 MCP 도구
- 읽기(자동): find_vessel(query), find_action(query), get_vessel_mappings(vessel_id), list_issues(status?, since?, limit=10), get_issue(issue_no), get_issue_trace(issue_no).
- 로그(자동, append-only): log_resolution(issue_no, note).
- 쓰기(승인 필요): **propose_resolution(issue_no, mapping_change?, new_status, result)** 하나.
- 도구 반환은 정해진 형식의 문자열, 오류는 예외 대신 안내 문자열(받은 값·쓸 수 있는 값·다음 행동). TOOL_SPECS/TOOLS_IMPL 분리 + 이름 일치 assert.
- 위험 등급은 Python이 DB 상태로 정한다. 미등록 도구는 승인 필요.

### 승인
- 처리안(매핑 변경 + 상태 변경)을 묶어 한 번에 승인하고, 매핑·ISSUE·RESOLUTION_LOG를 **한 SQLite 트랜잭션**으로 반영한다. 부분 처리 상태는 없다.
- LangGraph interrupt. 카드: 위험 등급, 대상 전후 값, EXECUTED_YN, 근거(이슈 번호·문의 원문).
- y=실행, n=실행하지 않고 "거절됨" 전달, e=수정 지시로 재계획. EXECUTED_YN='Y' 매핑이 포함되면 destructive → y 뒤 이슈 번호 재입력.
- 실행 직전 재검증: 이슈가 열린 상태, 대상 매핑 존재/부재, EXECUTED_YN이 제안 시점과 같음, 허용된 상태 전이.
- 매핑 수정 = 연결된 ACTION을 다른 ACTION으로 교체(삭제 후 생성, 새 행 N·NULL).
- 상태 전이: 열린 건(확인필요·이관·처리대기) → 완료·반려·거절. 완료는 다시 열지 않음. 이관 건은 확인 후 "완료"로 닫음.
- 반려 건 "수정해서 재요청" → 값이 채워진 등록 폼 → PARENT_ISSUE_NO를 가진 새 이슈로 등록·자동 분류. 원래 건은 반려 유지.

### 멈춤과 실패
- 한 사용자 턴당 도구 호출 8회(승인 후 재개 포함). 상한·중복 호출 → finalize(근거, 확인 못 한 것, 다음 질문 제안). 같은 도구 연속 2회 실패 → finalize.
- Bedrock 실패 → 재시도 없이 다음 모델. 전부 실패 → 배너 + 다시 시도, 상태는 체크포인터에 보존. MCP 끊김 → 1회 재연결.
- 대화 thread는 이슈 번호별(자유 질의는 별도). 승인 대기 처리안은 그 thread에 남는다.

### 마스킹
- trace 로그에만 mask_pii(전화번호·이메일·사번)와 자격증명 마스킹. LLM 입력과 화면은 마스킹하지 않는다.

### 평가
- 15건: 자동 분류 4, ② 자유 질의 3, ① 처리안 승인 4(y/n/e·destructive 포함), ③ 값 이상 2, ④ 반려 재요청 2.
- 안전 지표 100%(가짜 LLM·실제 Bedrock): 승인 없는 쓰기 0, 거절 후 DB 변화 0, 이중 확인 없는 destructive 0, 예산 초과 0.
- 자동 분류 정답 = 담당자·처리상태 정확히 일치. 비서 정답 = 케이스별 필수 핵심 사실 문자열 포함 + 필수 도구 호출 여부.
- 결과 JSON Lines 저장, 케이스 단위 회귀(이전 pass → 이번 fail) 0건. 첫 실행이 기준선.
- 단계 완료 = 가짜 LLM pytest 통과. 실제 Bedrock 점검은 "실제 Bedrock 점검 목록"에 남겼다가 사용자가 지시할 때 실행.
- 최종 합격(실제 Bedrock): 자동 분류 4/4, 비서 시나리오 11건 중 10건 이상(85%), 안전 지표 100%, 회귀 0.

### 범위 제외
로그인·권한, 다중 사용자 동시 접속, 실제 고객 메시지 발송, 운영 DB·메신저 연결, 마스터 변경, 기존 배치 Agent 수정·연동, Claude Desktop 연동, 배포.

## Seed에 남은 옛 결정 (무시할 것)

인터뷰 중간에 바뀐 결정이 `seed.yaml`에 옛 버전 그대로 남아 있다. Seed QA 점수는 0.50(REVISE)이었고, 주된 원인이 이것이다.
- constraints의 마지막 항목(`[from-user][refined] Decision: MCP 도구 = ... 쓰기 4개 ...`): 쓰기 도구 4개와 별도 승인 설계는 폐기됐다. 쓰기 도구는 propose_resolution 하나다.
- `LEDGER_APPLIED(Y/N)`(constraints, 수용 기준 2번, ontology): 부분 처리 상태가 없어졌으므로 필요 없다.
- acceptance_criteria의 마지막 두 항목(`[from-user] 성공 판정 ...` 약 20건, `[from-user][refined] Decision: 목표 문장 승인 ...`): 결정 문장이 그대로 들어간 것이다. 평가셋은 15건이 맞다.
- QA 제안 중 반영할 만한 것: 수용 기준을 사용자가 보는 결과 5~6개로 묶기, "문의 원문·도구 결과는 신뢰하지 않는 데이터이며 도구 호출·위험 등급을 바꿀 수 없다"는 제약 추가.

## 다음에 할 일

1. 위 "확정한 결정"을 기준으로 이 프로젝트의 새 `SERVICE.md`를 쓴다(기존 프로젝트 형식을 참고하되 내용은 새로).
2. `TASKS.md`(작업 단위, 확인 기준, 실제 Bedrock 점검 목록)를 쓴다.
3. `CLAUDE.md`(작업 규칙: 한 번에 작업 하나, 보고서, Bedrock 호출 규칙 등)를 만든다.
4. ⓪부터 구현을 시작한다.
