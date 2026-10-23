"""멈춤 규칙(T22)과 실패 처리(T23). 모두 가짜 LLM."""

import asyncio
import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.agent import TOOL_BUDGET, run_turn
from app.db import connect
from app.fake_llm import FakeChatModel, FakeLLMError, fake_llm, tool_call

FREE = "free-A001-20261023-101500"


def tool_messages(result):
    return [m for m in result.messages if isinstance(m, ToolMessage)]


def run_row():
    return connect().execute("SELECT * FROM PROCESS_LOG ORDER BY rowid DESC").fetchone()


def finalize_answer(messages, tools):
    """마무리 LLM 호출: 도구 없이 불리고, 입력은 시스템 + 글로 푼 도구 결과 한 덩어리다."""
    assert tools is None
    assert [type(m) for m in messages] == [SystemMessage, HumanMessage]
    return AIMessage("V003 혼합탱크 1까지 확인했습니다.\n확인하지 못한 것: V005 매핑\n다음 질문 제안: V005 매핑만 보여 줘")


# ---- T22: 멈춤 규칙 ----

def test_stop_budget_reached(db_path):
    queries = ["V001", "V002", "V003", "V004", "V005", "반응", "혼합", "세정"]
    script = [tool_call("find_vessel", {"query": q}, f"c{i}") for i, q in enumerate(queries)] + [finalize_answer]
    model = FakeChatModel("f", script)
    progress = []
    result = asyncio.run(run_turn(FREE, "VESSEL 모두 하나씩 찾아", fake_llm(model), progress=progress.append))
    assert result.tool_calls_used == TOOL_BUDGET == 8
    assert len(tool_messages(result)) == 8  # 8번째 도구 뒤에는 LLM에 다시 묻지 않고 바로 finalize
    assert model.script == []  # 도구 8회 + 마무리 1회만 불렀다
    assert result.answer.startswith("조사 한도 도달(도구 호출 상한(8회) 도달).")
    assert "확인하지 못한 것: V005 매핑" in result.answer and "다음 질문 제안" in result.answer
    assert progress[-2:] == ["도구 호출 중: find_vessel(세정) (8/8)", "마무리 답 작성 중…"]
    run = run_row()
    assert (run["RESULT"], run["TOOL_CALLS"], run["STOP_REASON"]) == ("멈춤", 8, "도구 호출 상한(8회) 도달")
    events = connect().execute("SELECT EVENT_KIND, NAME, OUTPUT FROM CALL_LOG WHERE RUN_ID = ? ORDER BY SEQ",
                               (run["RUN_ID"],)).fetchall()
    assert [e[0] for e in events].count("tool") == 8
    assert events[-1][0] == "finalize" and events[-1][2].startswith("조사 한도 도달")
    assert [e[1] for e in events if e[0] == "llm"][-1] == "finalize@v1"


def test_stop_budget_in_one_message(db_path):
    """한 번에 10개를 부르면 8개만 실행하고 나머지는 '[멈춤]' 결과로 짝을 맞춘다."""
    calls = AIMessage("", tool_calls=[{"name": "find_action", "args": {"query": str(100 + i)}, "id": f"a{i}"}
                                      for i in range(10)])
    model = FakeChatModel("f", [calls, finalize_answer])
    result = asyncio.run(run_turn(FREE, "ACTION 다 찾아", fake_llm(model)))
    results = tool_messages(result)
    assert len(results) == 10 and result.tool_calls_used == 8
    assert all(m.content.startswith("[멈춤] 도구 호출 상한(8회) 도달") for m in results[8:])
    assert run_row()["TOOL_CALLS"] == 8


def test_stop_duplicate_call(db_path):
    model = FakeChatModel("f", [tool_call("find_vessel", {"query": "V003"}, "c1"),
                                tool_call("get_vessel_mappings", {"vessel_id": "V003"}, "c2"),
                                tool_call("find_vessel", {"query": "V003"}, "c3"),  # 같은 도구·같은 인자
                                finalize_answer])
    result = asyncio.run(run_turn(FREE, "V003 조사", fake_llm(model)))
    assert result.tool_calls_used == 2
    assert result.answer.startswith("조사 한도 도달(같은 도구·같은 인자 중복 호출: find_vessel(query='V003')).")
    # 실행하지 않은 중복 호출에도 결과 짝이 있다(다음 LLM 호출이 깨지지 않게).
    assert tool_messages(result)[-1].content.startswith("[멈춤] 같은 도구·같은 인자 중복 호출")
    assert run_row()["STOP_REASON"].startswith("같은 도구·같은 인자 중복 호출")


