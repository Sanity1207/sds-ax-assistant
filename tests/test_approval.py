"""처리안 승인(묶음 5). 모두 가짜 LLM."""

import pytest

from app.resolution import change_text, check_proposal
from app.tools import propose_resolution

CREATE_2 = {"kind": "create", "vessel_id": "V003", "action_seq": "104"}  # #2 처리대기: 매핑 생성
UPDATE_3 = {"kind": "update", "vessel_id": "V003", "action_seq": "103", "new_action_seq": "105"}  # #3 매핑 수정(N)
DELETE_4 = {"kind": "delete", "vessel_id": "V004", "action_seq": "105"}  # #4 수행된(Y) 매핑 삭제
UPDATE_5 = {"kind": "update", "vessel_id": "V001", "action_seq": "103", "new_action_seq": "104"}  # #5 값 이상


# ---- T24: propose_resolution 검사·위험 등급 ----

@pytest.mark.parametrize("issue, change, status, risk, executed", [
    (2, CREATE_2, "완료", "write", None),
    (3, UPDATE_3, "완료", "write", "N"),
    (4, DELETE_4, "완료", "destructive", "Y"),
    (5, UPDATE_5, "완료", "write", "N"),
    (5, None, "완료", "write", None),  # 확인필요: 확인 결과 정상 → 변경 없이 완료
    (6, None, "반려", "write", None),
    (7, None, "완료", "write", None),  # 이관: 담당자 확인 후 변경 없이 완료
    (7, None, "거절", "write", None),
    (2, None, "반려", "write", None),
    (4, None, "거절", "write", None),
])
def test_propose_valid(db_path, issue, change, status, risk, executed):
    checked = check_proposal(issue, change, status, "설명", issue)
    assert checked.ok, checked.errors
    p = checked.proposal
    assert (p["issue_no"], p["new_status"], p["risk"], p["executed_yn"]) == (issue, status, risk, executed)
    assert p["mapping_change"] == change


@pytest.mark.parametrize("issue, screen, change, status, part", [
    (3, 2, CREATE_2, "완료", "지금 화면의 이슈 #2에만"),  # 다른 이슈 번호
    (2, None, CREATE_2, "완료", "이슈 없는 대화에서는"),
    (1, 1, None, "완료", "이미 닫힌 건(완료)"),
    (8, 8, None, "거절", "이미 닫힌 건(반려)"),
    (2, 2, CREATE_2, "반려", "매핑을 바꾸는 처리안의 새 상태는 완료뿐"),  # 매핑 변경 + 반려
    (2, 2, CREATE_2, "거절", "매핑을 바꾸는 처리안의 새 상태는 완료뿐"),
    (2, 2, None, "완료", "매핑 변경이 있어야 완료"),  # 처리대기인데 변경 없이 완료
    (7, 7, {"kind": "create", "vessel_id": "V002", "action_seq": "103"}, "완료", "이관 건은 매핑 변경 없이만"),
    (2, 2, CREATE_2, "처리대기", "new_status '처리대기'는 허용되지 않습니다"),
    (2, 2, CREATE_2, "확인필요", "허용되지 않습니다"),
    (2, 2, {"kind": "create", "vessel_id": "V001", "action_seq": "104"}, "완료", "VESSEL_ID 칸 값 V003"),
    (2, 2, {"kind": "create", "vessel_id": "V003", "action_seq": "105"}, "완료", "ACTION_SEQ 칸 값 104"),
    (2, 2, {"kind": "insert", "vessel_id": "V003", "action_seq": "104"}, "완료", "kind 'insert'는 없습니다"),
    (3, 3, {"kind": "create", "vessel_id": "V003", "action_seq": "103"}, "완료", "이미 있어 생성할 수 없습니다"),
    (2, 2, {"kind": "delete", "vessel_id": "V003", "action_seq": "104"}, "완료", "매핑이 없어 삭제할 수 없습니다"),
    (3, 3, {"kind": "update", "vessel_id": "V003", "action_seq": "103"}, "완료", "new_action_seq)이 필요합니다"),
    (3, 3, dict(UPDATE_3, new_action_seq="999"), "완료", "'999'는 ACTION_MST에 없습니다"),
    (3, 3, dict(UPDATE_3, new_action_seq="103"), "완료", "바꾸기 전 ACTION과 같습니다"),
    (5, 5, dict(UPDATE_5, new_action_seq="101"), "완료", "V001→101 매핑이 이미 있어"),
    (2, 2, "엉터리", "완료", "mapping_change를 해석할 수 없습니다"),
    (2, 2, ["V003"], "완료", "객체 또는 null"),
])
def test_propose_rejected(db_path, issue, screen, change, status, part):
    checked = check_proposal(issue, change, status, "설명", screen)
    assert not checked.ok
    assert any(part in e for e in checked.errors), checked.errors
    text = checked.error_text()
    assert text.startswith("[오류] 처리안 검사에서 거부했습니다") and "다음 행동" in text


