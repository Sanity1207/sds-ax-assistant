import sqlite3

import pytest

from app.db import connect
from app.seed import ISSUES, init_db

NEW_NO = len(ISSUES) + 1  # 시드 이슈 다음 번호


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "app.db"
    init_db(path)
    conn = connect(path)
    yield conn
    conn.close()


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_seed_counts(db):
    assert count(db, "VESSEL_MST") == 5
    assert count(db, "ACTION_MST") == 6
    assert count(db, "VESSEL_ACTION") == 7
    assert count(db, "USER_MST") == 8
    assert count(db, "OWNER_MST") == 3


def test_executed_mappings(db):
    rows = db.execute(
        "SELECT VESSEL_ID, ACTION_SEQ, EXECUTED_AT FROM VESSEL_ACTION "
        "WHERE EXECUTED_YN = 'Y' ORDER BY VESSEL_ID, ACTION_SEQ").fetchall()
    assert [tuple(r) for r in rows] == [
        ("V001", "101", "2026-10-20 09:00:00"),
        ("V001", "102", "2026-10-20 10:30:00"),
        ("V002", "103", "2026-10-21 14:00:00"),
        ("V004", "105", "2026-10-21 16:20:00"),
    ]
    assert count(db, "VESSEL_ACTION WHERE EXECUTED_YN = 'N' AND EXECUTED_AT IS NULL") == 3


def test_user_roles(db):
    roles = dict(db.execute("SELECT USER_ID, ROLE FROM USER_MST").fetchall())
    assert [u for u, r in roles.items() if r == "요청 고객"] == ["C001", "C002", "C003", "C004"]
    assert roles["C005"] == "조회 고객"
    assert roles["A001"] == roles["A002"] == "관리자"
    assert roles["SYSTEM"] == "시스템"


@pytest.mark.parametrize("yn, at", [("Y", None), ("N", "2026-10-22 09:00:00"), ("X", None)])
def test_executed_check(db, yn, at):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO VESSEL_ACTION VALUES ('V003', '104', ?, ?)", (yn, at))


