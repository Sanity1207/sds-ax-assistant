import asyncio

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent import load_messages, run_turn
from app.fake_llm import FakeChatModel, fake_llm, tool_call


def answer_from_tool(messages, tools):
    """가짜 LLM의 두 번째 응답: 마지막 도구 결과를 그대로 인용해 답한다."""
    result = [m for m in messages if isinstance(m, ToolMessage)][-1]
    return AIMessage(f"검색 결과입니다. {result.content}")


def test_skeleton_find_vessel_then_answer(db_path):
    model = FakeChatModel("fake-1", [
        tool_call("find_vessel", {"query": "혼합"}),
        answer_from_tool,
        AIMessage("앞에서 찾은 VESSEL은 V003입니다."),
    ])
    llm = fake_llm(model)

    first = asyncio.run(run_turn("t-1", "혼합탱크 ID가 뭐야?", llm, user_id="A001"))
    assert "V003" in first.answer and "혼합탱크 1" in first.answer
    assert first.tool_calls_used == 1
    assert first.model_id == "fake-1"
    # 첫 LLM 호출에 도구가 바인딩되고, 두 번째 호출은 도구 결과를 본다.
    assert "find_vessel" in [t.name for t in model.calls[0][2]]
    assert isinstance(model.calls[1][1][-1], ToolMessage)

    second = asyncio.run(run_turn("t-1", "방금 그 ID 다시 알려줘", llm, user_id="A001"))
    assert second.answer == "앞에서 찾은 VESSEL은 V003입니다."
    # 같은 thread의 두 번째 입력: 이전 메시지(질문·도구 호출·도구 결과·답변)가 LLM 입력에 유지된다.
    seen = model.calls[2][1]
    assert [type(m).__name__ for m in seen] == [
        "SystemMessage", "HumanMessage", "AIMessage", "ToolMessage", "AIMessage", "HumanMessage"]
    assert len(asyncio.run(load_messages("t-1"))) == 6


def test_skeleton_threads_are_separate(db_path):
    llm = fake_llm(FakeChatModel("fake-1", [AIMessage("A"), AIMessage("B")]))
    asyncio.run(run_turn("t-a", "첫 대화", llm, user_id="A001"))
    asyncio.run(run_turn("t-b", "다른 대화", llm, user_id="A001"))
    assert [m.content for m in asyncio.run(load_messages("t-b"))] == ["다른 대화", "B"]


def test_skeleton_unknown_tool_returns_error_text(db_path):
    model = FakeChatModel("fake-1", [tool_call("drop_table", {}), AIMessage("그 도구는 없습니다.")])
    result = asyncio.run(run_turn("t-x", "테이블 지워", fake_llm(model), user_id="A001"))
    tool_msg = [m for m in result.messages if isinstance(m, ToolMessage)][0]
    assert tool_msg.content.startswith("[오류]") and tool_msg.status == "error"


def test_skeleton_tool_argument_error_is_error_text(db_path):
    model = FakeChatModel("fake-1", [tool_call("find_vessel", {"query": 123}), AIMessage("다시 시도할게요.")])
    result = asyncio.run(run_turn("t-y", "123번 찾아", fake_llm(model), user_id="A001"))
    tool_msg = [m for m in result.messages if isinstance(m, ToolMessage)][0]
    assert tool_msg.content.startswith("[오류]") and tool_msg.status == "error"


# ---- T18: 대화 모드·thread·첫 질문 ----

import pytest  # noqa: E402

from app.agent import (FIRST_NO, FIRST_QUESTION, FIRST_RETRY, FIRST_YES_REQUEST, MODE_TOOLS, issue_thread_id,  # noqa: E402
                       open_thread)
from app.db import connect  # noqa: E402
from app.tools import TOOL_SPECS  # noqa: E402

READ6 = {"find_vessel", "find_action", "get_vessel_mappings", "list_issues", "get_issue", "get_issue_trace"}


def bound_tools(model, call=0):
    return {t.name for t in model.calls[call][2]}


