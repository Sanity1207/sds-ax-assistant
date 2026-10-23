from app.classify import FAILED_RESULT, Classification, classify_issue, register_issue
from app.db import connect
from app.fake_llm import FakeChatModel, FakeLLMError, fake_llm
from app.seed import ISSUES

NEW_NO = len(ISSUES) + 1  # 시드 이슈 다음 번호

CREATE = {"type": "매핑 생성", "master_target": None, "multiple_requests": False,
          "id_mismatch": False, "reason": "V003에 104를 추가해 달라는 요청"}


def mappings(conn):
    return [tuple(r) for r in conn.execute("SELECT * FROM VESSEL_ACTION ORDER BY 1, 2")]


def test_skeleton_register_creates_issue(db_path):
    conn = connect()
    before = mappings(conn)
    model = FakeChatModel("fake-1", [CREATE])
    out = register_issue("C001", "V003", "104", "V003에 104 추가해 주세요", fake_llm(model), conn)

    rows = conn.execute("SELECT * FROM ISSUE WHERE ISSUE_NO > ?", (len(ISSUES),)).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["ISSUE_NO"] == out.issue_no == NEW_NO
    assert (row["STATUS"], row["OWNER_ROLE"]) == ("처리대기", "매핑 담당자")
    assert row["RESULT"] == "매핑 생성 요청으로 분류. 근거: V003에 104를 추가해 달라는 요청"
    assert row["REQUESTER_ID"] == "C001" and row["PROCESSED_AT"] is None and row["PROCESSOR_ID"] is None
    assert out.model_id == "fake-1"
    assert mappings(conn) == before  # 분류 단계에서는 매핑을 바꾸지 않는다.
    # 구조화 출력 1회, 프롬프트에 입력칸 값과 문의 원문이 들어간다.
    assert len(model.calls) == 1
    kind, messages, schema = model.calls[0]
    assert kind == "structured" and schema is Classification
    assert "V003" in messages[1].content and "V003에 104 추가해 주세요" in messages[1].content
    assert "신뢰하지 않는 데이터" in messages[0].content
    conn.close()


def test_skeleton_undecidable_needs_check(db_path):
    other = dict(CREATE, type="판단 불가", reason="무엇을 원하는지 알 수 없음")
    out = register_issue("C002", "V001", "103", "확인 부탁", fake_llm(FakeChatModel("f", [other])))
    assert (out.status, out.owner_role) == ("확인필요", "매핑 담당자")


def test_skeleton_all_models_fail_keeps_issue(db_path):
    llm = fake_llm(FakeChatModel("f1", error=FakeLLMError("x")), FakeChatModel("f2", error=FakeLLMError("y")))
    out = register_issue("C001", "V003", "104", "V003에 104 추가", llm)
    row = connect().execute("SELECT STATUS, OWNER_ROLE, RESULT FROM ISSUE WHERE ISSUE_NO = ?", (out.issue_no,)).fetchone()
    assert tuple(row) == ("확인필요", "매핑 담당자", FAILED_RESULT)
    assert "모든 모델 실패" in out.error


def test_skeleton_schema_error_keeps_issue(db_path):
    out = register_issue("C001", "V003", "104", "V003에 104 추가", fake_llm(FakeChatModel("f", ["형식 없는 답"])))
    assert (out.status, out.result) == ("확인필요", FAILED_RESULT)


def test_skeleton_reclassify_updates_same_issue(db_path):
    first = register_issue("C001", "V003", "104", "V003에 104 추가", fake_llm(FakeChatModel("f", error=FakeLLMError("x"))))
    again = classify_issue(first.issue_no, fake_llm(FakeChatModel("f", [CREATE])))
    conn = connect()
    assert conn.execute("SELECT COUNT(*) FROM ISSUE").fetchone()[0] == NEW_NO
    assert again.issue_no == first.issue_no and again.status == "처리대기"


def test_skeleton_master_target_aliases():
    c = Classification.model_validate(dict(CREATE, type="마스터 요청", master_target="BOTH"))
    assert c.master_target == "둘 다"
    assert Classification.model_validate(dict(CREATE, master_target="null")).master_target is None


# ---- T14: 등록 전 Python 검사 ----

import pytest  # noqa: E402

from app.classify import RegistrationRejected, precheck  # noqa: E402

VESSEL_CHOICES = "고를 수 있는 값: V001 반응기 A, V002 반응기 B, V003 혼합탱크 1, V004 저장탱크 1, V005 세정조 1"
ACTION_CHOICES = "고를 수 있는 값: 101 원료 투입, 102 가열, 103 교반, 104 냉각, 105 배출, 106 세정"


