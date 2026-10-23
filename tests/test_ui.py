from streamlit.testing.v1 import AppTest

from app.config import ROOT
from app.db import connect
from app.seed import ISSUES

NEW_NO = len(ISSUES) + 1  # 시드 이슈 다음 번호

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
    assert at.success[0].value.startswith(f"접수됨 #{NEW_NO}, 처리상태: 처리대기")

    at.sidebar.radio(key="page").set_value("이슈대장").run()
    table = at.dataframe[0].value
    assert table.iloc[0]["번호"] == NEW_NO and len(table) == NEW_NO
    assert table.iloc[0]["VESSEL_ID"] == "V003" and table.iloc[0]["처리상태"] == "처리대기"


def test_skeleton_investigate_shows_answer(db_path):
    at = register(open_app(), "V003", "104", "V003에 104 추가해 주세요")
    at.sidebar.radio(key="page").set_value("이슈대장").run()
    at.button(key=f"investigate_{NEW_NO}").click().run()
    assert not at.exception
    assert at.sidebar.radio(key="page").value == "조사 비서"
    # 열린 이슈의 새 thread: 비서가 LLM 없이 첫 질문을 한다.
    assert chat_texts(at, "assistant") == [f"이슈 #{NEW_NO} 건에 대해 조사를 진행하시겠습니까? (y/n)"]

    at.chat_input[0].set_value(" Y ").run()  # 데모 가짜 LLM은 get_issue → propose_resolution 순서로 부른다
    assert not at.exception
    texts = " ".join(chat_texts(at, "assistant"))
    assert "V003" in texts and "혼합탱크 1" in texts
    assert [s.value for s in at.subheader] == ["승인 카드"]

    # 승인 카드가 떠 있는 동안 대화창 입력은 승인 응답으로 판정한다(y/n/e가 아니면 다시 묻기).
    at.chat_input[0].set_value("V001도 찾아 줘").run()
    assert not at.exception
    assert at.warning[0].value.startswith("y, n, e 중 하나로만 답해 주세요.")
    assert len(chat_texts(at, "user")) == 1


def chat_texts(at, name):
    """대화창에서 name(user·assistant) 쪽 말의 글(도구 호출·결과 caption 포함)."""
    return [el.value for m in at.chat_message if m.name == name for el in m.children.values() if hasattr(el, "value")]


def test_skeleton_empty_inquiry_not_saved(db_path):
    register(open_app(), "V003", "104", "   ")
    assert connect().execute("SELECT COUNT(*) FROM ISSUE").fetchone()[0] == len(ISSUES)


def test_skeleton_ledger_is_read_only(db_path):
    at = open_app()
    register(at, "V003", "104", "V003에 104 추가")
    at.sidebar.radio(key="page").set_value("이슈대장").run()
    assert len(at.text_input) == 0 and len(at.text_area) == 0


# ---- T12: 사용자 선택·권한 ----

import asyncio  # noqa: E402

import pytest  # noqa: E402

from app.agent import issue_thread_id, run_turn  # noqa: E402
from app.auth import PermissionDenied  # noqa: E402
from app.classify import RegistrationRejected, register_issue  # noqa: E402


def as_user(at, user_id):
    at.selectbox(key="current_user").set_value(user_id).run()
    assert not at.exception
    return at


def investigate_buttons(at):
    return [b for b in at.button if str(b.key or "").startswith("investigate_")]


def test_role_default_user_and_dropdown(db_path):
    at = open_app()
    box = at.selectbox(key="current_user")
    assert box.value == "A001"
    assert box.options == ["윤도현 (관리자)", "한지민 (관리자)", "김민수 (요청 고객)", "이서연 (요청 고객)",
                           "박지훈 (요청 고객)", "최유진 (요청 고객)", "정하늘 (조회 고객)"]
    assert not any("시스템" in o for o in box.options)  # SYSTEM은 고를 수 없다
    assert at.sidebar.radio(key="page").options == ["이슈대장", "신규 문의 등록", "조사 비서"]
    assert investigate_buttons(at)