@pytest.mark.parametrize("thread, expected", [
    ("issue-2-A001", READ6 | {"log_resolution", "propose_resolution"}),  # 열린 이슈(처리대기)
    ("issue-7-A001", READ6 | {"log_resolution", "propose_resolution"}),  # 열린 이슈(이관)
    ("issue-1-A001", READ6 | {"log_resolution"}),  # 닫힌 이슈(완료)
    ("issue-8-A001", READ6 | {"log_resolution"}),  # 닫힌 이슈(반려)
    ("free-A001-20261023-101500", READ6),  # 이슈 없음
])
def test_mode_tool_binding(db_path, thread, expected):
    model = FakeChatModel("f", [AIMessage("확인했습니다.")])
    result = asyncio.run(run_turn(thread, "현황 알려 줘", fake_llm(model)))
    # 아직 MCP 서버에 없는 도구(뒤 작업에서 추가)는 바인딩되지 않는다.
    assert bound_tools(model) == expected & set(TOOL_SPECS)
    assert set(result.tools) == expected & set(TOOL_SPECS)
    assert set(MODE_TOOLS[result.mode]) == expected


def test_mode_follows_issue_status_each_turn(db_path):
    model = FakeChatModel("f", [AIMessage("1"), AIMessage("2")])
    llm = fake_llm(model)
    thread = issue_thread_id(2, "A001")
    assert asyncio.run(run_turn(thread, "첫 질문", llm)).mode == "open"
    conn = connect()
    with conn:
        conn.execute("UPDATE ISSUE SET STATUS = '완료', PROCESSED_AT = '2026-10-23 10:00:00', PROCESSOR_ID = 'A001'"
                     " WHERE ISSUE_NO = 2")
    assert asyncio.run(run_turn(thread, "다시", llm)).mode == "closed"
    assert "propose_resolution" not in bound_tools(model, 1)


def test_mode_tool_outside_mode_is_error(db_path):
    model = FakeChatModel("f", [tool_call("log_resolution", {"issue_no": 2, "note": "메모"}), AIMessage("못 남겼습니다.")])
    result = asyncio.run(run_turn("free-A001-20261023-101500", "메모 남겨", fake_llm(model)))
    tool_msg = [m for m in result.messages if isinstance(m, ToolMessage)][0]
    assert tool_msg.content.startswith("[오류] 'log_resolution' 도구는 지금 대화(이슈 없는 대화)에서 쓸 수 없습니다.")
    assert connect().execute("SELECT COUNT(*) FROM RESOLUTION_LOG WHERE KIND = 'note'").fetchone()[0] == 0


def test_mode_log_resolution_records_acting_user(db_path):
    model = FakeChatModel("f", [tool_call("log_resolution", {"issue_no": 7, "note": "ACTION 담당자 답변 대기"}),
                                AIMessage("기록했습니다.")])
    result = asyncio.run(run_turn(issue_thread_id(7, "A002"), "근거 남겨 줘", fake_llm(model)))
    tool_msg = [m for m in result.messages if isinstance(m, ToolMessage)][0]
    assert tool_msg.content == "처리 이력에 기록함: #7 · note · A002 · ACTION 담당자 답변 대기"
    conn = connect()
    row = conn.execute("SELECT ISSUE_NO, KIND, CONTENT, USER_ID FROM RESOLUTION_LOG WHERE KIND = 'note'").fetchone()
    assert tuple(row) == (7, "note", "ACTION 담당자 답변 대기", "A002")
    assert conn.execute("SELECT STATUS FROM ISSUE WHERE ISSUE_NO = 7").fetchone()[0] == "이관"  # 상태 불변
    tool_event = conn.execute("SELECT INPUT FROM CALL_LOG WHERE EVENT_KIND = 'tool'").fetchone()[0]
    assert '"risk": "log"' in tool_event and '"auto_run": true' in tool_event


def test_mode_log_resolution_other_issue_is_error(db_path):
    model = FakeChatModel("f", [tool_call("log_resolution", {"issue_no": 3, "note": "메모"}), AIMessage("안 됩니다.")])
    result = asyncio.run(run_turn(issue_thread_id(7, "A001"), "3번에 메모", fake_llm(model)))
    tool_msg = [m for m in result.messages if isinstance(m, ToolMessage)][0]
    assert tool_msg.content.startswith("[오류] 지금 화면의 이슈는 #7입니다.")
    assert connect().execute("SELECT COUNT(*) FROM RESOLUTION_LOG WHERE KIND = 'note'").fetchone()[0] == 0