@pytest.mark.parametrize("requester, vessel, action, inquiry, expected", [
    ("X999", "V003", "104", "추가", ["사용자 'X999'가 USER_MST에 없습니다."]),
    ("", "V003", "104", "추가", ["사용자 ''가 USER_MST에 없습니다."]),
    ("C005", "V003", "104", "추가", ["정하늘(C005, 조회 고객)에게는 문의 등록 권한이 없습니다."]),
    ("SYSTEM", "V003", "104", "추가", ["시스템(SYSTEM, 시스템)에게는 문의 등록 권한이 없습니다."]),
    ("C001", "", "104", "추가", [f"VESSEL_ID 칸이 비어 있습니다. {VESSEL_CHOICES}"]),
    ("C001", None, "104", "추가", [f"VESSEL_ID 칸이 비어 있습니다. {VESSEL_CHOICES}"]),
    ("C001", "V003", "  ", "추가", [f"ACTION_SEQ 칸이 비어 있습니다. {ACTION_CHOICES}"]),
    ("C001", "V003,V004", "104", "추가",
     [f"VESSEL_ID 칸에 값이 여러 개입니다(받은 값: 'V003,V004'). 값은 하나만 고르세요. {VESSEL_CHOICES}"]),
    ("C001", "V003", "103 104", "추가",
     [f"ACTION_SEQ 칸에 값이 여러 개입니다(받은 값: '103 104'). 값은 하나만 고르세요. {ACTION_CHOICES}"]),
    ("C001", ["V003", "V004"], "104", "추가",
     [f"VESSEL_ID 칸에 값이 여러 개입니다(받은 값: V003, V004). 값은 하나만 고르세요. {VESSEL_CHOICES}"]),
    ("C001", "V009", "104", "추가", [f"VESSEL_ID 칸의 값 'V009'는 VESSEL_MST에 없습니다. {VESSEL_CHOICES}"]),
    ("C001", "V003", "999", "추가", [f"ACTION_SEQ 칸의 값 '999'는 ACTION_MST에 없습니다. {ACTION_CHOICES}"]),
    ("C001", "v003", "104", "추가", [f"VESSEL_ID 칸의 값 'v003'는 VESSEL_MST에 없습니다. {VESSEL_CHOICES}"]),
    ("C001", "V003", "104", "   ", ["문의내용이 비어 있습니다. 요청 내용을 적어 주세요."]),
    # 문제가 여러 개면 한 번에 모두 알려 준다.
    ("C005", "", "999", "추가", ["정하늘(C005, 조회 고객)에게는 문의 등록 권한이 없습니다.",
                               f"VESSEL_ID 칸이 비어 있습니다. {VESSEL_CHOICES}",
                               f"ACTION_SEQ 칸의 값 '999'는 ACTION_MST에 없습니다. {ACTION_CHOICES}"]),
])
def test_precheck_rejects(db_path, requester, vessel, action, inquiry, expected):
    conn = connect()
    before = conn.execute("SELECT COUNT(*) FROM ISSUE").fetchone()[0]
    model = FakeChatModel("fake-1", [CREATE])
    with pytest.raises(RegistrationRejected) as info:
        register_issue(requester, vessel, action, inquiry, fake_llm(model), conn)
    reasons = info.value.reasons
    # 권한 사유 문장 끝의 '가능한 권한: …'은 비교에서 뺀다.
    assert [r.split(" 가능한 권한:")[0] for r in reasons] == expected
    assert conn.execute("SELECT COUNT(*) FROM ISSUE").fetchone()[0] == before  # 저장하지 않음
    assert model.calls == []  # LLM 호출 0
    run = conn.execute("SELECT * FROM PROCESS_LOG").fetchone()
    assert (run["RUN_KIND"], run["RESULT"], run["ISSUE_NO"], run["USER_ID"]) == ("문의 등록", "등록 거부", None, "SYSTEM")
    [event] = conn.execute("SELECT * FROM CALL_LOG WHERE RUN_ID = ?", (run["RUN_ID"],)).fetchall()
    assert (event["EVENT_KIND"], event["NAME"]) == ("branch", "등록 전 검사")
    assert event["OUTPUT"].startswith("거부: ") and expected[0] in event["OUTPUT"]
    conn.close()


def test_precheck_passes_valid_input(db_path):
    conn = connect()
    assert precheck("C001", "V003", "104", "V003에 104 추가", conn) == []
    assert precheck("A001", " V003 ", ["104"], "추가", conn) == []  # 앞뒤 공백·값 하나짜리 목록은 통과
    out = register_issue("A001", " V003 ", ["104"], " 추가 ", fake_llm(FakeChatModel("f", [CREATE])), conn)
    row = conn.execute("SELECT VESSEL_ID, ACTION_SEQ, INQUIRY FROM ISSUE WHERE ISSUE_NO = ?", (out.issue_no,)).fetchone()
    assert tuple(row) == ("V003", "104", "추가")
    conn.close()


# ---- T15: 규칙표 전체 ----

from app.classify import MASTER_RULES, RULES  # noqa: E402