def test_role_viewer_reads_only(db_path):
    at = as_user(open_app(), "C005")
    assert at.sidebar.radio(key="page").options == ["이슈대장"]
    assert len(at.dataframe) == 1  # 이슈대장은 읽을 수 있다
    assert not investigate_buttons(at)
    assert len(at.text_area) == 0 and not [b for b in at.button if "등록" in (b.label or "")]


def test_role_requester_registers_but_no_investigation(db_path):
    at = as_user(open_app(), "C001")
    assert at.sidebar.radio(key="page").options == ["이슈대장", "신규 문의 등록"]
    assert not investigate_buttons(at)
    register(at, "V003", "104", "V003에 104 추가해 주세요")
    assert at.success[0].value.startswith(f"접수됨 #{NEW_NO}")
    assert connect().execute("SELECT REQUESTER_ID FROM ISSUE WHERE ISSUE_NO = ?", (NEW_NO,)).fetchone()[0] == "C001"


def test_role_switch_away_from_forbidden_page(db_path):
    at = open_app()
    at.sidebar.radio(key="page").set_value("조사 비서").run()
    as_user(at, "C005")
    assert at.sidebar.radio(key="page").value == "이슈대장"
    as_user(at, "A002")
    assert at.sidebar.radio(key="page").options == ["이슈대장", "신규 문의 등록", "조사 비서"]


@pytest.mark.parametrize("user_id, reason", [("C005", "문의 등록 권한이 없습니다"), ("SYSTEM", "문의 등록 권한이 없습니다"),
                                             ("X999", "USER_MST에 없습니다")])
def test_role_register_function_rejects(db_path, user_id, reason):
    with pytest.raises(RegistrationRejected, match=reason):
        register_issue(user_id, "V003", "104", "V003에 104 추가")
    conn = connect()
    assert conn.execute("SELECT COUNT(*) FROM ISSUE").fetchone()[0] == len(ISSUES)  # 저장하지 않음
    run = conn.execute("SELECT * FROM PROCESS_LOG").fetchone()
    assert (run["RESULT"], run["ISSUE_NO"], run["USER_ID"]) == ("등록 거부", None, "SYSTEM")
    branch = conn.execute("SELECT OUTPUT FROM CALL_LOG WHERE EVENT_KIND = 'branch'").fetchone()[0]
    assert branch.startswith("거부:")
    assert conn.execute("SELECT COUNT(*) FROM CALL_LOG WHERE EVENT_KIND = 'llm'").fetchone()[0] == 0  # LLM 안 부름


@pytest.mark.parametrize("user_id", ["C001", "C005", "X999"])
def test_role_assistant_function_rejects_non_admin(db_path, user_id):
    with pytest.raises(PermissionDenied):
        asyncio.run(run_turn(issue_thread_id(2, user_id), "조사해 줘"))
    assert connect().execute("SELECT COUNT(*) FROM PROCESS_LOG").fetchone()[0] == 0


# ---- T13: 이슈대장 화면 ----

EDITABLE = ("text_input", "text_area", "number_input", "date_input", "time_input", "color_picker", "slider",
            "select_slider", "toggle", "radio")


def ledger(at, user_id="A001", statuses=None, mine=None):
    if user_id != "A001":
        as_user(at, user_id)
    at.sidebar.radio(key="page").set_value("이슈대장").run()
    if statuses is not None:
        at.multiselect(key="ledger_status").set_value(statuses).run()
    if mine is not None:
        at.checkbox(key="ledger_mine").set_value(mine).run()
    assert not at.exception
    return at


def table_of(at):
    return at.dataframe[0].value


def test_ledger_mine_only_filter(db_path):
    at = ledger(open_app(), "C001", mine=True)
    table = table_of(at)
    assert set(table["요청자 ID"]) == {"C001"}
    assert sorted(table["번호"]) == [1, 2, 6]
    at.checkbox(key="ledger_mine").uncheck().run()
    assert len(table_of(at)) == len(ISSUES)


