"""④ 반려 건 재요청(묶음 7). 모두 가짜 LLM."""

import pytest
from streamlit.testing.v1 import AppTest

from app.classify import RegistrationRejected, register_issue
from app.config import ROOT
from app.db import connect
from app.fake_llm import FakeChatModel, fake_llm
from app.seed import ISSUES

NEW_NO = len(ISSUES) + 1
CREATE = {"type": "매핑 생성", "master_target": None, "multiple_requests": False, "id_mismatch": False,
          "reason": "V002에 102를 추가해 달라는 요청"}


def issue(n):
    return connect().execute("SELECT * FROM ISSUE WHERE ISSUE_NO = ?", (n,)).fetchone()


# ---- T31: [수정해서 재요청] ----

def test_reapply_registers_child_issue(db_path):
    original = tuple(issue(8))
    out = register_issue("C003", "V002", "102", "V002 반응기 B에 가열(102)을 추가해 주세요.",
                         fake_llm(FakeChatModel("f", [CREATE])), parent_issue_no=8)
    child = issue(out.issue_no)
    assert out.issue_no == NEW_NO and child["PARENT_ISSUE_NO"] == 8
    assert (child["STATUS"], child["OWNER_ROLE"], child["REQUESTER_ID"]) == ("처리대기", "매핑 담당자", "C003")
    assert tuple(issue(8)) == original  # 원래 건은 반려 그대로
    run = connect().execute("SELECT * FROM PROCESS_LOG").fetchone()
    assert (run["RUN_KIND"], run["ISSUE_NO"], run["RESULT"]) == ("문의 등록", NEW_NO, "분류 완료")


@pytest.mark.parametrize("user, parent, part", [
    ("C001", 8, "원래 요청자 본인만"),  # #8은 C003의 건
    ("A001", 8, "원래 요청자 본인만"),
    ("C003", 2, "반려 건만"),  # #2는 처리대기
    ("C001", 1, "반려 건만"),  # #1은 완료
    ("C003", 999, "이슈가 없습니다"),
    ("C003", "8", "올바르지 않습니다"),
])
def test_reapply_rejected(db_path, user, parent, part):
    model = FakeChatModel("f", [CREATE])
    with pytest.raises(RegistrationRejected) as info:
        register_issue(user, "V002", "102", "재요청", fake_llm(model), parent_issue_no=parent)
    assert any(part in r for r in info.value.reasons)
    assert model.calls == [] and connect().execute("SELECT COUNT(*) FROM ISSUE").fetchone()[0] == len(ISSUES)


def open_as(user_id):
    at = AppTest.from_file(str(ROOT / "app" / "main.py"), default_timeout=60)
    at.run()
    at.selectbox(key="current_user").set_value(user_id).run()
    assert not at.exception
    return at


def reapply_buttons(at):
    return sorted(b.key for b in at.button if (b.key or "").startswith("reapply_"))


def test_reapply_button_only_for_original_requester(db_path):
    assert reapply_buttons(open_as("C003")) == ["reapply_8"]
    assert reapply_buttons(open_as("C004")) == ["reapply_9"]
    for user in ("C001", "C002", "C005", "A001", "A002"):
        assert reapply_buttons(open_as(user)) == [], user


def test_reapply_prefilled_form_registers_new_issue(db_path):
    at = open_as("C003")
    at.button(key="reapply_8").click().run()
    assert at.sidebar.radio(key="page").value == "신규 문의 등록"
    assert at.info[0].value.startswith("반려된 #8을(를) 수정해서 재요청합니다.")
    # 값이 채워진 등록 폼
    assert at.selectbox(key="reg_vessel").value == "V001" and at.selectbox(key="reg_action").value == "102"
    assert at.text_area(key="reg_inquiry").value == "V002 반응기 B에 가열(102)을 추가해 주세요."
    at.selectbox(key="reg_vessel").set_value("V002")  # 입력칸을 문의내용에 맞게 고친다
    at.button[[b.label for b in at.button].index("등록")].click().run()
    assert not at.exception
    assert at.success[0].value.startswith(f"접수됨 #{NEW_NO}") and at.success[0].value.endswith("(원래 번호 #8)")
    assert issue(NEW_NO)["PARENT_ISSUE_NO"] == 8 and issue(NEW_NO)["VESSEL_ID"] == "V002"
    assert issue(8)["STATUS"] == "반려"
    # 이슈대장 상세에 원래 번호가 보인다.
    at.sidebar.radio(key="page").set_value("이슈대장").run()
    child = next(e for e in at.expander if e.label.startswith(f"#{NEW_NO} "))
    assert "원래 번호: #8" in " ".join(m.value for m in child.markdown)


def test_reapply_menu_resets_form(db_path):
    at = open_as("C003")
    at.button(key="reapply_8").click().run()
    at.sidebar.radio(key="page").set_value("이슈대장").run()
    at.sidebar.radio(key="page").set_value("신규 문의 등록").run()
    assert not at.info  # 메뉴로 다시 들어오면 일반 등록
