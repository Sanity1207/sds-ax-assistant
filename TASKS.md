# TASKS · VESSEL·ACTION 매핑 문의 조사 비서

기준 문서: `seed.yaml` (seed_44adea522f5e v1.0.0). 애매한 점은 추측하지 않고 묻는다.

## 작업 규칙

- 한 번에 작업 하나만 한다. 끝나면 "확인" 줄의 방법으로 확인하고 체크한다.
- 작업 크기는 30분~1시간. 넘칠 것 같으면 쪼개서 이 문서에 먼저 반영한다.
- 평소 개발·pytest는 **가짜 LLM**만 쓴다. 실제 Bedrock 호출이 필요한 확인은 맨 아래 "실제 Bedrock 점검 목록"에 적어 두고, 사용자가 지시할 때 한 번에 실행한다.
- 계산·규칙 판정·권한·위험 등급·상태 전이는 Python이 한다. LLM은 분류(구조화 출력)와 조사 대화만 한다.
- 단계 완료 조건 = 그 단계 pytest(가짜 LLM) 통과.
- 시간 예산: 순수 구현 12시간(문서 작업 제외). 부족하면 ③ 값 이상 조사 → ④ 반려 재요청 순으로 축소·연기하고, 완료·남은 작업을 이 문서에 기록한다.

## 묶음 0 · 문서 (구현 시간 제외)

- [ ] **T00 SERVICE.md 작성** — 시나리오(Ⓐ②①③④), MCP 도구 8개 명세, 승인 UX(y/n/e·이중 확인), 멈춤 기준, 실패 처리, 평가 방법, 범위 제외, 시드 이슈 9건의 문의 문구·대상 값 확정. `docs/ref_old_SERVICE.md`는 형식만 참고.
  - 확인: `python -c "import pathlib; s=pathlib.Path('SERVICE.md').read_text(encoding='utf-8'); t=pathlib.Path('TASKS.md').read_text(encoding='utf-8'); assert 'propose_resolution' in s and 'Bedrock' in t; print('OK')"` 가 OK

## 묶음 1 · 최소 경로 (입력 → 최종 답변 한 줄기)

목표 경로: **요청 고객이 문의 등록 → 자동 분류(매핑 생성 한 유형) → ISSUE 저장 → 관리자가 [이 건 조사] → 비서가 MCP 도구 1개로 조사 → 최종 답변 표시.**
이 묶음에서는 규칙표 전체, 처리 기록, 권한, 승인, 멈춤·폴백 세부는 만들지 않는다(뒤 묶음에서 채움).

- [x] **T01 환경 준비** — `requirements.txt`의 "설치 승인 필요" 항목을 사용자에게 먼저 승인받고 `pip install -r requirements.txt`로 설치. 폴더 골격(`app/`, `tests/`, `data/`, `eval/`, `traces/`)과 `.env` 로딩 함수.
  - 확인: `.venv/Scripts/python -c "import langgraph, mcp, langchain_mcp_adapters, streamlit, dotenv"` 오류 없음, `pytest -q` 가 "no tests ran"으로 정상 종료
- [x] **T02 최소 DB·시드** — `data/app.db`에 VESSEL_MST·ACTION_MST·VESSEL_ACTION·USER_MST·ISSUE 생성(CHECK·복합 PK·FOREIGN KEY, 연결마다 `PRAGMA foreign_keys = ON`), seed.yaml의 마스터·매핑·사용자 값(SYSTEM 포함) 투입, 재실행 시 초기화. ISSUE의 요청자·처리자·VESSEL_ID·ACTION_SEQ·PARENT_ISSUE_NO는 FOREIGN KEY, VESSEL_ID·ACTION_SEQ는 NOT NULL. 시드 이슈·로그 테이블은 T09.
  - 확인: `pytest -q tests/test_seed.py` — 건수 5/6/7/8, EXECUTED_YN='Y'인데 EXECUTED_AT NULL인 행 INSERT 시 실패, 마스터에 없는 값·빈 값·없는 요청자로 ISSUE INSERT 시 실패
- [x] **T03 LLM 호출 래퍼 + 가짜 LLM** — `BEDROCK_MODEL_IDS` 순서대로 시도, temperature 0, 호출당 60초 제한, 재시도 없이 다음 모델, 응답 모델 ID 반환. 같은 인터페이스의 가짜 LLM(정해진 응답·도구 호출 순서를 흉내).
  - 확인: `pytest -q tests/test_llm.py` — 가짜 1순위 예외 → 2순위 응답, 반환된 모델 ID가 2순위
- [x] **T04 자동 분류 최소판** — 분류 출력 스키마(type·master_target·multiple_requests·id_mismatch·reason), 구조화 출력 1회, "매핑 생성 → 처리대기·매핑 담당자" 한 규칙, 처리결과 = 템플릿 + 근거 한 줄, ISSUE INSERT. 매핑은 건드리지 않음.
  - 확인: `pytest -q tests/test_classify.py -k skeleton` — 가짜 LLM으로 등록 → ISSUE 1행(처리대기·매핑 담당자), VESSEL_ACTION 변화 0