def test_ledger_mine_only_follows_current_user(db_path):
    at = ledger(open_app(), "C004", mine=True)
    assert set(table_of(at)["요청자 ID"]) == {"C004"}
    as_user(at, "C002")
    assert set(table_of(at)["요청자 ID"]) == {"C002"}
    as_user(at, "C005")  # 요청한 건이 없는 조회 고객
    assert len(at.dataframe) == 0 and at.info[0].value == "조건에 맞는 이슈가 없습니다."


def test_ledger_status_filter(db_path):
    at = ledger(open_app(), statuses=["반려"])
    assert list(table_of(at)["번호"]) == [9, 8]
    at.multiselect(key="ledger_status").set_value(["처리대기", "이관"]).run()
    assert sorted(table_of(at)["번호"]) == [2, 3, 4, 7]
    at.checkbox(key="ledger_mine").check().run()  # A001이 요청한 건은 없다
    assert len(at.dataframe) == 0


def test_ledger_status_and_mine_combined(db_path):
    at = ledger(open_app(), "C001", statuses=["확인필요"], mine=True)
    assert list(table_of(at)["번호"]) == [6]


def test_ledger_detail(db_path):
    at = ledger(open_app(), "C005")
    labels = [e.label for e in at.expander]
    assert len(labels) == len(ISSUES) and labels[-1].startswith("#1 · 완료 · V002 / 103")
    texts = lambda exp: " ".join(m.value for m in exp.markdown)  # noqa: E731
    done = texts(at.expander[-1])
    assert "요청자: C001 김민수" in done and "VESSEL_ID: V002 반응기 B" in done and "ACTION_SEQ: 103 교반" in done
    assert "처리자: A001 윤도현" in done and "처리 이력" in done and "실행 성공" in done
    moved = texts(next(e for e in at.expander if e.label.startswith("#7 ")))
    assert "담당자: ACTION 담당자 — 연락처: ACTION 담당자 오세훈(내선 1003)" in moved


def test_ledger_has_no_editing_widgets(db_path):
    for user_id in ("A001", "C001", "C005"):
        at = ledger(open_app(), user_id)
        for kind in EDITABLE:
            if kind == "radio":  # 사이드바의 화면 선택 radio만 있다
                assert [r.key for r in at.radio] == ["page"]
                continue
            assert len(at.get(kind)) == 0, (user_id, kind)
        # 표는 읽기 전용(st.dataframe). st.data_editor면 editing_mode가 READ_ONLY가 아니다.
        assert all(df.proto.editing_mode == df.proto.EditingMode.READ_ONLY for df in at.dataframe)
        others = [b.key for b in at.button if b.label != "이 건 조사"]
        # 그 밖의 버튼은 관리자에게만 보이는 [다시 분류](자동 분류 실패 시드 이슈 #6) 하나뿐이다.
        assert others == (["reclassify_6"] if user_id == "A001" else [])


def test_ledger_investigate_only_for_admin(db_path):
    at = ledger(open_app())
    assert sorted(b.key for b in investigate_buttons(at)) == sorted(f"investigate_{n}" for n in range(1, 10))
    for user_id in ("C001", "C005"):
        assert not investigate_buttons(ledger(open_app(), user_id))


def test_ledger_investigate_opens_issue_thread(db_path):
    at = ledger(open_app(), "A002")
    at.button(key="investigate_3").click().run()
    assert not at.exception
    assert at.sidebar.radio(key="page").value == "조사 비서"
    assert at.caption[0].value.startswith("이슈 #3 · V003 / 103")
    assert at.caption[1].value == "열린 이슈 대화 · thread issue-3-A002"
    at.chat_input[0].set_value("y").run()
    run = connect().execute("SELECT THREAD_ID, USER_ID FROM PROCESS_LOG").fetchone()
    assert tuple(run) == ("issue-3-A002", "A002")


# ---- T16: [다시 분류] ----

from app.classify import FAILED_RESULT  # noqa: E402


