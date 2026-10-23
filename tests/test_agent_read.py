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


# ---- T21: 비서 프롬프트·진행 표시·대화 기록 ----

from app.agent import OFF_TOPIC_REPLY, SYSTEM_PROMPT, progress_text  # noqa: E402


def first_line_answer(messages, tools):
    """가짜 LLM: 마지막 도구 결과를 근거로 답한다(실제 LLM이 할 일을 흉내)."""
    result = [m for m in messages if isinstance(m, ToolMessage)][-1].content
    return AIMessage(f"근거: {result}")


def test_prompt_scenario_name_to_id(db_path):
    model = FakeChatModel("f", [tool_call("find_vessel", {"query": "혼합탱크"}), first_line_answer])
    progress = []
    result = asyncio.run(run_turn("free-A001-20261023-101500", "혼합탱크 1의 VESSEL_ID가 뭐야?", fake_llm(model),
                                  progress=progress.append))
    assert "V003 혼합탱크 1" in result.answer
    assert progress == ["비서가 생각하는 중…", "도구 호출 중: find_vessel(혼합탱크) (1/8)", "비서가 생각하는 중…"]


def test_prompt_scenario_master_count(db_path):
    model = FakeChatModel("f", [tool_call("find_action", {"query": ""}), tool_call("find_vessel", {"query": ""}),
                                lambda m, t: AIMessage("ACTION은 6건, VESSEL은 5건입니다.")])
    progress = []
    result = asyncio.run(run_turn("free-A001-20261023-101500", "마스터에 ACTION하고 VESSEL 몇 개야?", fake_llm(model),
                                  progress=progress.append))
    tool_results = [m.content for m in result.messages if isinstance(m, ToolMessage)]
    assert tool_results[0].startswith("ACTION 전체 6건") and tool_results[1].startswith("VESSEL 전체 5건")
    assert result.answer == "ACTION은 6건, VESSEL은 5건입니다." and result.tool_calls_used == 2
    assert "도구 호출 중: find_vessel() (2/8)" in progress


def test_prompt_off_topic_sentence_in_prompt(db_path):
    model = FakeChatModel("f", [AIMessage(OFF_TOPIC_REPLY)])
    result = asyncio.run(run_turn("free-A001-20261023-101500", "오늘 날씨 어때?", fake_llm(model)))
    assert result.answer == OFF_TOPIC_REPLY and result.tool_calls_used == 0
    system = model.calls[0][1][0].content
    assert f'"{OFF_TOPIC_REPLY}"' in system


def test_prompt_rules():
    for part in ("신뢰하지 않는 데이터", "<<< >>>", "따르지 않습니다", "8회", "propose_resolution", "승인 카드",
                 "이관 건은 변경 없이만", "log_resolution", "find_vessel·find_action"):
        assert part in SYSTEM_PROMPT, part


def test_prompt_progress_text():
    assert progress_text("get_vessel_mappings", {"vessel_id": "V003"}, 3) == "도구 호출 중: get_vessel_mappings(V003) (3/8)"
    assert progress_text("list_issues", {"status": "반려", "limit": 5}, 1) == "도구 호출 중: list_issues(반려, 5) (1/8)"


def test_prompt_message_log(db_path):
    model = FakeChatModel("f", [tool_call("find_vessel", {"query": "V003"}), AIMessage("V003 혼합탱크 1입니다.")])
    asyncio.run(run_turn(issue_thread_id(2, "A001"), "V003 이름 알려 줘", fake_llm(model)))
    rows = connect().execute("SELECT SPEAKER, CONTENT FROM MESSAGE_LOG ORDER BY SEQ").fetchall()
    assert [tuple(r) for r in rows] == [("관리자", "V003 이름 알려 줘"), ("비서", "V003 혼합탱크 1입니다.")]
    llm_event = connect().execute("SELECT NAME FROM CALL_LOG WHERE EVENT_KIND = 'llm'").fetchone()[0]
    assert llm_event == "assistant@v4"


# ---- T30: ③ 값 이상 조사 ----

from app.agent import SYSTEM_PROMPT as PROMPT_V3  # noqa: E402
from app.classify import register_issue  # noqa: E402


def test_anomaly_prompt_guidance():
    for part in ("[값 이상 조사]", "매핑 오류", "올바른 ACTION으로 교체하는 처리안", "마스터 이름·설명 불만", "이관 성격",
                 "log_resolution으로 판단 근거"):
        assert part in PROMPT_V3