# (분류 출력 일부, 기대 처리상태, 기대 담당자, 기대 처리결과). 규칙표의 모든 행 + 적용 순서.
RULE_CASES = [
    ({"type": "매핑 생성"}, "처리대기", "매핑 담당자", "매핑 생성 요청으로 분류. 근거: R"),
    ({"type": "매핑 수정"}, "처리대기", "매핑 담당자", "매핑 수정 요청으로 분류. 근거: R"),
    ({"type": "매핑 삭제"}, "처리대기", "매핑 담당자", "매핑 삭제 요청으로 분류. 근거: R"),
    ({"type": "값 이상"}, "확인필요", "매핑 담당자", "값 이상 요청으로 분류. 근거: R"),
    ({"type": "마스터 요청", "master_target": "VESSEL"}, "이관", "VESSEL 담당자",
     "마스터 요청(VESSEL)으로 분류해 VESSEL 담당자에게 이관. 근거: R"),
    ({"type": "마스터 요청", "master_target": "ACTION"}, "이관", "ACTION 담당자",
     "마스터 요청(ACTION)으로 분류해 ACTION 담당자에게 이관. 근거: R"),
    ({"type": "마스터 요청", "master_target": "둘 다"}, "이관", "VESSEL 담당자, ACTION 담당자",
     "마스터 요청(VESSEL·ACTION)으로 분류해 VESSEL 담당자, ACTION 담당자에게 이관. 근거: R"),
    ({"type": "마스터 요청", "master_target": "모름"}, "반려", "매핑 담당자",
     "마스터 요청이지만 대상(VESSEL·ACTION)을 알 수 없어 반려. 근거: R"),
    ({"type": "마스터 요청", "master_target": None}, "반려", "매핑 담당자",
     "마스터 요청이지만 대상(VESSEL·ACTION)을 알 수 없어 반려. 근거: R"),
    ({"type": "담당 외"}, "거절", "", "담당 외 요청이라 거절. 근거: R"),
    ({"type": "판단 불가"}, "확인필요", "매핑 담당자", "유형을 판단할 수 없어 매핑 담당자 확인 필요. 근거: R"),
    # 적용 순서: multiple_requests → id_mismatch → type별
    ({"type": "매핑 생성", "multiple_requests": True}, "반려", "매핑 담당자", "한 문의에 요청이 여러 개라 반려. 근거: R"),
    ({"type": "매핑 생성", "id_mismatch": True}, "반려", "매핑 담당자", "입력칸과 문의내용의 ID가 달라 반려. 근거: R"),
    ({"type": "담당 외", "multiple_requests": True, "id_mismatch": True}, "반려", "매핑 담당자",
     "한 문의에 요청이 여러 개라 반려. 근거: R"),
    ({"type": "마스터 요청", "master_target": "VESSEL", "id_mismatch": True}, "반려", "매핑 담당자",
     "입력칸과 문의내용의 ID가 달라 반려. 근거: R"),
    # 마스터 요청이 아니면 master_target은 보지 않는다.
    ({"type": "매핑 생성", "master_target": "VESSEL"}, "처리대기", "매핑 담당자", "매핑 생성 요청으로 분류. 근거: R"),
]


@pytest.mark.parametrize("fields, status, owner, result", RULE_CASES,
                         ids=[f"{i}-{c[0]['type']}-{c[1]}" for i, c in enumerate(RULE_CASES)])
def test_rules_table(db_path, fields, status, owner, result):
    conn = connect()
    before = mappings(conn)
    answer = {"multiple_requests": False, "id_mismatch": False, "master_target": None, "reason": " R \n"} | fields
    out = register_issue("C001", "V003", "104", "문의", fake_llm(FakeChatModel("f", [answer])), conn)
    row = conn.execute("SELECT * FROM ISSUE WHERE ISSUE_NO = ?", (out.issue_no,)).fetchone()
    assert (row["STATUS"], row["OWNER_ROLE"], row["RESULT"]) == (status, owner, result)
    assert (out.status, out.owner_role, out.result) == (status, owner, result)
    if status in ("반려", "거절"):
        # 분류 단계에서 닫히는 건: 처리일시 = 분류 시각, 처리자 = SYSTEM
        assert row["PROCESSOR_ID"] == "SYSTEM" and row["PROCESSED_AT"] >= row["RECEIVED_AT"]
    else:
        assert row["PROCESSOR_ID"] is None and row["PROCESSED_AT"] is None
    assert mappings(conn) == before
    conn.close()


def test_rules_cover_every_type():
    """규칙표가 분류 스키마의 모든 유형·대상을 덮는다(빠진 유형이 있으면 KeyError가 날 자리)."""
    from typing import get_args

    from app.classify import InquiryType, MasterTarget

    assert set(RULES) | {"마스터 요청"} == set(get_args(InquiryType))
    assert set(MASTER_RULES) == set(get_args(MasterTarget)) | {None}


def test_rules_branch_event_names_rule(db_path):
    answer = dict(CREATE, type="마스터 요청", master_target="ACTION")
    register_issue("C002", "V002", "103", "교반 이름 바꿔 주세요", fake_llm(FakeChatModel("f", [answer])))
    import json

    branch = connect().execute("SELECT OUTPUT FROM CALL_LOG WHERE NAME = '규칙표'").fetchone()[0]
    assert json.loads(branch) == {"rule": "마스터 요청(ACTION)", "status": "이관", "owner_role": "ACTION 담당자"}