- [x] **T05 MCP 서버 최소판** — FastMCP stdio 서버, `find_vessel(query)` 1개. `TOOL_SPECS`와 `TOOLS_IMPL` 분리, import 시 이름 일치 assert.
  - 확인: `pytest -q tests/test_mcp.py` — stdio 클라이언트로 도구 목록에 find_vessel, `find_vessel("혼합")` 결과에 V003
- [x] **T06 비서 그래프 최소판** — LangGraph로 agent↔tools 순환 직접 조립, `should_stop`, `finalize` 자리(지금은 통과), `add_messages` + 체크포인터, langchain-mcp-adapters로 T05 도구 로드.
  - 확인: `pytest -q tests/test_agent_read.py -k skeleton` — 가짜 LLM이 find_vessel 호출 후 "V003"이 든 최종 답변, 같은 thread 두 번째 입력에서 이전 메시지 유지
- [x] **T07 Streamlit 최소 화면** — 사용자 A001 고정. 등록 폼(VESSEL_ID·ACTION_SEQ 마스터 드롭다운, 문의내용) → "접수됨 #번호, 처리상태, 처리결과" 표시, 이슈대장 표, 행별 [이 건 조사] → 대화창 → 비서 답변.
  - 확인: `pytest -q tests/test_ui.py -k skeleton`(AppTest, 가짜 LLM) — 등록 후 표에 새 행, [이 건 조사] 후 답변 메시지 표시
- [x] **T08 최소 경로 종단 테스트** — 등록 → 분류 → 조사 → 답변을 한 테스트로 묶고, 실제 Bedrock 점검 목록에 "최소 경로 실연" 항목 추가.
  - 확인: `pytest -q tests/test_skeleton.py` 통과 + `streamlit run app/main.py`로 같은 경로를 손으로 1회 통과(가짜 LLM 모드)

## 묶음 2 · ⓪ 기반 완성 (DB·시드·사용자 선택·이슈대장·처리 기록)

- [x] **T09 나머지 테이블 + 시드 이슈 9건** — OWNER_MST, RESOLUTION_LOG(append-only), PROCESS_LOG, CALL_LOG, MESSAGE_LOG. 시드 이슈 9건(처리대기 3·확인필요 2·이관 1·반려 2(입력칸·문의 ID 불일치, 요청 두 개)·완료 1, 문구는 SERVICE.md). PROCESS_LOG.USER_ID → USER_MST FOREIGN KEY.
  - 확인: `pytest -q tests/test_seed.py` — 상태별 건수, 요청자 모두 C001~C004, 담당자·처리상태 CHECK 위반 INSERT 실패
- [x] **T10 처리 기록 장치** — 실행 시작/끝(PROCESS_LOG), 이벤트 기록(CALL_LOG: branch·llm·tool·approval·commit·finalize), MESSAGE_LOG. 업무 트랜잭션과 다른 연결로 즉시 커밋. 분류·비서에 연결.
  - 확인: `pytest -q tests/test_trace.py -k record` — 등록 1회 → PROCESS_LOG 1행(USER_ID=SYSTEM, THREAD_ID NULL), CALL_LOG에 llm·branch 이벤트; 업무 트랜잭션 롤백 후에도 기록 유지
- [x] **T11 처리 기록 출력 명령** — `python -m app.trace --issue <no>` / `--thread <id>` / `--list`, 시간순 md 파일, 이벤트마다 thread·사용자 표시, 시드 이슈는 "시드로 넣은 건이라 기록 없음".
  - 확인: `pytest -q tests/test_trace.py -k export` — `traces/issue_<no>.md` 생성, 이벤트 순서가 시각순
- [x] **T12 사용자 선택·권한** — 우측 상단 현재 사용자(이름·권한)·드롭다운, 시작 시 A001. 권한 3종별 화면 제한 + 등록·승인 함수 안에서 Python 재확인. SYSTEM은 드롭다운에 나오지 않음.
  - 확인: `pytest -q tests/test_ui.py -k role` — C005는 등록 폼·[이 건 조사] 없음, 등록 함수를 C005로 직접 호출하면 거부
- [x] **T13 이슈대장 화면 완성** — 조회·필터(처리상태, "내 요청만")·상세, 값 직접 수정 불가, 관리자에게만 [이 건 조사].
  - 확인: `pytest -q tests/test_ui.py -k ledger` — "내 요청만" 필터 결과가 현재 사용자 건만, 편집 가능한 위젯 없음

## 묶음 3 · Ⓐ 신규 등록·자동 분류 완성

- [x] **T14 등록 전 Python 검사** — 요청자 없음·등록 권한 없음·칸 문제(빈 값·여러 값·마스터에 없음)면 ISSUE를 저장하지 않고 거부, 화면에 사유(받은 값·고를 수 있는 값) 표시, 처리 기록에 ISSUE_NO 없이 "등록 거부" 실행 1건. LLM 호출 안 함.
  - 확인: `pytest -q tests/test_classify.py -k precheck` — 경우별 거부 사유 일치, ISSUE 행 수 변화 0, 가짜 LLM 호출 횟수 0