def test_mapping_primary_key(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO VESSEL_ACTION VALUES ('V001', '101', 'N', NULL)")


@pytest.mark.parametrize("vessel, action", [("V999", "101"), ("V001", "999")])
def test_mapping_foreign_key(db, vessel, action):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO VESSEL_ACTION VALUES (?, ?, 'N', NULL)", (vessel, action))


def test_master_delete_restricted(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM VESSEL_MST WHERE VESSEL_ID = 'V001'")


def insert_issue(conn, **overrides):
    values = dict(RECEIVED_AT="2026-10-23 09:00:00", REQUESTER_ID="C001", VESSEL_ID="V003",
                  ACTION_SEQ="104", INQUIRY="V003에 104 추가", OWNER_ROLE="매핑 담당자",
                  STATUS="처리대기")
    values.update(overrides)
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    return conn.execute(f"INSERT INTO ISSUE ({cols}) VALUES ({marks})", list(values.values())).lastrowid


def test_issue_checks(db):
    assert insert_issue(db) == NEW_NO
    with pytest.raises(sqlite3.IntegrityError):
        insert_issue(db, STATUS="처리중")
    with pytest.raises(sqlite3.IntegrityError):
        insert_issue(db, OWNER_ROLE="고객")


@pytest.mark.parametrize("bad", [
    {"VESSEL_ID": "V999"},
    {"VESSEL_ID": ""},
    {"VESSEL_ID": "V001, V002"},
    {"VESSEL_ID": None},
    {"ACTION_SEQ": "999"},
    {"ACTION_SEQ": ""},
    {"ACTION_SEQ": "101, 102"},
    {"ACTION_SEQ": None},
    {"REQUESTER_ID": "X999"},
    {"PROCESSOR_ID": "X999"},
    {"PARENT_ISSUE_NO": 999},
])
def test_issue_rejects_invalid_values(db, bad):
    with pytest.raises(sqlite3.IntegrityError):
        insert_issue(db, **bad)


def test_issue_valid_references(db):
    parent = insert_issue(db, STATUS="반려", PROCESSED_AT="2026-10-23 09:00:01", PROCESSOR_ID="SYSTEM")
    child = insert_issue(db, PARENT_ISSUE_NO=parent)
    assert child == parent + 1


def test_reseed_resets(tmp_path):
    path = tmp_path / "app.db"
    init_db(path)
    conn = connect(path)
    with conn:
        insert_issue(conn)
        conn.execute("DELETE FROM VESSEL_ACTION")
    conn.close()

    init_db(path)
    conn = connect(path)
    assert count(conn, "VESSEL_ACTION") == 7
    assert count(conn, "ISSUE") == len(ISSUES)
    assert insert_issue(conn) == NEW_NO
    conn.close()


def test_foreign_keys_on_every_connection(db):
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_reset_checkpoints(tmp_path):
    from app.seed import reset_checkpoints

    path = tmp_path / "checkpoints.db"
    path.write_bytes(b"x")
    (tmp_path / "checkpoints.db-wal").write_bytes(b"x")
    reset_checkpoints(path)
    reset_checkpoints(path)  # 없어도 오류 없음
    assert list(tmp_path.iterdir()) == []


# ---- T09: 나머지 테이블과 시드 이슈 9건 ----

def test_owner_contacts(db):
    rows = [tuple(r) for r in db.execute("SELECT * FROM OWNER_MST ORDER BY CONTACT")]
    assert rows == [("매핑 담당자", "윤도현", "내선 1001"), ("VESSEL 담당자", "강수진", "내선 1002"),
                    ("ACTION 담당자", "오세훈", "내선 1003")]


def test_seed_issue_statuses(db):
    by_status = dict(db.execute("SELECT STATUS, COUNT(*) FROM ISSUE GROUP BY STATUS").fetchall())
    assert by_status == {"처리대기": 3, "확인필요": 2, "이관": 1, "반려": 2, "완료": 1}
    assert count(db, "ISSUE") == 9


def test_seed_issue_requesters_are_request_customers(db):
    requesters = {r[0] for r in db.execute("SELECT REQUESTER_ID FROM ISSUE")}
    assert requesters <= {"C001", "C002", "C003", "C004"}
    assert count(db, "ISSUE I JOIN USER_MST U ON U.USER_ID = I.REQUESTER_ID WHERE U.ROLE <> '요청 고객'") == 0


def test_seed_issue_kinds(db):
    rows = {r["ISSUE_NO"]: r for r in db.execute("SELECT * FROM ISSUE")}
    # 처리대기 3: 매핑 생성·수정·수행된 Y 매핑 삭제
    pending = [r for r in rows.values() if r["STATUS"] == "처리대기"]
    assert [r["RESULT"].split(" 요청")[0] for r in pending] == ["매핑 생성", "매핑 수정", "매핑 삭제"]
    delete = pending[2]
    assert db.execute("SELECT EXECUTED_YN FROM VESSEL_ACTION WHERE VESSEL_ID = ? AND ACTION_SEQ = ?",
                      (delete["VESSEL_ID"], delete["ACTION_SEQ"])).fetchone()[0] == "Y"
    # 확인필요 2: 값 이상, 자동 분류 실패
    check = sorted(r["RESULT"] for r in rows.values() if r["STATUS"] == "확인필요")
    assert check[0] == "값 이상 요청으로 분류. 근거: V001의 103 연결이 맞지 않는 것 같다는 문의"
    assert check[1] == "자동 분류 실패"
    # 이관 1: ACTION 이름 변경 → ACTION 담당자
    moved = [r for r in rows.values() if r["STATUS"] == "이관"]
    assert len(moved) == 1 and moved[0]["OWNER_ROLE"] == "ACTION 담당자"
    # 반려 2: 입력칸·문의 ID 불일치, 요청 두 개(C004)
    rejected = [r for r in rows.values() if r["STATUS"] == "반려"]
    assert rejected[0]["VESSEL_ID"] == "V001" and "V002" in rejected[0]["INQUIRY"]
    assert rejected[1]["REQUESTER_ID"] == "C004" and "요청이 여러 개" in rejected[1]["RESULT"]


def test_seed_issue_closed_fields(db):
    """닫힌 건만 처리일시·처리자가 있다. 분류 단계에서 닫힌 반려는 SYSTEM, 완료는 승인한 관리자."""
    for r in db.execute("SELECT * FROM ISSUE"):
        closed = r["STATUS"] in ("완료", "반려", "거절")
        assert (r["PROCESSED_AT"] is not None) == closed and (r["PROCESSOR_ID"] is not None) == closed
        if r["STATUS"] == "반려":
            assert r["PROCESSOR_ID"] == "SYSTEM"
        if r["STATUS"] == "완료":
            assert r["PROCESSOR_ID"] == "A001" and r["RESULT"].startswith("[매핑 변경] V002→103 생성")


def test_seed_completed_issue_matches_data(db):
    """완료 건의 매핑 변경이 실제 데이터와 RESOLUTION_LOG에 있다(이슈대장·데이터 일치)."""
    assert count(db, "VESSEL_ACTION WHERE VESSEL_ID = 'V002' AND ACTION_SEQ = '103'") == 1
    log = db.execute("SELECT * FROM RESOLUTION_LOG").fetchall()
    assert [(r["ISSUE_NO"], r["KIND"], r["USER_ID"]) for r in log] == [(1, "실행 성공", "A001")]


def test_seed_has_no_trace(db):
    for table in ("PROCESS_LOG", "CALL_LOG", "MESSAGE_LOG"):
        assert count(db, table) == 0


@pytest.mark.parametrize("column, value", [("STATUS", "처리중"), ("STATUS", ""), ("OWNER_ROLE", "고객"),
                                           ("OWNER_ROLE", "VESSEL 담당자,ACTION 담당자")])
def test_issue_status_and_owner_check(db, column, value):
    with pytest.raises(sqlite3.IntegrityError):
        insert_issue(db, **{column: value})


def test_issue_owner_both_allowed(db):
    insert_issue(db, OWNER_ROLE="VESSEL 담당자, ACTION 담당자", STATUS="이관")
    insert_issue(db, OWNER_ROLE="", STATUS="거절", PROCESSED_AT="2026-10-23 09:00:01", PROCESSOR_ID="SYSTEM")


def test_owner_role_check(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO OWNER_MST VALUES ('고객 담당자', '홍길동', '내선 9999')")


def add_resolution(conn, **overrides):
    values = dict(ISSUE_NO=2, KIND="note", CONTENT="조사 시작", PROPOSAL_JSON=None, USER_ID="A001",
                  LOGGED_AT="2026-10-23 09:00:00")
    values.update(overrides)
    return conn.execute(f"INSERT INTO RESOLUTION_LOG ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
                        list(values.values())).lastrowid


def test_resolution_log_append_only(db):
    log_id = add_resolution(db)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute("UPDATE RESOLUTION_LOG SET CONTENT = 'x' WHERE LOG_ID = ?", (log_id,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute("DELETE FROM RESOLUTION_LOG WHERE LOG_ID = ?", (log_id,))


@pytest.mark.parametrize("bad", [{"KIND": "승인함"}, {"ISSUE_NO": 999}, {"USER_ID": "X999"}, {"USER_ID": None}])
def test_resolution_log_checks(db, bad):
    with pytest.raises(sqlite3.IntegrityError):
        add_resolution(db, **bad)


def add_run(conn, **overrides):
    values = dict(RUN_ID="run-1", ISSUE_NO=None, THREAD_ID=None, USER_ID="SYSTEM", RUN_KIND="문의 등록",
                  STARTED_AT="2026-10-23 09:00:00")
    values.update(overrides)
    conn.execute(f"INSERT INTO PROCESS_LOG ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
                 list(values.values()))


@pytest.mark.parametrize("bad", [{"USER_ID": "X999"}, {"USER_ID": None}, {"ISSUE_NO": 999}, {"RUN_KIND": "배치"}])
def test_process_log_checks(db, bad):
    with pytest.raises(sqlite3.IntegrityError):
        add_run(db, **bad)


def test_call_and_message_log_checks(db):
    add_run(db, ISSUE_NO=2)
    db.execute("INSERT INTO CALL_LOG (RUN_ID, SEQ, EVENT_KIND, NAME, SUCCESS, CREATED_AT)"
               " VALUES ('run-1', 1, 'llm', 'classify', 'Y', '2026-10-23 09:00:00')")
    db.execute("INSERT INTO MESSAGE_LOG VALUES ('run-1', 2, '관리자', '조사해 줘', '2026-10-23 09:00:00')")
    for sql in [
        "INSERT INTO CALL_LOG (RUN_ID, SEQ, EVENT_KIND, NAME, SUCCESS, CREATED_AT) VALUES ('run-x', 1, 'llm', 'a', 'Y', 't')",
        "INSERT INTO CALL_LOG (RUN_ID, SEQ, EVENT_KIND, NAME, SUCCESS, CREATED_AT) VALUES ('run-1', 3, 'sql', 'a', 'Y', 't')",
        "INSERT INTO CALL_LOG (RUN_ID, SEQ, EVENT_KIND, NAME, SUCCESS, CREATED_AT) VALUES ('run-1', 3, 'llm', 'a', 'O', 't')",
        "INSERT INTO MESSAGE_LOG VALUES ('run-1', 4, '고객', '안녕', 't')",
        "INSERT INTO MESSAGE_LOG VALUES ('run-x', 4, '비서', '안녕', 't')",
    ]:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(sql)


def test_reseed_clears_logs(tmp_path):
    path = tmp_path / "app.db"
    init_db(path)
    conn = connect(path)
    with conn:
        add_resolution(conn)
        add_run(conn)
    conn.close()
    init_db(path)
    conn = connect(path)
    assert count(conn, "RESOLUTION_LOG") == 1 and count(conn, "PROCESS_LOG") == 0
    conn.close()
