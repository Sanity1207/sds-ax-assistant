import asyncio
import json

import pytest
from langchain_core.messages import AIMessage

from app.agent import free_thread_id, issue_thread_id, parse_thread_id, run_turn
from app.classify import FAILED_RESULT, classify_issue, register_issue
from app.db import connect
from app.fake_llm import FakeChatModel, FakeLLMError, fake_llm, tool_call
from app.llm import AllModelsFailed
from app.recorder import start_run
from app.seed import ISSUES

NEW_NO = len(ISSUES) + 1
CREATE = {"type": "매핑 생성", "master_target": None, "multiple_requests": False,
          "id_mismatch": False, "reason": "V003에 104를 추가해 달라는 요청"}


def runs(conn):
    return conn.execute("SELECT * FROM PROCESS_LOG ORDER BY rowid").fetchall()


def events(conn, run_id):
    return conn.execute("SELECT * FROM CALL_LOG WHERE RUN_ID = ? ORDER BY SEQ", (run_id,)).fetchall()


# ---- T10: 처리 기록 장치 ----

def test_record_register_creates_one_run(db_path):
    register_issue("C001", "V003", "104", "V003에 104 추가해 주세요", fake_llm(FakeChatModel("fake-1", [CREATE])))
    conn = connect()
    [run] = runs(conn)
    assert run["USER_ID"] == "SYSTEM" and run["THREAD_ID"] is None
    assert (run["ISSUE_NO"], run["RUN_KIND"], run["RESULT"], run["MODEL_ID"]) == (NEW_NO, "문의 등록", "분류 완료", "fake-1")
    assert run["STARTED_AT"] <= run["ENDED_AT"]

    rows = events(conn, run["RUN_ID"])
    assert [(r["EVENT_KIND"], r["NAME"]) for r in rows] == [
        ("branch", "등록 전 검사"), ("llm", "classify@v1"), ("branch", "규칙표")]
    assert [r["SEQ"] for r in rows] == [1, 2, 3]
    llm = rows[1]
    sent = json.loads(llm["INPUT"])
    assert sent["prompt"] == "classify" and sent["version"] == "1"
    assert [m["role"] for m in sent["messages"]] == ["system", "human"]
    assert "V003에 104 추가해 주세요" in sent["messages"][1]["content"]
    out = json.loads(llm["OUTPUT"])
    assert out["parsed"]["type"] == "매핑 생성" and out["raw"] == CREATE
    assert [a["model_id"] for a in out["attempts"]] == ["fake-1"]
    assert llm["MODEL_ID"] == "fake-1" and llm["SUCCESS"] == "Y" and llm["ELAPSED_MS"] >= 0
    assert json.loads(rows[2]["OUTPUT"]) == {"status": "처리대기", "owner_role": "매핑 담당자"}
    conn.close()


def test_record_fallback_attempts(db_path):
    llm = fake_llm(FakeChatModel("fake-1", error=FakeLLMError("요청 제한")), FakeChatModel("fake-2", [CREATE]))
    register_issue("C001", "V003", "104", "V003에 104 추가", llm)
    conn = connect()
    [run] = runs(conn)
    llm_event = [e for e in events(conn, run["RUN_ID"]) if e["EVENT_KIND"] == "llm"][0]
    attempts = json.loads(llm_event["OUTPUT"])["attempts"]
    assert [(a["model_id"], a["ok"]) for a in attempts] == [("fake-1", False), ("fake-2", True)]
    assert "요청 제한" in attempts[0]["error"]
    assert llm_event["MODEL_ID"] == run["MODEL_ID"] == "fake-2"


def test_record_all_models_fail(db_path):
    llm = fake_llm(FakeChatModel("f1", error=FakeLLMError("x")), FakeChatModel("f2", error=FakeLLMError("y")))
    register_issue("C001", "V003", "104", "V003에 104 추가", llm)
    conn = connect()
    [run] = runs(conn)
    assert run["RESULT"] == FAILED_RESULT and run["ENDED_AT"] is not None
    kinds = [(e["EVENT_KIND"], e["SUCCESS"]) for e in events(conn, run["RUN_ID"])]
    assert kinds == [("branch", "Y"), ("llm", "N"), ("branch", "Y")]
    assert "AllModelsFailed" in events(conn, run["RUN_ID"])[1]["ERROR"]


def test_record_survives_business_rollback(db_path):
    """분류 결과를 쓰는 업무 트랜잭션이 롤백돼도 처리 기록은 남는다."""
    conn = connect()
    conn.execute("CREATE TRIGGER BOOM BEFORE UPDATE ON ISSUE BEGIN SELECT RAISE(ABORT, 'boom'); END")
    with pytest.raises(Exception, match="boom"):
        register_issue("C001", "V003", "104", "V003에 104 추가", fake_llm(FakeChatModel("f", [CREATE])), conn)
    # 업무: 분류 결과 UPDATE는 롤백되어 먼저 저장한 '자동 분류 실패' 확인필요 그대로다.
    issue = conn.execute("SELECT STATUS, RESULT FROM ISSUE WHERE ISSUE_NO = ?", (NEW_NO,)).fetchone()
    assert tuple(issue) == ("확인필요", FAILED_RESULT)
    # 기록: 실행·LLM 호출·규칙표 분기가 남고, 결과에 오류가 적힌다.
    [run] = runs(conn)
    assert run["RESULT"].startswith("오류: IntegrityError") and "boom" in run["RESULT"]
    assert run["ENDED_AT"] is not None
    assert [e["EVENT_KIND"] for e in events(conn, run["RUN_ID"])] == ["branch", "llm", "branch"]
    conn.close()