def test_reclassify_button_only_admin_and_failed(db_path):
    at = ledger(open_app())
    assert [b.key for b in at.button if (b.key or "").startswith("reclassify_")] == ["reclassify_6"]
    for user_id in ("C001", "C005"):
        assert not [b for b in ledger(open_app(), user_id).button if (b.key or "").startswith("reclassify_")]


def test_reclassify_button_updates_same_issue(db_path):
    at = ledger(open_app())
    at.button(key="reclassify_6").click().run()  # 데모 가짜 LLM은 늘 '매핑 생성'으로 분류한다
    assert not at.exception
    assert at.success[0].value.startswith("다시 분류됨 #6, 처리상태: 처리대기")
    conn = connect()
    assert conn.execute("SELECT STATUS FROM ISSUE WHERE ISSUE_NO = 6").fetchone()[0] == "처리대기"
    assert conn.execute("SELECT COUNT(*) FROM ISSUE").fetchone()[0] == len(ISSUES)
    run = conn.execute("SELECT RUN_KIND, ISSUE_NO, USER_ID FROM PROCESS_LOG").fetchone()
    assert tuple(run) == ("다시 분류", 6, "A001")
    assert not [b for b in at.button if (b.key or "").startswith("reclassify_")]  # 이제 대상이 아니다


def test_reclassify_register_failure_shows_info_without_banner(db_path, monkeypatch):
    from app import fake_llm as fake

    monkeypatch.setattr(fake.DemoChatModel, "structured", lambda self, m, s: (None, "?", "형식 오류"))
    at = register(open_app(), "V003", "104", "V003에 104 추가")
    assert at.success[0].value == f"접수됨 #{NEW_NO}, 처리상태: 확인필요, 처리결과: {FAILED_RESULT}"
    assert "다시 분류" in at.info[0].value and len(at.error) == 0  # 배너 없음


# ---- T18: 대화 모드·thread·첫 질문 ----

def test_mode_menu_opens_free_chat_and_new_chat(db_path):
    import time

    at = ledger(open_app())
    at.button(key="investigate_2").click().run()
    at.sidebar.radio(key="page").set_value("이슈대장").run()
    at.sidebar.radio(key="page").set_value("조사 비서").run()  # 메뉴로 들어오면 선택된 이슈 없음
    assert not at.exception
    first = at.caption[0].value
    assert first.startswith("이슈 없는 대화 · thread free-A001-")
    assert chat_texts(at, "assistant") == []  # 이슈 없는 대화는 첫 질문 없음
    at.chat_input[0].set_value("혼합탱크 ID 알려 줘").run()
    assert "V003" in " ".join(chat_texts(at, "assistant"))
    time.sleep(1.1)  # thread ID는 초 단위 시작 시각
    at.button(key="new_chat").click().run()
    assert at.caption[0].value != first and chat_texts(at, "user") == []


def test_mode_closed_issue_has_no_first_question(db_path):
    at = ledger(open_app())
    at.button(key="investigate_1").click().run()
    assert at.caption[1].value == "닫힌 이슈 대화(조회 전용) · thread issue-1-A001"
    assert chat_texts(at, "assistant") == []


# ---- T23: 실패 배너·[다시 시도] ----

def test_failure_banner_and_retry(db_path, monkeypatch):
    from app import fake_llm as fake

    real_chat = fake.DemoChatModel.chat

    def broken(self, messages, tools=None):
        raise fake.FakeLLMError("모델 장애")

    monkeypatch.setattr(fake.DemoChatModel, "chat", broken)
    at = open_app()
    at.sidebar.radio(key="page").set_value("조사 비서").run()
    at.chat_input[0].set_value("혼합탱크 ID 알려 줘").run()
    assert not at.exception
    assert "대화가 멈췄습니다" in at.error[0].value and "모델 장애" in at.error[0].value
    assert at.button(key="retry_turn")

    monkeypatch.setattr(fake.DemoChatModel, "chat", real_chat)
    at.button(key="retry_turn").click().run()
    assert not at.exception
    assert len(at.error) == 0
    assert "V003" in " ".join(chat_texts(at, "assistant"))
    assert chat_texts(at, "user") == ["혼합탱크 ID 알려 줘"]  # 관리자 재입력 없음