def test_stop_same_args_different_tool_is_not_duplicate(db_path):
    model = FakeChatModel("f", [tool_call("find_vessel", {"query": "1"}, "c1"), tool_call("find_action", {"query": "1"}, "c2"),
                                AIMessage("끝")])
    result = asyncio.run(run_turn(FREE, "1 찾아", fake_llm(model)))
    assert result.answer == "끝" and result.tool_calls_used == 2


def test_stop_two_consecutive_failures(db_path):
    model = FakeChatModel("f", [tool_call("get_vessel_mappings", {"vessel_id": "V9"}, "c1"),
                                tool_call("get_vessel_mappings", {"vessel_id": "V99"}, "c2"),
                                finalize_answer])
    result = asyncio.run(run_turn(FREE, "V9 매핑", fake_llm(model)))
    assert result.tool_calls_used == 2
    assert result.answer.startswith("도구 실패로 조사를 멈췄습니다(같은 도구 연속 2회 실패: get_vessel_mappings).")
    assert run_row()["RESULT"] == "멈춤"


def test_stop_failure_streak_resets(db_path):
    """실패 사이에 성공이나 다른 도구가 끼면 연속 실패가 아니다."""
    model = FakeChatModel("f", [tool_call("get_vessel_mappings", {"vessel_id": "V9"}, "c1"),
                                tool_call("get_vessel_mappings", {"vessel_id": "V003"}, "c2"),
                                tool_call("get_vessel_mappings", {"vessel_id": "V99"}, "c3"),
                                tool_call("get_issue", {"issue_no": 999}, "c4"),
                                tool_call("get_vessel_mappings", {"vessel_id": "V999"}, "c5"),
                                AIMessage("V003만 확인됩니다.")])
    result = asyncio.run(run_turn(FREE, "매핑 찾아", fake_llm(model)))
    assert result.answer == "V003만 확인됩니다." and result.tool_calls_used == 5


def test_stop_finalize_llm_failure_uses_python_sentence(db_path):
    model = FakeChatModel("f", [tool_call("find_vessel", {"query": "V003"}, "c1"), tool_call("find_vessel", {"query": "V003"}, "c2"),
                                FakeLLMError("마무리 실패")])
    result = asyncio.run(run_turn(FREE, "V003", fake_llm(model)))
    assert result.answer.startswith("조사 한도 도달(같은 도구·같은 인자 중복 호출")
    assert "조사 한도에 도달했습니다. 지금까지 부른 도구: find_vessel(query='V003')" in result.answer
    assert "다음 질문 제안" in result.answer
    llm_events = connect().execute("SELECT NAME, SUCCESS FROM CALL_LOG WHERE EVENT_KIND = 'llm' ORDER BY SEQ").fetchall()
    assert [tuple(e) for e in llm_events][-1] == ("finalize@v1", "N")


def test_stop_budget_resets_each_input(db_path):
    script = [tool_call("find_vessel", {"query": f"V00{i}"}, f"a{i}") for i in range(1, 6)] + [AIMessage("첫 답")]
    script += [tool_call("find_vessel", {"query": f"V00{i}"}, f"b{i}") for i in range(1, 6)] + [AIMessage("둘째 답")]
    llm = fake_llm(FakeChatModel("f", script))
    assert asyncio.run(run_turn(FREE, "하나", llm)).tool_calls_used == 5
    second = asyncio.run(run_turn(FREE, "둘", llm))  # 같은 인자라도 새 입력이면 중복이 아니고 예산도 새로
    assert second.answer == "둘째 답" and second.tool_calls_used == 5