def test_propose_empty_result_rejected(db_path):
    checked = check_proposal(2, CREATE_2, "완료", "   ", 2)
    assert not checked.ok and "비어 있습니다" in checked.errors[0]


def test_propose_collects_all_errors(db_path):
    checked = check_proposal(2, {"kind": "create", "vessel_id": "V001", "action_seq": "105"}, "반려", "", 2)
    assert len(checked.errors) >= 4


def test_propose_json_string_change(db_path):
    checked = check_proposal("#2", '{"kind": "create", "vessel_id": "V003", "action_seq": "104"}', " 완료 ", "설명", 2)
    assert checked.ok and checked.proposal["mapping_change"] == CREATE_2 and checked.proposal["new_status"] == "완료"
    for none in (None, "", "null", {}):
        assert check_proposal(5, none, "완료", "정상 확인", 5).proposal["mapping_change"] is None


def test_propose_change_text():
    assert change_text(CREATE_2) == "[매핑 변경] V003→104 생성"
    assert change_text(DELETE_4) == "[매핑 변경] V004→105 삭제"
    assert change_text(UPDATE_3) == "[매핑 변경] V003→103 삭제, V003→105 생성"
    assert change_text(None) == "[매핑 변경 없음]"


def test_propose_tool_uses_screen_issue(db_path, monkeypatch):
    monkeypatch.setenv("APP_SCREEN_ISSUE", "2")
    monkeypatch.setenv("APP_ACTING_USER", "A001")
    ok = propose_resolution(2, "완료", "V003에 냉각 공정 연결", CREATE_2)
    assert ok.startswith("처리안 검사 통과: #2 처리대기 → 완료 · [매핑 변경] V003→104 생성 · 위험 등급 write.")
    assert "아직 반영되지 않았습니다" in ok
    other = propose_resolution(3, "완료", "x", UPDATE_3)
    assert other.startswith("[오류] 처리안 검사에서 거부했습니다: 처리안은 지금 화면의 이슈 #2에만")
    monkeypatch.setenv("APP_SCREEN_ISSUE", "4")
    assert "위험 등급 destructive" in propose_resolution(4, "완료", "배출 연결 삭제", DELETE_4)


def test_propose_check_writes_nothing(db_path):
    from app.db import connect

    conn = connect()
    before = (conn.execute("SELECT * FROM VESSEL_ACTION ORDER BY 1, 2").fetchall(),
              conn.execute("SELECT * FROM ISSUE ORDER BY 1").fetchall())
    for issue, change in ((2, CREATE_2), (3, UPDATE_3), (4, DELETE_4)):
        assert check_proposal(issue, change, "완료", "x", issue).ok
    after = (conn.execute("SELECT * FROM VESSEL_ACTION ORDER BY 1, 2").fetchall(),
             conn.execute("SELECT * FROM ISSUE ORDER BY 1").fetchall())
    assert [list(map(tuple, t)) for t in before] == [list(map(tuple, t)) for t in after]


# ---- T25: interrupt 승인 카드 ----

import asyncio  # noqa: E402
import json  # noqa: E402

from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402

from app.agent import (CARD_QUESTION, CARD_RETRY, answer_approval, issue_thread_id, pending_approval,  # noqa: E402
                       run_turn)