# ---- T29: 승인 카드·편집 폼 화면 ----

def to_card(at, issue_no):
    """이슈대장 → [이 건 조사] → 첫 질문 y → 데모 가짜 LLM이 처리안을 만들어 승인 카드가 뜬다."""
    at.sidebar.radio(key="page").set_value("이슈대장").run()
    at.button(key=f"investigate_{issue_no}").click().run()
    at.chat_input[0].set_value("y").run()
    assert not at.exception
    assert [s.value for s in at.subheader] == ["승인 카드"]
    return at


def issue_row(issue_no):
    return connect().execute("SELECT * FROM ISSUE WHERE ISSUE_NO = ?", (issue_no,)).fetchone()


def test_approval_card_y_completes_issue(db_path):
    at = to_card(open_app(), 2)
    card = " ".join(m.value for m in at.markdown)
    assert "반영 전: V003→104 없음" in card and "반영 후: V003→104 냉각 · 미수행 N(새 행)" in card
    assert "문의 원문: 혼합탱크 1(V003)에 냉각(104) 공정을 추가해 주세요." in card
    assert at.info[0].value.startswith("위험 등급: write · 도구: propose_resolution")
    at.button(key="approve_y").click().run()
    assert not at.exception
    assert not at.subheader  # 카드가 사라진다
    assert "반영 완료: [매핑 변경] V003→104 생성, #2 완료" in chat_texts(at, "assistant")
    assert issue_row(2)["STATUS"] == "완료"
    assert at.caption[0].value.startswith("이슈 #2 · V003 / 104 · 완료")
    at.sidebar.radio(key="page").set_value("이슈대장").run()
    table = at.dataframe[0].value
    assert table[table["번호"] == 2]["처리상태"].iloc[0] == "완료"


def test_approval_card_n_cancels(db_path):
    at = to_card(open_app(), 2)
    at.button(key="approve_n").click().run()
    assert "처리안 취소됨" in chat_texts(at, "assistant") and issue_row(2)["STATUS"] == "처리대기"
    assert not at.subheader


def test_approval_destructive_double_confirm(db_path):
    at = to_card(open_app(), 4)
    assert at.error[0].value.startswith("위험 등급: destructive")
    at.button(key="approve_y").click().run()
    assert issue_row(4)["STATUS"] == "처리대기"  # y만으로는 실행하지 않는다
    at.text_input(key="confirm_no").input("5")
    at.button[[b.label for b in at.button].index("이중 확인")].click().run()
    assert not at.exception
    assert at.warning[0].value.startswith("이슈 번호가 맞지 않아 실행하지 않았습니다.")
    assert issue_row(4)["STATUS"] == "처리대기"
    at.text_input(key="confirm_no").input("4")
    at.button[[b.label for b in at.button].index("이중 확인")].click().run()
    assert issue_row(4)["STATUS"] == "완료"
    assert "반영 완료: [매핑 변경] V004→105 삭제, #4 완료" in chat_texts(at, "assistant")


def test_approval_edit_form(db_path):
    at = to_card(open_app(), 3)  # 데모 처리안: V003 103 → 105 교체
    at.button(key="approve_e").click().run()
    assert "처리안을 직접 편집해 주세요." in chat_texts(at, "assistant")
    assert at.selectbox(key="edit_kind").value == "update" and at.selectbox(key="edit_new_action").value == "105"
    # 위반 편집: 매핑을 바꾸면서 반려 → 거부, 편집 폼 그대로
    at.selectbox(key="edit_status").set_value("반려")
    at.button[[b.label for b in at.button].index("편집 완료")].click().run()
    assert at.warning[0].value.startswith("편집한 처리안이 검사에서 거부됐습니다")
    assert at.selectbox(key="edit_kind")
    # 정상 편집: 냉각(104)으로 교체 → 새 카드 → 승인
    at.selectbox(key="edit_status").set_value("완료")
    at.selectbox(key="edit_new_action").set_value("104")
    at.text_area(key="edit_result").input("냉각으로 교체")
    at.button[[b.label for b in at.button].index("편집 완료")].click().run()
    assert "반영 후: V003→103 없음; V003→104 냉각 · 미수행 N(새 행)" in " ".join(m.value for m in at.markdown)
    at.button(key="approve_y").click().run()
    assert issue_row(3)["STATUS"] == "완료"
    assert issue_row(3)["RESULT"] == "[매핑 변경] V003→103 삭제, V003→104 생성 냉각으로 교체"


