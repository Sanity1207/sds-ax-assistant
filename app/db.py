"""SQLite 연결과 스키마. 모든 연결은 connect()로 열어 FOREIGN KEY를 켠다."""

import sqlite3
from pathlib import Path

from app.config import DB_PATH

STATUSES = ("확인필요", "이관", "처리대기", "완료", "반려", "거절")
OWNER_ROLES = ("", "매핑 담당자", "VESSEL 담당자", "ACTION 담당자", "VESSEL 담당자, ACTION 담당자")
# 시스템은 SYSTEM 전용 권한이다(자동 분류가 행동한 사용자·처리자). 사람이 고를 수 없다.
USER_ROLES = ("조회 고객", "요청 고객", "관리자", "시스템")


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
"""


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