from app.auth import PermissionDenied  # noqa: E402
from app.db import connect  # noqa: E402
from app.fake_llm import FakeChatModel, fake_llm, tool_call  # noqa: E402


def business(conn=None):
    """업무 데이터(VESSEL_ACTION·ISSUE) 스냅숏."""
    conn = conn or connect()
    return ([tuple(r) for r in conn.execute("SELECT * FROM VESSEL_ACTION ORDER BY 1, 2")],
            [tuple(r) for r in conn.execute("SELECT * FROM ISSUE ORDER BY 1")])


def propose(issue, change, status="완료", result="요청대로 처리", user="A001", extra=()):
    """가짜 LLM이 propose_resolution을 부르게 해 승인 카드에서 멈춘다."""
    thread = issue_thread_id(issue, user)
    args = {"issue_no": issue, "new_status": status, "result": result, "mapping_change": change}
    model = FakeChatModel("f", [tool_call("propose_resolution", args, "p1"), *extra])
    turn = asyncio.run(run_turn(thread, "처리안 만들어 줘", fake_llm(model)))
    return thread, turn, model


def test_card_interrupts_with_db_values(db_path):
    before = business()
    thread, turn, model = propose(2, CREATE_2, result="V003에 냉각 공정 연결")
    assert turn.pending is not None and turn.pending["stage"] == "card"
    card = turn.pending["card"]
    issue = connect().execute("SELECT * FROM ISSUE WHERE ISSUE_NO = 2").fetchone()
    assert card["inquiry"] == issue["INQUIRY"] and card["requester_id"] == issue["REQUESTER_ID"]
    assert (card["risk"], card["tool"], card["issue_no"], card["issue_status"], card["new_status"]) == (
        "write", "propose_resolution", 2, "처리대기", "완료")
    assert card["before"] == ["V003→104 없음"] and card["after"] == ["V003→104 냉각 · 미수행 N(새 행)"]
    assert card["executed_yn"] == "-" and card["change"] == "[매핑 변경] V003→104 생성"
    assert card["result"] == "[매핑 변경] V003→104 생성 V003에 냉각 공정 연결"
    assert turn.answer.startswith("[승인 카드] 위험 등급: write · 도구: propose_resolution")
    assert turn.answer.endswith(CARD_QUESTION)
    assert business() == before  # 승인 전에는 아무것도 바뀌지 않는다
    assert len(model.calls) == 1  # 카드에서 멈춘 뒤 LLM을 더 부르지 않는다
    tool_msg = [m for m in turn.messages if isinstance(m, ToolMessage)][-1]
    assert tool_msg.content.startswith("처리안 검사 통과")


def test_card_destructive_reads_executed_from_db(db_path):
    _, turn, _ = propose(4, DELETE_4, result="배출 공정 중단")
    card = turn.pending["card"]
    mapping = connect().execute("SELECT * FROM VESSEL_ACTION WHERE VESSEL_ID = 'V004' AND ACTION_SEQ = '105'").fetchone()
    assert card["risk"] == "destructive" and card["executed_yn"] == mapping["EXECUTED_YN"] == "Y"
    assert card["before"] == [f"V004→105 배출 · 수행됨 Y({mapping['EXECUTED_AT']})"] and card["after"] == ["V004→105 없음"]


def test_card_update_shows_both_rows(db_path):
    _, turn, _ = propose(3, UPDATE_3)
    card = turn.pending["card"]
    assert card["before"] == ["V003→103 교반 · 미수행 N", "V003→105 없음"]
    assert card["after"] == ["V003→103 없음", "V003→105 배출 · 미수행 N(새 행)"]
    assert card["executed_yn"] == "N"


def test_card_rejected_proposal_has_no_card(db_path):
    other = {"issue_no": 3, "new_status": "완료", "result": "x", "mapping_change": UPDATE_3}
    model = FakeChatModel("f", [tool_call("propose_resolution", other, "p1"), AIMessage("다른 이슈라 만들 수 없습니다.")])
    turn = asyncio.run(run_turn(issue_thread_id(2, "A001"), "3번 처리안", fake_llm(model)))
    assert turn.pending is None and turn.answer == "다른 이슈라 만들 수 없습니다."
    tool_msg = [m for m in turn.messages if isinstance(m, ToolMessage)][-1]
    assert tool_msg.content.startswith("[오류] 처리안 검사에서 거부했습니다: 처리안은 지금 화면의 이슈 #2에만")
    assert asyncio.run(pending_approval(issue_thread_id(2, "A001"))) is None