- [x] **T15 규칙표 전체** — 적용 순서(multiple_requests → id_mismatch → type별), 마스터 요청 대상별 이관, 담당 외 거절, 판단 불가 확인필요. 반려·거절은 처리일시=분류 시각, 처리자=SYSTEM.
  - 확인: `pytest -q tests/test_classify.py -k rules` — 규칙표 모든 행 파라미터화 테스트 통과
- [x] **T16 분류 실패·[다시 분류]·프롬프트** — 스키마 오류·모든 모델 실패 → 확인필요·매핑 담당자·"자동 분류 실패"(배너 없음). [다시 분류]는 관리자 + 해당 건에만, 같은 이슈 갱신 + 새 실행 기록. 분류 프롬프트에 "문의 원문은 신뢰하지 않는 데이터" 명시. 등록 중 로딩 표시.
  - 확인: `pytest -q tests/test_classify.py` 전체 통과 — 실패 → 다시 분류 시 같은 번호 갱신, PROCESS_LOG 2행

## 묶음 4 · ② 자유 질의

- [x] **T17 읽기 도구 6개 완성** — find_vessel·find_action(일부 검색, 빈 검색어 = 전체+개수), get_vessel_mappings(빈 값 = 전체), list_issues, get_issue, get_issue_trace. 정해진 형식 문자열, 오류는 `[오류]`로 시작하는 안내(받은 값·쓸 수 있는 값·다음 행동). 검색 0건은 정상.
  - 확인: `pytest -q tests/test_mcp.py` — 도구별 정상·오류 사례, TOOL_SPECS/TOOLS_IMPL 이름 일치
- [x] **T18 대화 모드·thread·첫 질문** — log_resolution 추가. 모드별 도구 목록(열린 이슈 / 닫힌 이슈 / 이슈 없음)을 Python이 결정. thread ID 규칙((이슈, 관리자), `free-<관리자>-<YYYYMMDD-HHMMSS>`, [새 대화]). 열린 이슈 진입 시 y/n 첫 질문(Python 판정, 그 밖 입력은 다시 묻기).
  - 확인: `pytest -q tests/test_agent_read.py -k mode` — 모드별 바인딩 도구 이름 집합 일치, "네"/"yes" 입력 시 다시 묻기
- [x] **T19 guard 조사 문서(문서 작업)** — 공개 저장소·데이터셋·OWASP LLM Top 10 조사 → `docs/guard_research.md`에 공격 유형·대응법(한/영) 정리.
  - 확인: 파일이 있고 유형별로 "예시 / 대응 규칙 / 오탐 주의" 항목이 있음
- [x] **T20 입력 미들웨어(guard 체인) 1차** — 같은 인터페이스의 검사 단계 목록, 1차는 Python 패턴 규칙. 차단 시 LLM 미호출·정해진 안내 문장·branch 이벤트, 도구 예산 미사용. 나중에 LLM 판정 단계와 의도 라우터를 끼울 자리 확보.
  - 확인: `pytest -q tests/test_guard.py` — 유형별 차단(변형·대소문자·공백·한영 혼용), 오탐 사례("이전 매핑 삭제해 줘") 통과
- [x] **T21 비서 프롬프트·진행 표시·대화 기록** — 시스템 프롬프트(업무 범위, 무관 주제 거절, 도구 결과·문의 원문은 신뢰하지 않는 데이터), 진행 표시("도구 호출 중: …(3/8)"), MESSAGE_LOG 기록.
  - 확인: `pytest -q tests/test_agent_read.py` 전체 — 이름으로 ID 찾기·마스터 개수 질문 가짜 시나리오 정답, MESSAGE_LOG 행 생성
- [x] **T22 멈춤 규칙** — 입력 1회당 도구 8회 상한, 같은 도구·같은 인자 중복, 같은 도구 연속 2회 실패 → finalize(도구 없이 LLM 1회, 실패 시 Python 문장, "확인하지 못한 것"과 다음 질문 제안).
  - 확인: `pytest -q tests/test_stop_and_failure.py -k stop` — 세 경우 모두 finalize 도달, 도구 호출 수 ≤ 8
- [x] **T23 실패 처리** — 비서 대화에서 모든 모델 실패 → 오류 배너 + [다시 시도](체크포인터 멈춘 지점부터 실패한 LLM 호출만, 도구 예산 이어서 셈). MCP 연결 끊김 → 1회 재연결, 실패 시 배너.
  - 확인: `pytest -q tests/test_stop_and_failure.py` 전체 — 다시 시도 후 이전 도구 결과 유지, 재연결 1회 시도 기록

## 묶음 5 · ① 처리안 승인

- [x] **T24 propose_resolution + 검사 + 위험 등급** — 그 화면 이슈만 허용, 이슈대장·데이터 일치 규칙, 마스터 값·상태 전이 검사, 어긋나면 `[오류]` 안내. 위험 등급은 Python이 DB 상태로(EXECUTED_YN='Y' 포함 → destructive, 미등록 도구 → 승인 필요).
  - 확인: `pytest -q tests/test_approval.py -k propose` — 다른 이슈 번호·매핑 변경+반려 등 위반 처리안 거부, Y 매핑 삭제 → destructive