def test_approval_typed_answers_work(db_path):
    at = to_card(open_app(), 2)
    at.chat_input[0].set_value(" Y ").run()
    assert issue_row(2)["STATUS"] == "완료"


# ---- 실제 Bedrock 형식: 설명 글 + 도구 호출이 블록 목록으로 오는 AIMessage (시연 중 발견, issue-10-A001) ----

def bedrock_style_tool_call(text, name, args, call_id):
    """ChatBedrockConverse가 도구 호출과 설명 글을 함께 돌려줄 때의 모양: content가 블록 목록."""
    from langchain_core.messages import AIMessage

    return AIMessage(content=[{"type": "text", "text": text},
                              {"type": "tool_use", "id": call_id, "name": name, "input": args}],
                     tool_calls=[{"name": name, "args": args, "id": call_id}])


def test_block_list_content_renders(db_path, monkeypatch):
    from langchain_core.messages import AIMessage as AI, ToolMessage as TM

    from app import fake_llm as fake

    def chat(self, messages, tools=None):
        if isinstance(messages[-1], TM):
            return AI(content=[{"type": "text", "text": "{고객:C001} 고객님, V003 혼합탱크 1입니다."}])
        return bedrock_style_tool_call("먼저 혼합탱크를 찾아볼게요.", "find_vessel", {"query": "혼합"}, "tooluse_1")

    monkeypatch.setattr(fake.DemoChatModel, "chat", chat)
    at = open_app()
    at.sidebar.radio(key="page").set_value("조사 비서").run()
    at.chat_input[0].set_value("혼합탱크 ID 알려 줘").run()
    assert not at.exception
    texts = chat_texts(at, "assistant")
    assert "먼저 혼합탱크를 찾아볼게요." in texts  # 글 블록만 보이고
    assert "도구 호출: find_vessel({'query': '혼합'})" in texts  # 도구 호출은 따로 표시
    assert "김민수 고객님, V003 혼합탱크 1입니다." in texts  # 블록 목록이어도 고객 이름 채우기
    assert not any("tool_use" in t for t in texts)

    # LLM 래퍼가 입구에서 글로 바꾸므로 체크포인터에는 문자열로 저장된다.
    from app.agent import load_messages

    thread = at.caption[0].value.split("thread ")[1]
    stored = [m for m in asyncio.run(load_messages(thread)) if m.type == "ai"]
    assert all(isinstance(m.content, str) for m in stored)
    assert stored[0].content == "먼저 혼합탱크를 찾아볼게요." and stored[0].tool_calls[0]["id"] == "tooluse_1"


def test_block_list_content_legacy_thread_renders(db_path):
    """변경 전에 블록 목록 그대로 저장된 thread(시연의 issue-10-A001)를 열어도 화면이 그려진다."""
    from langchain_core.messages import AIMessage, HumanMessage

    from app.agent import build_graph, open_checkpointer

    async def save():
        async with open_checkpointer() as saver:
            graph = build_graph(None, [], saver)
            await graph.aupdate_state({"configurable": {"thread_id": "issue-1-A001"}}, {"messages": [
                HumanMessage("완료 건 요약해 줘"),
                AIMessage(content=[{"type": "text", "text": "옛 형식 답변입니다."},
                                   {"type": "tool_use", "id": "t1", "name": "get_issue", "input": {"issue_no": 1}}])]},
                as_node="finalize")

    asyncio.run(save())
    at = ledger(open_app())
    at.button(key="investigate_1").click().run()
    assert not at.exception
    assert "옛 형식 답변입니다." in chat_texts(at, "assistant")