@pytest.mark.parametrize("text", ["ㅇㅇ", "네", "yes", "승인", "Y?", "", "yn", "okay"])
def test_card_other_input_asks_again_without_llm(db_path, text):
    thread, turn, model = propose(2, CREATE_2)
    before = business()
    calls = len(model.calls)
    again = asyncio.run(answer_approval(thread, text, llm=fake_llm(model)))
    assert again.answer == CARD_RETRY and not again.llm_called and again.pending["stage"] == "card"
    assert len(model.calls) == calls  # LLM 호출 0
    assert business() == before
    # 대화창 입력으로 와도(run_turn) 승인 응답으로 판정해 비서에게 보내지 않는다.
    via_chat = asyncio.run(run_turn(thread, text, fake_llm(model)))
    assert via_chat.answer == CARD_RETRY and len(model.calls) == calls
    runs = connect().execute("SELECT RUN_KIND, RESULT FROM PROCESS_LOG ORDER BY rowid").fetchall()
    assert [tuple(r) for r in runs] == [("대화 입력", "승인 대기"), ("승인 응답", "다시 묻기"), ("승인 응답", "다시 묻기")]
    event = connect().execute("SELECT NAME, SUCCESS FROM CALL_LOG WHERE EVENT_KIND = 'approval' ORDER BY rowid DESC").fetchone()
    assert tuple(event) == ("다시 묻기", "N")


def test_card_recorded_and_survives_reopen(db_path):
    thread, turn, _ = propose(2, CREATE_2)
    conn = connect()
    run = conn.execute("SELECT * FROM PROCESS_LOG").fetchone()
    assert (run["RUN_KIND"], run["RESULT"], run["TOOL_CALLS"]) == ("대화 입력", "승인 대기", 1)
    events = conn.execute("SELECT EVENT_KIND, NAME, INPUT, OUTPUT FROM CALL_LOG WHERE RUN_ID = ? ORDER BY SEQ",
                          (run["RUN_ID"],)).fetchall()
    tool = next(e for e in events if e["EVENT_KIND"] == "tool")
    assert json.loads(tool["INPUT"])["risk"] == "write" and json.loads(tool["INPUT"])["auto_run"] is False
    card_event = next(e for e in events if e["EVENT_KIND"] == "approval")
    assert card_event["NAME"] == "승인 카드" and json.loads(card_event["OUTPUT"]) == turn.pending["card"]
    # 다시 열어도(새 체크포인터 연결) 승인 대기 처리안이 이어진다.
    pending = asyncio.run(pending_approval(thread))
    assert pending["card"] == turn.pending["card"]


def test_card_destructive_tool_event_risk(db_path):
    propose(4, DELETE_4)
    tool = connect().execute("SELECT INPUT FROM CALL_LOG WHERE EVENT_KIND = 'tool'").fetchone()[0]
    assert json.loads(tool)["risk"] == "destructive"


@pytest.mark.parametrize("user_id", ["C001", "C005"])
def test_card_answer_requires_admin(db_path, user_id):
    thread, _, _ = propose(2, CREATE_2)
    with pytest.raises(PermissionDenied):
        asyncio.run(answer_approval(thread, "y", user_id=user_id))
    assert asyncio.run(pending_approval(thread)) is not None


def test_card_one_proposal_at_a_time(db_path):
    calls = AIMessage("", tool_calls=[
        {"name": "propose_resolution", "args": {"issue_no": 2, "new_status": "완료", "result": "a", "mapping_change": CREATE_2},
         "id": "p1"},
        {"name": "propose_resolution", "args": {"issue_no": 2, "new_status": "반려", "result": "b", "mapping_change": None},
         "id": "p2"}])
    turn = asyncio.run(run_turn(issue_thread_id(2, "A001"), "처리안", fake_llm(FakeChatModel("f", [calls]))))
    assert turn.pending["proposal"]["new_status"] == "완료"
    second = [m for m in turn.messages if isinstance(m, ToolMessage)][-1]
    assert second.content.startswith("[오류] 한 번에 처리안 하나만")


