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
    assert [t.name for t in model.calls[0][2]] == ["find_vessel"]
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
