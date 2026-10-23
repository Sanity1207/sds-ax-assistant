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
