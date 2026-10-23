"""최소 경로 종단 테스트: 문의 등록 → 자동 분류 → ISSUE 저장 → [이 건 조사] → MCP 도구 1개로 조사 → 최종 답변."""

import asyncio

from langchain_core.messages import AIMessage, ToolMessage

from app.agent import issue_thread_id, run_turn
from app.classify import register_issue
from app.db import connect
from app.fake_llm import FakeChatModel, fake_llm, tool_call


def test_minimal_path(db_path):
    conn = connect()
    mappings_before = conn.execute("SELECT * FROM VESSEL_ACTION ORDER BY 1, 2").fetchall()

    # 1) 요청 고객 C001이 문의 등록 → 자동 분류(구조화 출력 1회) → ISSUE 저장
    classifier = FakeChatModel("fake-1", [{"type": "매핑 생성", "multiple_requests": False, "id_mismatch": False,
                                           "reason": "혼합탱크 1에 냉각(104)을 추가해 달라는 요청"}])
    out = register_issue("C001", "V003", "104", "혼합탱크 1에 냉각 공정을 추가해 주세요", fake_llm(classifier))
    issue = conn.execute("SELECT * FROM ISSUE WHERE ISSUE_NO = ?", (out.issue_no,)).fetchone()
    assert (issue["STATUS"], issue["OWNER_ROLE"]) == ("처리대기", "매핑 담당자")

    # 2) 관리자 A001이 [이 건 조사] → 비서가 find_vessel로 조사 → 최종 답변
    def answer(messages, tools):
        found = [m for m in messages if isinstance(m, ToolMessage)][-1].content
        return AIMessage(f"문의의 '혼합탱크 1'은 {found.splitlines()[1].strip('- ')}입니다.")

    assistant = FakeChatModel("fake-1", [tool_call("find_vessel", {"query": "혼합탱크"}), answer])
    turn = asyncio.run(run_turn(issue_thread_id(out.issue_no, "A001"),
                                f"이슈 #{out.issue_no}을 조사해 주세요. 문의내용: {issue['INQUIRY']}",
                                fake_llm(assistant)))
    assert turn.answer == "문의의 '혼합탱크 1'은 V003 혼합탱크 1입니다."
    assert turn.tool_calls_used == 1

    # 3) 이 경로는 읽기만 한다: 매핑 변화 없음, 이슈 상태 그대로
    assert conn.execute("SELECT * FROM VESSEL_ACTION ORDER BY 1, 2").fetchall() == mappings_before
    assert conn.execute("SELECT STATUS FROM ISSUE WHERE ISSUE_NO = ?", (out.issue_no,)).fetchone()[0] == "처리대기"
    conn.close()
