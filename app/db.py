"""SQLite 연결과 스키마. 모든 연결은 connect()로 열어 FOREIGN KEY를 켠다."""

import sqlite3
from pathlib import Path

from app.config import db_path

STATUSES = ("확인필요", "이관", "처리대기", "완료", "반려", "거절")
OWNER_ROLES = ("", "매핑 담당자", "VESSEL 담당자", "ACTION 담당자", "VESSEL 담당자, ACTION 담당자")
# 시스템은 SYSTEM 전용 권한이다(자동 분류가 행동한 사용자·처리자). 사람이 고를 수 없다.
USER_ROLES = ("조회 고객", "요청 고객", "관리자", "시스템")
# OWNER_MST의 역할(안내용 연락처). VESSEL·ACTION 담당자는 계정이 아니다.
CONTACT_ROLES = ("매핑 담당자", "VESSEL 담당자", "ACTION 담당자")
RESOLUTION_KINDS = ("note", "승인", "취소", "직접 편집", "실행 성공", "실행 실패")
# 처리 기록의 실행 = 사람의 행동 1회에 프로그램이 반응해 멈출 때까지의 한 덩어리.
RUN_KINDS = ("문의 등록", "다시 분류", "대화 입력", "승인 응답")
EVENT_KINDS = ("branch", "llm", "tool", "approval", "commit", "finalize")
SPEAKERS = ("관리자", "비서")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