def test_stop_finalize_input_has_no_tool_blocks(db_path):
    """마무리 호출은 도구 없이 부르므로 도구 호출·결과를 글로 풀어 보낸다(Bedrock은 도구 설정 없이 도구 블록을 못 받음)."""
    seen = {}

    def capture(messages, tools):
        seen["messages"] = messages
        return AIMessage("요약")

    model = FakeChatModel("f", [tool_call("find_vessel", {"query": "V003"}, "c1"), tool_call("find_vessel", {"query": "V003"}, "c2"),
                                capture])
    asyncio.run(run_turn(FREE, "V003 찾아", fake_llm(model)))
    system, human = seen["messages"]
    assert "멈춘 이유: 같은 도구·같은 인자 중복 호출" in system.content and "확인하지 못한 것" in system.content
    assert "[이번 질문] V003 찾아" in human.content and "[도구 호출] find_vessel(query='V003')" in human.content
    assert "V003 혼합탱크 1" in human.content
    assert not getattr(human, "tool_calls", None)
    payload = connect().execute("SELECT INPUT FROM CALL_LOG WHERE NAME = 'finalize@v1'").fetchone()[0]
    assert "tools" not in json.loads(payload)


# ---- T23: 실패 처리(모든 모델 실패 → [다시 시도], MCP 끊김 → 1회 재연결) ----

import anyio  # noqa: E402
import pytest  # noqa: E402

from app import agent as agent_module  # noqa: E402
from app.agent import (ABANDONED_REPLY, McpUnavailable, NothingToRetry, load_messages, needs_retry,  # noqa: E402
                       retry_turn)
from app.llm import AllModelsFailed  # noqa: E402


def down():
    """모든 모델이 실패하는 LLM."""
    return fake_llm(FakeChatModel("f1", error=FakeLLMError("x")), FakeChatModel("f2", error=FakeLLMError("y")))


def test_failure_all_models_then_retry_keeps_tool_results(db_path):
    first = FakeChatModel("f1", [tool_call("find_vessel", {"query": "혼합"}, "c1"), FakeLLMError("장애")])
    with pytest.raises(AllModelsFailed):
        asyncio.run(run_turn(FREE, "혼합탱크 ID?", fake_llm(first)))
    assert asyncio.run(needs_retry(FREE))
    assert run_row()["RESULT"] == "모든 모델 실패"

    def answer(messages, tools):
        # 다시 시도: 관리자 재입력 없이, 이미 부른 도구 결과를 보고 실패한 LLM 호출만 다시 한다.
        assert isinstance(messages[-1], ToolMessage) and "V003 혼합탱크 1" in messages[-1].content
        assert [m.content for m in messages if isinstance(m, HumanMessage)] == ["혼합탱크 ID?"]
        return AIMessage("V003 혼합탱크 1입니다.")

    second = FakeChatModel("f1", [answer])
    result = asyncio.run(retry_turn(FREE, fake_llm(second)))
    assert result.answer == "V003 혼합탱크 1입니다." and result.tool_calls_used == 1
    assert len(second.calls) == 1  # 실패한 LLM 호출만
    assert not asyncio.run(needs_retry(FREE))
    run = run_row()
    assert (run["RUN_KIND"], run["RESULT"], run["THREAD_ID"], run["USER_ID"]) == ("다시 시도", "답변 완료", FREE, "A001")
    assert [m.content for m in asyncio.run(load_messages(FREE)) if isinstance(m, HumanMessage)] == ["혼합탱크 ID?"]


def test_failure_retry_continues_budget(db_path):
    script = [tool_call("find_action", {"query": str(101 + i)}, f"c{i}") for i in range(6)] + [FakeLLMError("장애")]
    with pytest.raises(AllModelsFailed):
        asyncio.run(run_turn(FREE, "ACTION 하나씩", fake_llm(FakeChatModel("f", script))))
    calls = AIMessage("", tool_calls=[{"name": "find_vessel", "args": {"query": f"V00{i}"}, "id": f"v{i}"}
                                      for i in range(1, 4)])
    retry = FakeChatModel("f", [calls, lambda m, t: AIMessage("요약")])
    result = asyncio.run(retry_turn(FREE, fake_llm(retry)))
    assert result.tool_calls_used == 8  # 6 + 2, 세 번째는 상한
    assert result.answer.startswith("조사 한도 도달(도구 호출 상한(8회) 도달).")


def test_failure_first_call_fails_then_retry(db_path):
    with pytest.raises(AllModelsFailed):
        asyncio.run(run_turn(FREE, "VESSEL 몇 개?", down()))
    result = asyncio.run(retry_turn(FREE, fake_llm(FakeChatModel("f", [AIMessage("5개입니다.")]))))
    assert result.answer == "5개입니다."


