from streamlit.testing.v1 import AppTest

from app.config import ROOT
from app.db import connect

MAIN = str(ROOT / "app" / "main.py")


def open_app():
    at = AppTest.from_file(MAIN, default_timeout=60)
    at.run()
    assert not at.exception
    return at


def register(at, vessel, action, inquiry):
    at.sidebar.radio(key="page").set_value("신규 문의 등록").run()
    at.selectbox(key="reg_vessel").set_value(vessel)
    at.selectbox(key="reg_action").set_value(action)
    at.text_area(key="reg_inquiry").input(inquiry)
    at.button[0].click().run()  # 폼 제출 버튼
    assert not at.exception
    return at


def test_skeleton_register_then_ledger_row(db_path):
    at = register(open_app(), "V003", "104", "V003에 104 추가해 주세요")
    assert at.success[0].value.startswith("접수됨 #1, 처리상태: 처리대기")

    at.sidebar.radio(key="page").set_value("이슈대장").run()
    table = at.dataframe[0].value
    assert list(table["번호"]) == [1]
    assert table.iloc[0]["VESSEL_ID"] == "V003" and table.iloc[0]["처리상태"] == "처리대기"


def test_skeleton_investigate_shows_answer(db_path):
    at = register(open_app(), "V003", "104", "V003에 104 추가해 주세요")
    at.sidebar.radio(key="page").set_value("이슈대장").run()
    at.button(key="investigate_1").click().run()
    assert not at.exception
    assert at.sidebar.radio(key="page").value == "조사 비서"
    answers = [m for m in at.chat_message if m.name == "assistant"]
    texts = " ".join(el.value for m in answers for el in m.children.values() if hasattr(el, "value"))
    assert "V003" in texts and "혼합탱크 1" in texts

    at.chat_input[0].set_value("V001도 찾아 줘").run()
    assert not at.exception
    users = [m for m in at.chat_message if m.name == "user"]
    assert len(users) == 2


def test_skeleton_empty_inquiry_not_saved(db_path):
    register(open_app(), "V003", "104", "   ")
    assert connect().execute("SELECT COUNT(*) FROM ISSUE").fetchone()[0] == 0


def test_skeleton_ledger_is_read_only(db_path):
    at = open_app()
    register(at, "V003", "104", "V003에 104 추가")
    at.sidebar.radio(key="page").set_value("이슈대장").run()
    assert len(at.text_input) == 0 and len(at.text_area) == 0