# ---- T26: y 실행 — 재검증 + 한 트랜잭션 ----

def logs(issue):
    return [tuple(r) for r in connect().execute(
        "SELECT KIND, CONTENT, USER_ID FROM RESOLUTION_LOG WHERE ISSUE_NO = ? ORDER BY LOG_ID", (issue,))]


def mapping(v, a):
    row = connect().execute("SELECT * FROM VESSEL_ACTION WHERE VESSEL_ID = ? AND ACTION_SEQ = ?", (v, a)).fetchone()
    return None if row is None else tuple(row)


def test_execute_create(db_path):
    thread, _, model = propose(2, CREATE_2, result="V003에 냉각 공정 연결")
    calls = len(model.calls)
    done = asyncio.run(answer_approval(thread, " Y ", llm=fake_llm(model)))
    assert done.answer == "반영 완료: [매핑 변경] V003→104 생성, #2 완료" and done.pending is None
    assert len(model.calls) == calls  # 반영 후 LLM을 부르지 않는다
    assert mapping("V003", "104") == ("V003", "104", "N", None)
    issue = connect().execute("SELECT * FROM ISSUE WHERE ISSUE_NO = 2").fetchone()
    assert (issue["STATUS"], issue["RESULT"], issue["PROCESSOR_ID"]) == (
        "완료", "[매핑 변경] V003→104 생성 V003에 냉각 공정 연결", "A001")
    assert issue["PROCESSED_AT"] is not None
    assert [k for k, _, _ in logs(2)] == ["승인", "실행 성공"]
    assert logs(2)[1] == ("실행 성공", "반영 완료: [매핑 변경] V003→104 생성, #2 완료", "A001")
    run = connect().execute("SELECT * FROM PROCESS_LOG ORDER BY rowid DESC").fetchone()
    assert (run["RUN_KIND"], run["RESULT"]) == ("승인 응답", "답변 완료")
    commit = connect().execute("SELECT OUTPUT, SUCCESS FROM CALL_LOG WHERE EVENT_KIND = 'commit'").fetchone()
    assert tuple(commit) == ("커밋", "Y")
    assert asyncio.run(pending_approval(thread)) is None
    # 대화 맥락에는 정해진 문장이 남는다.
    assert done.messages[-1].content == done.answer


def test_execute_update_is_delete_then_create(db_path):
    thread, _, _ = propose(3, UPDATE_3)
    done = asyncio.run(answer_approval(thread, "y"))
    assert done.answer == "반영 완료: [매핑 변경] V003→103 삭제, V003→105 생성, #3 완료"
    assert mapping("V003", "103") is None and mapping("V003", "105") == ("V003", "105", "N", None)


def test_execute_without_mapping_change(db_path):
    before = business()[0]
    thread, _, _ = propose(5, None, result="확인 결과 매핑 정상")
    done = asyncio.run(answer_approval(thread, "y"))
    assert done.answer == "반영 완료: [매핑 변경 없음], #5 완료"
    assert business()[0] == before
    assert connect().execute("SELECT RESULT FROM ISSUE WHERE ISSUE_NO = 5").fetchone()[0] == "[매핑 변경 없음] 확인 결과 매핑 정상"


def explain(messages, tools):
    assert tools is None
    return AIMessage("이슈가 이미 닫혀 반영하지 않았습니다. 이슈 상태를 확인해 주세요.")