def test_mode_first_question_on_open_issue(db_path):
    thread = issue_thread_id(2, "A001")
    asyncio.run(open_thread(thread))
    asyncio.run(open_thread(thread))  # 다시 열어도 질문은 한 번
    messages = asyncio.run(load_messages(thread))
    assert [m.content for m in messages] == [FIRST_QUESTION.format(n=2)]
    for closed in (issue_thread_id(1, "A001"), "free-A001-20261023-101500"):
        asyncio.run(open_thread(closed))
        assert asyncio.run(load_messages(closed)) == []


@pytest.mark.parametrize("text", ["네", "yes", "ㅇㅇ", "Y please", "응", "", "yy"])
def test_mode_first_question_other_input_asks_again(db_path, text):
    thread = issue_thread_id(2, "A001")
    asyncio.run(open_thread(thread))
    model = FakeChatModel("f", [])
    result = asyncio.run(run_turn(thread, text, fake_llm(model)))
    assert result.answer == FIRST_RETRY.format(n=2) and not result.llm_called
    assert model.calls == []  # LLM 호출 0
    conn = connect()
    assert conn.execute("SELECT OUTPUT FROM CALL_LOG WHERE NAME = '첫 질문'").fetchone()[0] == "다시 묻기"
    assert conn.execute("SELECT RESULT FROM PROCESS_LOG").fetchone()[0] == "첫 질문 다시 묻기"
    # 다시 물은 뒤에도 답 대기 상태가 이어진다.
    again = asyncio.run(run_turn(thread, "n", fake_llm(model)))
    assert again.answer == FIRST_NO and model.calls == []


@pytest.mark.parametrize("text", ["y", " Y ", "y\n"])
def test_mode_first_question_yes_investigates(db_path, text):
    thread = issue_thread_id(2, "A001")
    asyncio.run(open_thread(thread))
    model = FakeChatModel("f", [tool_call("get_issue", {"issue_no": 2}), AIMessage("#2는 V003→104 생성 요청입니다.")])
    result = asyncio.run(run_turn(thread, text, fake_llm(model)))
    assert result.answer == "#2는 V003→104 생성 요청입니다."
    sent = model.calls[0][1]
    # 첫 질문 교환은 LLM 입력에서 빠지고, 조사 요청이 첫 관리자 말이 된다.
    assert [type(m).__name__ for m in sent] == ["SystemMessage", "HumanMessage"]
    assert sent[1].content == FIRST_YES_REQUEST.format(n=2)
    assert "#2" in sent[0].content and "열린 건" in sent[0].content
    # 화면용 기록에는 질문·답(y)·조사 요청·도구·답이 모두 남는다.
    shown = asyncio.run(load_messages(thread))
    assert shown[0].content == FIRST_QUESTION.format(n=2) and shown[1].content == text


def test_mode_first_question_no_then_free_chat(db_path):
    thread = issue_thread_id(2, "A001")
    asyncio.run(open_thread(thread))
    model = FakeChatModel("f", [AIMessage("V003입니다.")])
    first = asyncio.run(run_turn(thread, "N", fake_llm(model)))
    assert first.answer == FIRST_NO and model.calls == []
    second = asyncio.run(run_turn(thread, "혼합탱크 ID?", fake_llm(model)))
    assert second.answer == "V003입니다."
    assert [type(m).__name__ for m in model.calls[0][1]] == ["SystemMessage", "HumanMessage"]
    assert model.calls[0][1][1].content == "혼합탱크 ID?"
    # 이후 y를 입력해도 첫 질문은 끝났으므로 일반 입력이다.
    third = asyncio.run(run_turn(thread, "y", fake_llm(FakeChatModel("f", [AIMessage("무엇을요?")]))))
    assert third.llm_called and third.answer == "무엇을요?"


def test_mode_thread_per_issue_and_admin(db_path):
    assert issue_thread_id(2, "A001") != issue_thread_id(2, "A002")
    for user in ("A001", "A002"):
        asyncio.run(open_thread(issue_thread_id(2, user)))
    asyncio.run(run_turn(issue_thread_id(2, "A001"), "n", fake_llm(FakeChatModel("f", []))))
    assert len(asyncio.run(load_messages(issue_thread_id(2, "A002")))) == 1  # A001의 답은 섞이지 않는다
    assert len(asyncio.run(load_messages(issue_thread_id(2, "A001")))) == 3