def test_record_is_separate_from_business_transaction(db_path):
    """기록은 별도 연결로 바로 커밋되므로, 같은 실행 중 업무 트랜잭션을 롤백해도 지워지지 않는다."""
    conn = connect()
    with start_run("대화 입력", "A001", 2, issue_thread_id(2, "A001")) as run:
        run.event("tool", "find_vessel", {"args": {"query": "V003"}}, "V003 혼합탱크 1")
        conn.execute("UPDATE ISSUE SET STATUS = '완료' WHERE ISSUE_NO = 2")
        conn.rollback()
        run.message("비서", "조사했습니다.")
    assert conn.execute("SELECT STATUS FROM ISSUE WHERE ISSUE_NO = 2").fetchone()[0] == "처리대기"
    assert len(events(conn, run.run_id)) == 1
    assert conn.execute("SELECT SEQ, SPEAKER FROM MESSAGE_LOG").fetchall()[0][:] == (2, "비서")
    conn.close()


def test_record_reclassify_is_new_run(db_path):
    classify_issue(6, fake_llm(FakeChatModel("f", [dict(CREATE, reason="V005에 106 확인 요청")])), user_id="A001")
    [run] = runs(connect())
    assert (run["RUN_KIND"], run["ISSUE_NO"], run["USER_ID"], run["THREAD_ID"]) == ("다시 분류", 6, "A001", None)


def test_record_chat_turn(db_path):
    model = FakeChatModel("fake-1", [tool_call("find_vessel", {"query": "혼합"}), AIMessage("V003입니다.")])
    thread = issue_thread_id(2, "A001")
    asyncio.run(run_turn(thread, "혼합탱크 ID 알려줘", fake_llm(model)))
    conn = connect()
    [run] = runs(conn)
    assert (run["RUN_KIND"], run["ISSUE_NO"], run["THREAD_ID"], run["USER_ID"]) == ("대화 입력", 2, thread, "A001")
    assert (run["RESULT"], run["TOOL_CALLS"], run["MODEL_ID"]) == ("답변 완료", 1, "fake-1")

    rows = events(conn, run["RUN_ID"])
    assert [(r["EVENT_KIND"], r["NAME"], r["OUTPUT"] if r["EVENT_KIND"] == "branch" else None) for r in rows] == [
        ("llm", "assistant@v1", None), ("branch", "should_stop", "tools"), ("tool", "find_vessel", None),
        ("llm", "assistant@v1", None), ("branch", "should_stop", "finalize"), ("finalize", "finalize", None)]
    tool = rows[2]
    assert json.loads(tool["INPUT"]) == {"args": {"query": "혼합"}, "risk": "read", "auto_run": True}
    assert "V003 혼합탱크 1" in tool["OUTPUT"] and tool["SUCCESS"] == "Y"
    assert json.loads(rows[0]["INPUT"])["tools"] == ["find_vessel"]
    assert rows[-1]["OUTPUT"] == "답변 완료"

    messages = conn.execute("SELECT SEQ, SPEAKER, CONTENT FROM MESSAGE_LOG ORDER BY SEQ").fetchall()
    assert [tuple(m) for m in messages] == [(1, "관리자", "혼합탱크 ID 알려줘"), (8, "비서", "V003입니다.")]
    assert [r["SEQ"] for r in rows] == [2, 3, 4, 5, 6, 7]  # 이벤트와 말이 순번 하나를 나눠 쓴다.
    conn.close()


def test_record_chat_tool_error_is_failure(db_path):
    model = FakeChatModel("f", [tool_call("drop_table", {}), AIMessage("없는 도구입니다.")])
    asyncio.run(run_turn(issue_thread_id(2, "A001"), "테이블 지워", fake_llm(model)))
    conn = connect()
    tool = conn.execute("SELECT * FROM CALL_LOG WHERE EVENT_KIND = 'tool'").fetchone()
    assert tool["SUCCESS"] == "N" and tool["OUTPUT"].startswith("[오류]")
    assert json.loads(tool["INPUT"])["risk"] == "미등록(승인 필요)"


def test_record_chat_all_models_fail(db_path):
    llm = fake_llm(FakeChatModel("f1", error=FakeLLMError("x")), FakeChatModel("f2", error=FakeLLMError("y")))
    thread = free_thread_id("A002")
    with pytest.raises(AllModelsFailed):
        asyncio.run(run_turn(thread, "V003 매핑 보여줘", llm))
    conn = connect()
    [run] = runs(conn)
    assert (run["ISSUE_NO"], run["USER_ID"], run["RESULT"]) == (None, "A002", "모든 모델 실패")
    assert run["ENDED_AT"] is not None
    [llm_event] = events(conn, run["RUN_ID"])
    assert llm_event["EVENT_KIND"] == "llm" and llm_event["SUCCESS"] == "N"
    assert conn.execute("SELECT SPEAKER FROM MESSAGE_LOG").fetchall()[0][0] == "관리자"


def test_record_thread_id_parsing():
    assert parse_thread_id("issue-12-A001") == (12, "A001")
    assert parse_thread_id(free_thread_id("A002")) == (None, "A002")
    assert parse_thread_id("free-A001-20261023-101500") == (None, "A001")
    assert parse_thread_id("t-1") == (None, None)


def test_record_run_needs_user():
    with pytest.raises(ValueError, match="사용자"):
        asyncio.run(run_turn("t-1", "안녕"))