def test_execute_revalidation_fails_when_issue_closed(db_path):
    """같은 이슈에 처리안이 둘 대기하면 먼저 승인된 것만 반영되고, 나머지는 재검증에서 막힌다."""
    t1, _, _ = propose(2, CREATE_2, user="A001")
    t2, _, m2 = propose(2, None, status="반려", user="A002", extra=[explain])
    assert asyncio.run(answer_approval(t1, "y")).answer.startswith("반영 완료")
    before = business()
    failed = asyncio.run(answer_approval(t2, "y", llm=fake_llm(m2)))
    assert business() == before  # 업무 데이터 변화 0
    assert failed.answer.startswith("처리안을 반영하지 못했습니다(실행 실패: 재검증 실패: 이슈 #2는 이미 닫힌 건(완료)")
    assert "이슈가 이미 닫혀 반영하지 않았습니다" in failed.answer
    kinds = [(k, u) for k, _, u in logs(2)]
    assert kinds == [("승인", "A001"), ("실행 성공", "A001"), ("승인", "A002"), ("실행 실패", "A002")]
    assert logs(2)[-1][1].startswith("실행 실패(재검증 실패: ")
    commit = connect().execute("SELECT OUTPUT, SUCCESS FROM CALL_LOG WHERE EVENT_KIND = 'commit' ORDER BY rowid DESC").fetchone()
    assert commit["SUCCESS"] == "N" and commit["OUTPUT"].startswith("롤백: 재검증 실패")
    assert asyncio.run(pending_approval(t2)) is None  # 자동 재제안 없음


def test_execute_revalidation_executed_changed(db_path):
    thread, _, model = propose(3, UPDATE_3, extra=[explain])
    conn = connect()
    with conn:
        conn.execute("UPDATE VESSEL_ACTION SET EXECUTED_YN = 'Y', EXECUTED_AT = '2026-10-23 09:00:00'"
                     " WHERE VESSEL_ID = 'V003' AND ACTION_SEQ = '103'")
    before = business()
    failed = asyncio.run(answer_approval(thread, "y", llm=fake_llm(model)))
    assert business() == before
    assert "EXECUTED_YN이 제안 시점(N)과 다릅니다(지금: Y)" in logs(3)[-1][1]
    assert failed.answer.startswith("처리안을 반영하지 못했습니다")


def test_execute_revalidation_mapping_appeared(db_path):
    thread, _, model = propose(2, CREATE_2, extra=[explain])
    conn = connect()
    with conn:
        conn.execute("INSERT INTO VESSEL_ACTION VALUES ('V003', '104', 'N', NULL)")
    before = business()
    asyncio.run(answer_approval(thread, "y", llm=fake_llm(model)))
    assert business() == before
    assert "매핑이 이미 있어 생성할 수 없습니다" in logs(2)[-1][1]


def test_execute_commit_error_rolls_back_everything(db_path):
    thread, _, model = propose(3, UPDATE_3, extra=[explain])
    conn = connect()
    conn.execute("CREATE TRIGGER BOOM BEFORE INSERT ON RESOLUTION_LOG WHEN NEW.KIND = '실행 성공'"
                 " BEGIN SELECT RAISE(ABORT, 'boom'); END")
    conn.commit()
    before = business()
    failed = asyncio.run(answer_approval(thread, "y", llm=fake_llm(model)))
    # 매핑 삭제·생성과 ISSUE 갱신이 모두 롤백된다(부분 처리 상태 없음).
    assert business() == before
    assert mapping("V003", "103") is not None and mapping("V003", "105") is None
    assert logs(3)[-1][0] == "실행 실패" and "DB 오류" in logs(3)[-1][1] and "boom" in logs(3)[-1][1]
    assert failed.answer.startswith("처리안을 반영하지 못했습니다(실행 실패: DB 오류")


def test_execute_failure_finalize_llm_down_uses_python_sentence(db_path):
    thread, _, _ = propose(2, CREATE_2)
    conn = connect()
    with conn:
        conn.execute("UPDATE ISSUE SET STATUS = '거절', PROCESSED_AT = '2026-10-23 09:00:00', PROCESSOR_ID = 'A002'"
                     " WHERE ISSUE_NO = 2")
    from app.fake_llm import FakeLLMError

    down = fake_llm(FakeChatModel("f", error=FakeLLMError("x")))
    failed = asyncio.run(answer_approval(thread, "y", llm=down))
    assert failed.answer.startswith("처리안을 반영하지 못했습니다(실행 실패: 재검증 실패")
    assert "승인한 처리안을 반영하지 못해 아무것도 바꾸지 않았습니다" in failed.answer
