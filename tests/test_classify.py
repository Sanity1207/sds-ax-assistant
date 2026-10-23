from app.classify import FAILED_RESULT, Classification, classify_issue, register_issue
from app.db import connect
from app.fake_llm import FakeChatModel, FakeLLMError, fake_llm

CREATE = {"type": "매핑 생성", "master_target": None, "multiple_requests": False,
          "id_mismatch": False, "reason": "V003에 104를 추가해 달라는 요청"}


def mappings(conn):
    return [tuple(r) for r in conn.execute("SELECT * FROM VESSEL_ACTION ORDER BY 1, 2")]


def test_skeleton_register_creates_issue(db_path):
    conn = connect()
    before = mappings(conn)
    model = FakeChatModel("fake-1", [CREATE])
    out = register_issue("C001", "V003", "104", "V003에 104 추가해 주세요", fake_llm(model), conn)

    rows = conn.execute("SELECT * FROM ISSUE").fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["ISSUE_NO"] == out.issue_no == 1
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


def test_skeleton_unknown_type_needs_check(db_path):
    other = dict(CREATE, type="매핑 삭제", reason="V001에서 103 삭제 요청")
    out = register_issue("C002", "V001", "103", "V001의 103 빼 주세요", fake_llm(FakeChatModel("f", [other])))
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
    assert conn.execute("SELECT COUNT(*) FROM ISSUE").fetchone()[0] == 1
    assert again.issue_no == first.issue_no and again.status == "처리대기"


def test_skeleton_master_target_aliases():
    c = Classification.model_validate(dict(CREATE, type="마스터 요청", master_target="BOTH"))
    assert c.master_target == "둘 다"
    assert Classification.model_validate(dict(CREATE, master_target="null")).master_target is None