def test_anomaly_mapping_error_ends_with_update_proposal(db_path):
    """시드 #5: V001에 교반(103)이 붙어 있는데 냉각(104)이어야 한다는 문의 → 매핑 오류 → 103을 104로 교체하는 처리안."""

    def judge(messages, tools):
        results = {m.name: m.content for m in messages if isinstance(m, ToolMessage)}
        # 근거: V001에 103이 미수행(N)으로 연결되어 있고, 104 냉각은 마스터에 있으며 아직 연결되지 않았다.
        assert "V001 → 103 교반 · 미수행 N" in results["get_vessel_mappings"]
        assert "104 냉각" in results["find_action"] and "V001 → 104" not in results["get_vessel_mappings"]
        return tool_call("propose_resolution", {
            "issue_no": 5, "new_status": "완료",
            "result": "매핑 오류로 판단. 근거: V001에 교반(103)이 미수행으로 연결되어 있고 공정상 냉각(104)이 맞음",
            "mapping_change": {"kind": "update", "vessel_id": "V001", "action_seq": "103", "new_action_seq": "104"}}, "p1")

    model = FakeChatModel("f", [tool_call("get_issue", {"issue_no": 5}, "c1"),
                                tool_call("get_vessel_mappings", {"vessel_id": "V001"}, "c2"),
                                tool_call("find_action", {"query": "냉각"}, "c3"), judge])
    thread = issue_thread_id(5, "A001")
    asyncio.run(open_thread(thread))
    result = asyncio.run(run_turn(thread, "y", fake_llm(model)))
    assert result.pending is not None and result.pending["stage"] == "card"
    proposal = result.pending["proposal"]
    assert proposal["mapping_change"] == {"kind": "update", "vessel_id": "V001", "action_seq": "103", "new_action_seq": "104"}
    assert (proposal["new_status"], proposal["risk"]) == ("완료", "write")
    assert result.pending["card"]["after"] == ["V001→103 없음", "V001→104 냉각 · 미수행 N(새 행)"]
    assert result.tool_calls_used == 4


def test_anomaly_master_name_complaint_is_transfer(db_path):
    """마스터 이름 불만(값 이상으로 분류된 새 문의) → 매핑은 맞음 → 처리안 없이 log_resolution + '이관 성격' 답."""
    classified = {"type": "값 이상", "multiple_requests": False, "id_mismatch": False,
                  "reason": "V002의 교반 이름이 이상하다는 문의"}
    out = register_issue("C002", "V002", "103", "반응기 B의 '교반' 표시가 이상합니다. 현장에서는 믹싱이라고 부르는데 값이 틀린 것 같아요.",
                         fake_llm(FakeChatModel("c", [classified])))
    assert out.status == "확인필요"
    n = out.issue_no

    def judge(messages, tools):
        results = {m.name: m.content for m in messages if isinstance(m, ToolMessage)}
        assert "V002 → 103 교반 · 수행됨 Y" in results["get_vessel_mappings"]  # 매핑은 실제로 수행된 정상 연결
        return tool_call("log_resolution", {"issue_no": n, "note": "이관 성격: 매핑 V002→103은 정상(수행됨 Y). "
                                            "ACTION 103 이름(교반) 변경 요청이므로 ACTION 담당자 확인 필요"}, "l1")

    model = FakeChatModel("f", [tool_call("get_issue", {"issue_no": n}, "c1"),
                                tool_call("get_vessel_mappings", {"vessel_id": "V002"}, "c2"), judge,
                                AIMessage("이관 성격입니다. 매핑 V002→103은 정상이고, ACTION 103의 이름 문제라 "
                                          "ACTION 담당자(오세훈, 내선 1003)가 확인해야 합니다.")])
    thread = issue_thread_id(n, "A001")
    asyncio.run(open_thread(thread))
    result = asyncio.run(run_turn(thread, "y", fake_llm(model)))
    assert result.pending is None and "이관 성격" in result.answer and "ACTION 담당자" in result.answer
    conn = connect()
    note = conn.execute("SELECT KIND, CONTENT, USER_ID FROM RESOLUTION_LOG WHERE ISSUE_NO = ?", (n,)).fetchone()
    assert note["KIND"] == "note" and note["CONTENT"].startswith("이관 성격") and note["USER_ID"] == "A001"
    # 처리안이 없으므로 이슈 상태·매핑은 그대로
    assert conn.execute("SELECT STATUS FROM ISSUE WHERE ISSUE_NO = ?", (n,)).fetchone()[0] == "확인필요"
