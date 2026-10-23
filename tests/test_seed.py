import sqlite3

import pytest

from app.db import connect
from app.seed import init_db


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
    assert insert_issue(db) == 1
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
    assert count(conn, "ISSUE") == 0
    assert insert_issue(conn) == 1
    conn.close()


def test_foreign_keys_on_every_connection(db):
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