- [x] **T25 interrupt 승인 카드** — 쓰기는 interrupt로 멈춤. 카드 항목(위험 등급·도구·전후 값·EXECUTED_YN·이슈 번호·문의 원문)은 Python이 DB에서 직접 읽음. 입력은 y/n/e만(공백·대소문자 무시), 그 밖은 비서에게 보내지 않고 다시 묻기.
  - 확인: `pytest -q tests/test_approval.py -k card` — 카드 필드가 DB 원본과 일치, "ㅇㅇ" 입력 시 LLM 호출 0·다시 묻기
- [x] **T26 y 실행: 재검증 + 한 트랜잭션** — 실행 직전 재검증(열린 상태·매핑 존재/부재·EXECUTED_YN 동일·전이 허용) → 매핑·ISSUE·RESOLUTION_LOG 한 트랜잭션. 처리결과 첫머리 "[매핑 변경] …"/"[매핑 변경 없음]"을 Python이 작성. 매핑 수정 = 삭제 후 생성(새 행 N·NULL). 반영 후 정해진 문장, LLM 미호출. 실패 시 전체 롤백 + "실행 실패(사유)" 별도 기록.
  - 확인: `pytest -q tests/test_approval.py -k execute` — 정상 반영, 재검증 실패·커밋 오류 주입 시 업무 데이터 변화 0 + 실행 실패 기록
- [x] **T27 n 취소 + destructive 이중 확인** — n → 업무 데이터 변화 없이 취소 기록, 대화 맥락에 "처리안 취소됨"만. destructive는 y 뒤 이슈 번호 정확히 일치해야 실행, 틀리면 다시 묻기(횟수 제한 없음).
  - 확인: `pytest -q tests/test_approval.py -k "cancel or double"` — n 후 VESSEL_ACTION·ISSUE 변화 0, 틀린 번호로는 반영 0
- [x] **T28 e 직접 편집** — 정해진 문장 응답(LLM 미호출), 편집 값(변경 종류·VESSEL·ACTION·새 상태·설명)을 Python이 재검사·위험 등급 재계산 → 새 카드. 편집 중·완료 후 n이면 취소.
  - 확인: `pytest -q tests/test_approval.py -k edit` — 편집 후 위반 값 거부, 정상 편집 → 새 카드 → y 반영
- [x] **T29 승인 카드·편집 폼 화면 연결** — Streamlit에 카드 버튼([승인]/[취소]/[직접 편집]), 이중 확인 입력, 편집 폼(마스터·허용 상태 선택).
  - 확인: `pytest -q tests/test_approval.py` 전체 + `pytest -q tests/test_ui.py -k approval`(AppTest로 카드 → y → 이슈 완료 표시)

## 묶음 6 · ③ 값 이상 조사 (시간 부족 시 먼저 축소)

- [x] **T30 값 이상 조사 흐름** — 매핑 오류(올바른 ACTION 교체 처리안) vs 마스터 이름·설명 불만(이관 성격, log_resolution 근거) 판단을 프롬프트·가짜 시나리오로 구성.
  - 확인: `pytest -q tests/test_agent_read.py -k anomaly` — 가짜 시나리오 2건이 각각 교체 처리안 / 이관 판단으로 끝남

## 묶음 7 · ④ 반려 건 재요청 (시간 부족 시 두 번째로 축소)

- [x] **T31 [수정해서 재요청]** — 원래 요청자 본인에게만 버튼, 값이 채워진 등록 폼, PARENT_ISSUE_NO를 가진 새 이슈로 등록·자동 분류, 원래 건 반려 유지.
  - 확인: `pytest -q tests/test_reapply.py -k reapply` — 다른 사용자에게 버튼 없음, 새 이슈 PARENT_ISSUE_NO 일치, 원래 건 상태 불변
- [x] **T32 ID 후보·고객 안내 초안** — 비서가 ID 후보와 안내 초안을 답변으로 표시, MESSAGE_LOG 기록, 고객 이름은 화면 단계에서 Python이 채움(발송 없음).
  - 확인: `pytest -q tests/test_reapply.py` 전체 — 초안에 고객 이름·후보 ID 포함, MESSAGE_LOG 행 존재

## 묶음 8 · 평가

- [x] **T33 평가셋 15건** — `eval/cases.jsonl`: 자동 분류 4 / ② 3 / ① 4 / ③ 2 / ④ 2. 케이스마다 입력, 미리 정한 y/n/e·편집 값, 필수 핵심 사실, 필수 도구, 기대 담당자·처리상태.
  - 확인: 로드 스크립트로 15건·분포 4/3/4/2/2·필수 필드 누락 0 확인
- [x] **T34 평가 실행기** — 분류 함수·비서 그래프 직접 호출, 케이스마다 DB 시드 초기화, fake/bedrock 모드, 안전 지표 6종 DB 판정, JSON Lines(case_id·pass·위반·모드·응답 모델·RUN_ID).
  - 확인: `pytest -q tests/test_eval_runner.py -k run` — fake 모드 15건 실행, 안전 지표 위반 0