def test_failure_new_input_instead_of_retry(db_path):
    with pytest.raises(AllModelsFailed):
        asyncio.run(run_turn(FREE, "첫 질문", fake_llm(FakeChatModel("f", [tool_call("find_vessel", {"query": "V001"}, "c1"),
                                                                           FakeLLMError("장애")]))))
    model = FakeChatModel("f", [AIMessage("둘째 답")])
    result = asyncio.run(run_turn(FREE, "다른 질문", fake_llm(model)))
    assert result.answer == "둘째 답" and not asyncio.run(needs_retry(FREE))
    sent = model.calls[0][1]
    kinds = [type(m).__name__ for m in sent]
    assert kinds == ["SystemMessage", "HumanMessage", "AIMessage", "ToolMessage", "AIMessage", "HumanMessage"]
    assert sent[4].content == ABANDONED_REPLY


def test_failure_nothing_to_retry(db_path):
    with pytest.raises(NothingToRetry):
        asyncio.run(retry_turn(FREE, fake_llm(FakeChatModel("f", []))))
    asyncio.run(run_turn(FREE, "질문", fake_llm(FakeChatModel("f", [AIMessage("답")]))))
    with pytest.raises(NothingToRetry):
        asyncio.run(retry_turn(FREE, fake_llm(FakeChatModel("f", []))))


def break_session_tools(monkeypatch, reconnect_ok=True):
    """세션으로 읽은 도구의 첫 호출이 '연결 끊김'으로 실패하게 만든다. reconnect_ok=False면 재연결도 실패."""
    real = agent_module.load_mcp_tools
    state = {"reconnects": 0, "real": real}

    async def flaky(session, **kwargs):
        if session is None:  # 재연결
            state["reconnects"] += 1
            if not reconnect_ok:
                raise anyio.BrokenResourceError("서버 프로세스 없음")
            return await real(None, **kwargs)
        tools = await real(session, **kwargs)
        for t in tools:
            async def broken(*args, **kw):
                raise anyio.ClosedResourceError()
            object.__setattr__(t, "ainvoke", broken)
        return tools

    monkeypatch.setattr(agent_module, "load_mcp_tools", flaky)
    return state


def test_failure_mcp_disconnect_reconnects_once(db_path, monkeypatch):
    state = break_session_tools(monkeypatch)
    model = FakeChatModel("f", [tool_call("find_vessel", {"query": "혼합"}, "c1"),
                                tool_call("find_action", {"query": "냉각"}, "c2"), AIMessage("V003, 104입니다.")])
    result = asyncio.run(run_turn(FREE, "혼합탱크와 냉각", fake_llm(model)))
    assert result.answer == "V003, 104입니다."
    results = tool_messages(result)
    assert "V003 혼합탱크 1" in results[0].content and "104 냉각" in results[1].content
    assert state["reconnects"] == 1  # 재연결은 한 번뿐, 그 뒤 호출은 새 연결로
    events = connect().execute("SELECT NAME, OUTPUT FROM CALL_LOG WHERE NAME = 'MCP 재연결'").fetchall()
    assert [tuple(e) for e in events] == [("MCP 재연결", "재연결 성공")]


def test_failure_mcp_reconnect_fails_then_retry(db_path, monkeypatch):
    state = break_session_tools(monkeypatch, reconnect_ok=False)
    model = FakeChatModel("f", [tool_call("find_vessel", {"query": "혼합"}, "c1")])
    with pytest.raises(McpUnavailable, match="다시 연결하지 못했습니다"):
        asyncio.run(run_turn(FREE, "혼합탱크 ID?", fake_llm(model)))
    assert state["reconnects"] == 1
    run = run_row()
    assert run["RESULT"] == "MCP 연결 실패"
    event = connect().execute("SELECT OUTPUT, SUCCESS FROM CALL_LOG WHERE NAME = 'MCP 재연결'").fetchone()
    assert tuple(event) == ("재연결 실패", "N")
    assert asyncio.run(needs_retry(FREE))

    monkeypatch.setattr(agent_module, "load_mcp_tools", state["real"])  # 서버가 돌아왔다
    result = asyncio.run(retry_turn(FREE, fake_llm(FakeChatModel("f", [AIMessage("V003입니다.")]))))
    assert result.answer == "V003입니다." and result.tool_calls_used == 1
    assert "V003 혼합탱크 1" in tool_messages(result)[0].content