# ID는 모두 TEXT로 다룬다(ACTION_SEQ '101' 포함). ISSUE 번호만 자동 증가 정수.
# ISSUE에는 유효한 값만 저장한다: ID 칸은 모두 FOREIGN KEY라 빈 값·여러 값·마스터에 없는 값은
# DB가 막는다. 등록 함수는 그 전에 Python으로 검사해 저장하지 않고 거부한다.
SCHEMA = f"""
CREATE TABLE VESSEL_MST (
    VESSEL_ID   TEXT PRIMARY KEY,
    VESSEL_NAME TEXT NOT NULL
);

CREATE TABLE ACTION_MST (
    ACTION_SEQ         TEXT PRIMARY KEY,
    ACTION_DESCRIPTION TEXT NOT NULL
);

CREATE TABLE VESSEL_ACTION (
    VESSEL_ID   TEXT NOT NULL REFERENCES VESSEL_MST(VESSEL_ID) ON DELETE RESTRICT,
    ACTION_SEQ  TEXT NOT NULL REFERENCES ACTION_MST(ACTION_SEQ) ON DELETE RESTRICT,
    EXECUTED_YN TEXT NOT NULL CHECK (EXECUTED_YN IN ('Y', 'N')),
    EXECUTED_AT TEXT,
    PRIMARY KEY (VESSEL_ID, ACTION_SEQ),
    CHECK ((EXECUTED_YN = 'Y' AND EXECUTED_AT IS NOT NULL)
        OR (EXECUTED_YN = 'N' AND EXECUTED_AT IS NULL))
);

CREATE TABLE USER_MST (
    USER_ID   TEXT PRIMARY KEY,
    USER_NAME TEXT NOT NULL,
    ROLE      TEXT NOT NULL CHECK (ROLE IN ({_in(USER_ROLES)}))
);

CREATE TABLE ISSUE (
    ISSUE_NO        INTEGER PRIMARY KEY AUTOINCREMENT,
    RECEIVED_AT     TEXT NOT NULL,
    REQUESTER_ID    TEXT NOT NULL REFERENCES USER_MST(USER_ID),
    VESSEL_ID       TEXT NOT NULL REFERENCES VESSEL_MST(VESSEL_ID),
    ACTION_SEQ      TEXT NOT NULL REFERENCES ACTION_MST(ACTION_SEQ),
    INQUIRY         TEXT NOT NULL,
    OWNER_ROLE      TEXT NOT NULL DEFAULT '' CHECK (OWNER_ROLE IN ({_in(OWNER_ROLES)})),
    STATUS          TEXT NOT NULL CHECK (STATUS IN ({_in(STATUSES)})),
    RESULT          TEXT NOT NULL DEFAULT '',
    PROCESSED_AT    TEXT,
    PROCESSOR_ID    TEXT REFERENCES USER_MST(USER_ID),
    PARENT_ISSUE_NO INTEGER REFERENCES ISSUE(ISSUE_NO)
);

CREATE TABLE OWNER_MST (
    OWNER_ROLE TEXT PRIMARY KEY CHECK (OWNER_ROLE IN ({_in(CONTACT_ROLES)})),
    OWNER_NAME TEXT NOT NULL,
    CONTACT    TEXT NOT NULL
);

-- 처리안의 승인·취소·실행 결과와 비서의 note. append-only: 고치거나 지우면 트리거가 막는다.
CREATE TABLE RESOLUTION_LOG (
    LOG_ID        INTEGER PRIMARY KEY AUTOINCREMENT,
    ISSUE_NO      INTEGER NOT NULL REFERENCES ISSUE(ISSUE_NO),
    KIND          TEXT NOT NULL CHECK (KIND IN ({_in(RESOLUTION_KINDS)})),
    CONTENT       TEXT NOT NULL,
    PROPOSAL_JSON TEXT,
    USER_ID       TEXT NOT NULL REFERENCES USER_MST(USER_ID),
    LOGGED_AT     TEXT NOT NULL
);

CREATE TRIGGER RESOLUTION_LOG_NO_UPDATE BEFORE UPDATE ON RESOLUTION_LOG
BEGIN SELECT RAISE(ABORT, 'RESOLUTION_LOG는 append-only입니다'); END;

CREATE TRIGGER RESOLUTION_LOG_NO_DELETE BEFORE DELETE ON RESOLUTION_LOG
BEGIN SELECT RAISE(ABORT, 'RESOLUTION_LOG는 append-only입니다'); END;

-- 처리 기록(디버깅용). 실행 1회 = PROCESS_LOG 1행. ISSUE_NO·THREAD_ID·USER_ID는 여기에만 둔다.
CREATE TABLE PROCESS_LOG (
    RUN_ID      TEXT PRIMARY KEY,
    ISSUE_NO    INTEGER REFERENCES ISSUE(ISSUE_NO),
    THREAD_ID   TEXT,
    USER_ID     TEXT NOT NULL REFERENCES USER_MST(USER_ID),
    RUN_KIND    TEXT NOT NULL CHECK (RUN_KIND IN ({_in(RUN_KINDS)})),
    STARTED_AT  TEXT NOT NULL,
    ENDED_AT    TEXT,
    RESULT      TEXT,
    TOOL_CALLS  INTEGER NOT NULL DEFAULT 0,
    MODEL_ID    TEXT,
    STOP_REASON TEXT
);

-- 실행 안의 이벤트 1건 = 1행. SEQ는 같은 실행의 MESSAGE_LOG와 한 순번을 나눠 쓴다(시간순 출력용).
CREATE TABLE CALL_LOG (
    RUN_ID     TEXT NOT NULL REFERENCES PROCESS_LOG(RUN_ID),
    SEQ        INTEGER NOT NULL,
    EVENT_KIND TEXT NOT NULL CHECK (EVENT_KIND IN ({_in(EVENT_KINDS)})),
    NAME       TEXT NOT NULL,
    INPUT      TEXT,
    OUTPUT     TEXT,
    MODEL_ID   TEXT,
    SUCCESS    TEXT NOT NULL CHECK (SUCCESS IN ('Y', 'N')),
    ERROR      TEXT,
    ELAPSED_MS INTEGER,
    CREATED_AT TEXT NOT NULL,
    PRIMARY KEY (RUN_ID, SEQ)
);

-- 실행 안에서 오간 말 1건 = 1행. ④ 고객 안내 초안도 여기 남는다(발송 없음).
CREATE TABLE MESSAGE_LOG (
    RUN_ID     TEXT NOT NULL REFERENCES PROCESS_LOG(RUN_ID),
    SEQ        INTEGER NOT NULL,
    SPEAKER    TEXT NOT NULL CHECK (SPEAKER IN ({_in(SPEAKERS)})),
    CONTENT    TEXT NOT NULL,
    CREATED_AT TEXT NOT NULL,
    PRIMARY KEY (RUN_ID, SEQ)
);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """path를 비우면 config.db_path()(APP_DB_PATH 또는 data/app.db)를 연다."""
    # 처리 기록은 업무 연결과 다른 연결로 쓰므로, 잠깐 겹치면 기다리도록 시간 제한을 둔다.
    conn = sqlite3.connect(path or db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