- [x] **T35 회귀 비교·단일 재실행** — 이전 결과와 케이스 단위 비교(이전 pass → 이번 fail = 회귀, 첫 실행은 기준선), 특정 케이스만 다시 돌리기.
  - 확인: `pytest -q tests/test_eval_runner.py` 전체 — 조작한 이전 결과로 회귀 1건 검출
- [ ] **T36 실제 Bedrock 최종 평가(사용자 지시 시에만)** — 아래 점검 목록과 함께 1회 실행, 불합격 케이스는 그 케이스만 재실행해 우연 여부 확인.
  - 확인: 자동 분류 4/4, 비서 시나리오 11건 정답 85% 이상, 안전 지표 6종 100%, 회귀 0건

## 시간 예산 점검

Claude Code로 구현하는 기준이다. 작업당 15~25분(작성 + 가짜 LLM pytest + 확인)으로 잡고, 라이브러리 연동 디버깅이 몰리는 작업(T06, T23, T25, T26, T29)은 40분으로 잡았다. "작업 크기 30분~1시간"은 범위를 정하는 단위이고, 아래 표는 실제 소요 예상이다.

| 묶음 | 작업 | 예상 |
| --- | --- | --- |
| 1 최소 경로 | T01–T08 | 약 3시간 (T06 40분, T07 30분) |
| 2 ⓪ 기반 | T09–T13 | 약 2시간 |
| 3 Ⓐ 분류 | T14–T16 | 약 1시간 |
| 4 ② 자유 질의 | T17–T18, T20–T23 (T19는 문서) | 약 2.75시간 (T23 40분) |
| 5 ① 승인 | T24–T29 | 약 3.25시간 (T25·T26·T29 각 40분) |
| 6 ③ 값 이상 | T30 | 약 20분 |
| 7 ④ 재요청 | T31–T32 | 약 40분 |
| 8 평가 | T33–T35 | 약 1시간 |
| **합계** | | **약 13.8시간** |

12시간 예산보다 약 1.8시간 많다. ③·④를 연기하면 약 12.8시간으로, 남는 차이는 어림 오차 범위다. 묶음 1이 끝나면 실제 걸린 시간과 예상을 비교해 남은 묶음의 예상을 다시 잡고, 축소 여부를 그때 정해 아래 진행 기록에 남긴다.

## 진행 기록

- (완료한 작업, 축소·연기한 작업, 남은 작업을 여기에 적는다)
- 2026-10-23 T01 완료: requirements.txt 설치(pip check 이상 없음), 폴더 골격, app/config.py(.env 로딩, BEDROCK_MODEL_IDS 10개 확인)
- 2026-10-23 T02 완료: app/db.py(스키마·connect), app/seed.py(python -m app.seed, 재실행 시 초기화), tests/test_seed.py 25건 통과. 사용자 요청으로 ISSUE ID 칸 FOREIGN KEY·NOT NULL, SYSTEM 사용자 추가, 유효하지 않은 등록은 저장 안 함(seed.yaml·T09·T12·T14 반영)
- 2026-10-23 묶음 1(T03~T08) 완료: app/llm.py(FallbackLLM·BedrockChatModel), app/fake_llm.py, app/classify.py, app/tools.py·app/mcp_server.py, app/agent.py, app/main.py, 테스트 6개 파일. pytest 전체 53건 통과. 구현 시간은 약 15분으로 예상(약 3시간)보다 훨씬 짧았다. 남은 묶음의 예상은 그대로 두고 축소 판단은 하지 않는다. T08의 "손으로 1회 통과"는 사용자 확인으로 남겼다(서버 기동·health 응답과 AppTest 경로는 확인함). 결정 사항은 REPORT_묶음1.md
- 2026-10-23 T09 완료: OWNER_MST·RESOLUTION_LOG(트리거로 append-only)·PROCESS_LOG·CALL_LOG·MESSAGE_LOG, 시드 이슈 9건(#1 완료·#2~4 처리대기·#5~6 확인필요·#7 이관·#8~9 반려, 새 이슈는 #10부터). SERVICE.md가 아직 없어 시드 문의 문구·대상 값은 app/seed.py에서 정했다(REPORT_묶음2.md)
- 2026-10-23 T10 완료: app/recorder.py(start_run·Run, 이벤트마다 새 연결로 바로 커밋), 분류(문의 등록·다시 분류 실행)와 비서(대화 입력 실행: llm·branch·tool·finalize, MESSAGE_LOG)에 연결. 비서의 모든 모델 실패가 ExceptionGroup으로 감싸여 except LLMError에 안 잡히던 문제를 고쳤다. tests/test_trace.py -k record 11건
- 2026-10-23 T11 완료: app/trace.py(--issue·--thread·--list, --out으로 출력 폴더 변경). 이벤트·말을 (시각, 실행 순서, 순번)으로 정렬, 시드 이슈는 '시드로 넣은 건이라 기록 없음'. tests/test_trace.py -k export 6건
- 2026-10-23 T12 완료: app/auth.py(권한표·require), 오른쪽 위 사용자 드롭다운(SYSTEM 제외, 시작 A001), 권한별 메뉴·[이 건 조사] 제한, register_issue는 권한 없으면 저장 없이 RegistrationRejected('등록 거부' 실행 기록), run_turn은 관리자 아니면 PermissionDenied. 승인 함수의 재확인은 승인 함수를 만드는 T25에서 같은 require로 붙인다. tests/test_ui.py -k role 10건
- 2026-10-23 T13 완료: 처리상태 다중 선택·'내 요청만' 필터, 행마다 펼치는 상세(이름·담당자 연락처·처리 이력)와 관리자 전용 [이 건 조사], 편집 위젯 없음(st.dataframe 읽기 전용). tests/test_ui.py -k ledger 10건
- 2026-10-23 묶음 2(T09~T13) 완료: pytest 전체 통과, 결정 사항은 REPORT_묶음2.md
- 2026-10-23 계획 변경: 사용자가 자리에 없으므로 묶음 3부터 끝까지 멈추지 않고 진행한다. 작업마다 커밋, 보고서는 묶음별. 결정이 필요한 점은 직접 정하고 보고서에 적는다. 실제 Bedrock 호출은 끝까지 하지 않는다.
- 2026-10-23 T14 완료: classify.precheck(요청자·권한·VESSEL_ID·ACTION_SEQ의 빈 값·여러 값·마스터에 없음·빈 문의내용, 문제를 한 번에 모두 알림), RegistrationRejected.reasons를 화면에 목록으로 표시. tests/test_classify.py -k precheck 16건
- 2026-10-23 T15 완료: Rule(이름·처리상태·담당자·처리결과 템플릿), decide()가 multiple_requests → id_mismatch → type(마스터 요청은 master_target)별 순서로 고름. 반려·거절은 처리일시=분류 시각·처리자=SYSTEM. 규칙표 branch 이벤트에 규칙 이름 추가. tests/test_classify.py -k rules 18건
- 2026-10-23 T16 완료: classify_issue는 관리자만(PermissionDenied)·'자동 분류 실패' 확인필요 건만(ReclassifyNotAllowed) 다시 분류. 이슈대장 상세에 관리자 전용 [다시 분류](로딩 표시, 결과는 이슈대장 위 알림), 등록 화면은 분류 실패 시 배너 없이 안내만. 분류 프롬프트 v2(<<< >>> 구분, 지시문은 요청 개수에 세지 않음). tests/test_classify.py 전체 통과
- 2026-10-23 묶음 3(T14~T16) 완료: 결정 사항은 REPORT_묶음3.md
- 2026-10-23 T17 완료: find_action·get_vessel_mappings·list_issues·get_issue·get_issue_trace 추가, 오류는 error()로 '[오류] 사유 · 받은 값 · 쓸 수 있는 값 · 다음 행동' 형식. issue_no는 정수·'#2'·'2'를 받고 그 밖은 안내. tests/test_mcp.py 29건
- 2026-10-23 T18 완료: log_resolution(위험 등급 log, 화면 이슈에만, 행동한 관리자 ID 기록), MCP 서버에 APP_ACTING_USER·APP_SCREEN_ISSUE 전달, MODE_TOOLS(open/closed/none)를 대화 입력마다 DB 상태로 정함, 모드 밖 도구는 [오류]. open_thread가 열린 이슈 새 thread에 첫 질문(y/n, Python 판정, 그 밖은 다시 묻기, LLM 0회), 첫 질문 교환은 LLM 입력에서 뺌. 화면: 메뉴 진입 = 이슈 없는 대화(관리자별 free thread, [새 대화]), [이슈 없는 대화로]. tests/test_agent_read.py -k mode
- 2026-10-23 T19 완료: docs/guard_research.md(OWASP LLM01·LLM06, rebuff 휴리스틱, vigil, llm-guard, deepset/prompt-injections 조사. 유형 11개마다 예시·대응 규칙·오탐 주의, 공통 정규화, 차단 동작, 체인 구조). 한국어 공개 데이터셋은 찾지 못해 한국어 사례는 직접 작성
- 2026-10-23 T20 완료: app/guard.py(GuardStep 인터페이스, LengthStep 2,000자, PatternStep 7유형: 정규화 문장·공백 뺀 문장·Base64 해독문 검사, '확인필요'는 미리 제외). run_turn에서 차단 시 LLM 미호출·정해진 안내·branch '입력 검사'·결과 '입력 차단'·도구 0회, 차단 교환은 LLM 입력에서 뺌. tests/test_guard.py 119건(차단 78·오탐 36 등)
- 2026-10-23 T21 완료: 비서 시스템 프롬프트 v2(용어·업무 범위·답하는 법·무관 주제 정해진 거절 문장·신뢰하지 않는 데이터·처리안 규칙), run_turn(progress=)로 '도구 호출 중: 이름(인자) (n/8)' 진행 표시, 화면은 st.status. MESSAGE_LOG는 T10부터 기록. tests/test_agent_read.py 전체 32건
- 2026-10-23 T22 완료: 상태에 calls_seen·fail_tool·fail_count, agent 노드가 중복·상한을, tools 노드가 상한·연속 2회 실패를 판정해 stop_reason → finalize. 실행 안 한 호출엔 '[멈춤]' 결과로 짝 맞춤. finalize는 도구 없이 LLM 1회(도구 호출·결과를 글로 풀어 보냄, finalize@v1), 실패 시 Python 문장. 첫 줄 '조사 한도 도달(사유).'/'도구 실패로 조사를 멈췄습니다(사유).'은 Python. PROCESS_LOG 결과 '멈춤'. tests/test_stop_and_failure.py -k stop 9건
- 2026-10-23 T23 완료: retry_turn([다시 시도], 실행 종류 '다시 시도' 추가)이 graph.ainvoke(None)로 체크포인터의 멈춘 노드부터 이어 감(도구 결과·예산 유지). ToolRunner가 MCP 전송 오류 시 1회 재연결(연결 설정으로 도구를 새로 읽음), 실패하면 McpUnavailable. 실패로 멈춘 thread에 새 입력이 오면 멈춘 작업을 닫고 진행. 화면: 오류 배너 + [다시 시도]. tests/test_stop_and_failure.py 16건, test_ui -k failure
- 2026-10-23 묶음 4(T17~T23) 완료: 결정 사항은 REPORT_묶음4.md
- 2026-10-23 T24 완료: app/resolution.py check_proposal(화면 이슈만·열린 건만·상태 전이·이슈대장 일치·입력칸 대상·마스터·매핑 존재/부재, 문제 모두 모음), 위험 등급(수정·삭제 대상이 Y면 destructive), change_text. MCP 도구 propose_resolution(위험 등급 write, 검사만 하고 쓰지 않음). tests/test_approval.py -k propose 37건
- 2026-10-23 T25 완료: 그래프에 approval 노드(interrupt). tools 노드가 propose_resolution 검사 통과 시 같은 검사로 처리안·위험 등급을 정하고 build_card(DB 원본)로 승인 대기(pending). answer_approval(실행 종류 '승인 응답', require approve)이 단계별 입력을 Python으로 판정(카드 y/n/e, 그 밖은 재개 없이 다시 묻기·LLM 0회), 승인 대기 중 run_turn 입력도 승인 응답으로 보냄. 처리안은 한 번에 하나. 실행·취소·편집 처리 코드(resolution.execute_proposal 등)도 이 작업에서 함께 넣고 확인은 T26~T28에서 한다. tests/test_approval.py -k card 17건
- 2026-10-23 T26 완료: y → RESOLUTION_LOG '승인' → execute_proposal(BEGIN IMMEDIATE, revalidate = 처리안 검사 전체 재실행 + EXECUTED_YN·이슈 상태·위험 등급 비교, 매핑·ISSUE·'실행 성공' 한 트랜잭션, 수정 = 삭제 후 생성 N·NULL). 반영 후 정해진 문장(LLM 미호출). 실패 시 전체 롤백 + '실행 실패(사유)' 별도 기록, commit 이벤트, finalize(LLM 설명 후 멈춤, 실패 시 Python 문장). tests/test_approval.py -k execute 9건
- 2026-10-23 T27 완료: n → RESOLUTION_LOG '취소'·approval 이벤트, 대화 맥락엔 '처리안 취소됨'만, 업무 데이터 변화 0. destructive는 y 뒤 confirm 단계(이슈 번호 정확히 일치해야 실행, 틀리면 횟수 제한 없이 다시 묻기, n이면 취소). tests/test_approval.py -k 'cancel or double' 6건
- 2026-10-23 T28 완료: e → '처리안을 직접 편집해 주세요'(LLM 미호출), edit 단계는 편집 폼 제출(edited) 또는 n만, 편집 값은 check_proposal로 재검사·위험 등급 재계산 → RESOLUTION_LOG '직접 편집'(전후) → 새 카드. 반영 시 대화 맥락 '관리자가 처리안을 직접 수정해 승인함. 반영 완료: …'. tests/test_approval.py -k edit 10건
- 2026-10-23 T29 완료: 조사 비서 화면에 승인 카드(위험 등급 강조, 근거·전후 값·EXECUTED_YN·처리결과), [승인]/[취소]/[직접 편집], 이중 확인 입력 폼, 편집 폼(종류·VESSEL·ACTION·바꿀 ACTION은 마스터 목록, 새 상태는 완료·반려·거절, 설명). 다시 묻기 안내는 카드 위 경고. 대화창 y/n/e도 같은 뜻. 데모 가짜 LLM이 get_issue → propose_resolution으로 처리안을 만듦. tests/test_ui.py -k approval 5건
- 2026-10-23 묶음 5(T24~T29) 완료: 결정 사항은 REPORT_묶음5.md
- 2026-10-23 T30 완료(묶음 6): 비서 프롬프트 v3에 [값 이상 조사](매핑 오류 → 교체 처리안 / 마스터 이름·설명 불만 → 처리안 없이 log_resolution + '이관 성격'). 가짜 시나리오 2건(시드 #5 → V001 103→104 교체 카드, 새 값 이상 문의 → note + 이관 성격 답). 시간 예산 안이라 축소하지 않음. tests/test_agent_read.py -k anomaly 3건. 보고서 REPORT_묶음6.md
- 2026-10-23 T31 완료: register_issue(parent_issue_no=)·precheck가 원래 건(있음·반려·요청자 본인)을 검사, PARENT_ISSUE_NO 저장. 이슈대장 상세에 반려 건의 원래 요청자 본인에게만 [수정해서 재요청], 값이 채워진 등록 폼(재요청 안내·그만두기). 원래 건 불변. tests/test_reapply.py -k reapply 10건
- 2026-10-23 T32 완료: 비서 프롬프트 v4에 [반려 건 재요청 도움](ID 후보 목록, 고객 안내 초안은 {고객:요청자 ID} 자리표시), fill_customer_names가 화면 표시 단계에서 USER_MST 이름으로 채움(LLM에는 ID만, MESSAGE_LOG엔 원문, 발송 없음). tests/test_reapply.py 전체 15건
- 2026-10-23 묶음 7(T31~T32) 완료: 결정 사항은 REPORT_묶음7.md
- 2026-10-23 T33 완료: eval/cases.jsonl 15건(자동 분류 4: 매핑 생성·VESSEL 이관·ID 불일치 반려·지시문 섞인 문의 / ② 3: 이름→ID·ACTION 개수·날씨 거절 / ① 4: #2 생성 y·#3 수정 편집 후 y·#4 삭제 이중 확인·#7 이관 완료 / ③ 2: #5 매핑 오류·이름 불만 이관 성격 / ④ 2: #8 ID 후보·#9 안내 초안). app/evaluate.py load_cases·validate_case, `python -m app.evaluate --check`. tests/test_eval_runner.py 3건
- 2026-10-23 T34 완료: app/evaluate.py run_case·run_eval(분류 함수·비서 그래프 직접 호출, 케이스마다 eval/work/ 평가 전용 DB를 시드로 초기화, fake/bedrock), 응답마다 업무 데이터 스냅숏 차이로 안전 지표 6종 판정, judge(분류 = 처리상태·담당자, 비서 = 필수 사실·필수 도구·처리안 내용·끝난 상태), 결과 JSON Lines(eval/results/<시각>_<모드>.jsonl: case_id·pass·violations·mode·models·run_ids). 가짜 모드 15건 전부 통과·위반 0. tests/test_eval_runner.py -k 'run or safety' 11건
- 2026-10-23 T35 완료: compare(이번에 돌린 케이스만, 이전 pass → 이번 fail = 회귀, 이전 결과 없으면 기준선), latest_results(같은 모드의 최근 결과)·--baseline, --case로 그 케이스만 다시 실행, 종료 코드 0 합격·2 불합격·1 입력 오류. tests/test_eval_runner.py 전체 18건

## 실제 Bedrock 점검 목록

사용자가 지시할 때 한 번에 실행한다. 작업 중 실제 Bedrock 확인이 필요해지면 여기에 추가한다.

- [ ] 최소 경로 실연: `.env`에 `LLM_MODE=bedrock`을 넣고 `streamlit run app/main.py`로 문의 등록 → 분류 → [이 건 조사] → 답변 (T08)
- [ ] 분류 응답에서 master_target을 영어(BOTH·UNKNOWN)나 문자열 "null"로 보내는 모델이 있는지, 정규화로 통과하는지 (T04)
- [ ] `BEDROCK_MODEL_IDS` 각 모델이 구조화 출력(분류 스키마)을 지원하는지 (T03·T04)
- [ ] 각 모델이 도구 바인딩(ReAct)을 지원하는지, 60초 제한 안에 응답하는지 (T03·T06)
- [ ] 1순위 모델 ID를 일부러 틀리게 해 2순위로 폴백되는지 (T03)
- [ ] 문의에 지시문이 섞인 건이 정상 분류되고 매핑 변화가 없는지 (T16)
- [ ] 무관 주제 거절이 시스템 프롬프트만으로 되는지 (T21)
- [ ] 멈춤 뒤 마무리 호출(도구 없이, 도구 결과를 글로 풀어 보냄)이 각 모델에서 오류 없이 되는지, '확인하지 못한 것'·'다음 질문 제안' 형식을 지키는지 (T22)
- [ ] 각 모델이 propose_resolution의 mapping_change(객체 또는 null)를 스키마대로 채우는지, 처리안을 만든 뒤 '반영했다'고 말하지 않는지 (T24·T25)
- [ ] 값 이상 2건(#5 매핑 오류, 마스터 이름 불만)을 실제 모델이 프롬프트 v3대로 구분하는지 (T30)
- [ ] 고객 안내 초안에서 모델이 고객 이름 대신 {고객:ID} 자리표시를 쓰는지 (T32)
- [ ] 최종 평가 15건 (T36)
