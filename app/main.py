"""Streamlit 화면(묶음 1 최소판). 실행: streamlit run app/main.py

사용자는 A001(관리자)로 고정이다(사용자 선택·권한은 T12). 화면은 이슈대장, 신규 문의 등록, 조사 비서 세 개다.
LLM_MODE=fake(기본)면 데모용 가짜 LLM, bedrock이면 실제 Bedrock을 쓴다.
"""

import asyncio
import sys
from pathlib import Path

# streamlit run은 이 파일의 폴더(app/)만 경로에 넣으므로, 패키지 import를 위해 저장소 루트를 넣는다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

from app import config  # noqa: E402
from app.agent import issue_thread_id, load_messages, run_turn  # noqa: E402
from app.classify import register_issue  # noqa: E402
from app.db import connect  # noqa: E402
from app.llm import LLMError  # noqa: E402

CURRENT_USER = "A001"
PAGES = ("이슈대장", "신규 문의 등록", "조사 비서")
LEDGER_COLUMNS = {
    "ISSUE_NO": "번호", "RECEIVED_AT": "접수일시", "REQUESTER_ID": "요청자 ID", "VESSEL_ID": "VESSEL_ID",
    "ACTION_SEQ": "ACTION_SEQ", "INQUIRY": "문의내용", "OWNER_ROLE": "담당자", "STATUS": "처리상태",
    "RESULT": "처리결과", "PROCESSED_AT": "처리일시", "PROCESSOR_ID": "처리자", "PARENT_ISSUE_NO": "원래 번호",
}


def query(sql: str, params: tuple = ()) -> list:
    conn = connect()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def start_investigation(issue_no: int) -> None:
    """[이 건 조사] 버튼 콜백: 조사 비서 화면으로 옮기고 첫 조사 요청을 예약한다."""
    st.session_state.page = "조사 비서"
    st.session_state.chat_issue = issue_no
    st.session_state.pending_first_turn = True


def page_ledger() -> None:
    st.header("이슈대장")
    rows = query(f"SELECT {', '.join(LEDGER_COLUMNS)} FROM ISSUE ORDER BY ISSUE_NO DESC")
    if not rows:
        st.info("등록된 이슈가 없습니다.")
        return
    table = [{LEDGER_COLUMNS[k]: r[k] for k in LEDGER_COLUMNS} for r in rows]
    st.dataframe(table, hide_index=True, width="stretch")

    st.subheader("이슈별 조사")
    for r in rows:
        left, right = st.columns([6, 1])
        left.write(f"#{r['ISSUE_NO']} · {r['VESSEL_ID']} / {r['ACTION_SEQ']} · {r['STATUS']} · {r['INQUIRY'][:40]}")
        right.button("이 건 조사", key=f"investigate_{r['ISSUE_NO']}",
                     on_click=start_investigation, args=(r["ISSUE_NO"],))


def page_register() -> None:
    st.header("신규 문의 등록")
    vessels = {r["VESSEL_ID"]: r["VESSEL_NAME"] for r in query("SELECT * FROM VESSEL_MST ORDER BY VESSEL_ID")}
    actions = {r["ACTION_SEQ"]: r["ACTION_DESCRIPTION"] for r in query("SELECT * FROM ACTION_MST ORDER BY ACTION_SEQ")}
    with st.form("register", clear_on_submit=True):
        st.text_input("요청자 ID", value=CURRENT_USER, disabled=True)
        vessel_id = st.selectbox("VESSEL_ID", list(vessels), index=None, placeholder="VESSEL 선택",
                                 format_func=lambda v: f"{v} {vessels[v]}", key="reg_vessel")
        action_seq = st.selectbox("ACTION_SEQ", list(actions), index=None, placeholder="ACTION 선택",
                                  format_func=lambda a: f"{a} {actions[a]}", key="reg_action")
        inquiry = st.text_area("문의내용", key="reg_inquiry")
        submitted = st.form_submit_button("등록")
    if not submitted:
        return
    if not vessel_id or not action_seq or not inquiry.strip():
        st.warning("VESSEL_ID, ACTION_SEQ, 문의내용을 모두 입력해 주세요.")
        return
    with st.spinner("자동 분류 중…"):
        out = register_issue(CURRENT_USER, vessel_id, action_seq, inquiry.strip())
    st.success(f"접수됨 #{out.issue_no}, 처리상태: {out.status}, 처리결과: {out.result}")


def show_message(message) -> None:
    if isinstance(message, HumanMessage):
        st.chat_message("user").write(message.content)
    elif isinstance(message, ToolMessage):
        st.chat_message("assistant").caption(f"도구 결과({message.name}): {message.content}")
    elif isinstance(message, AIMessage):
        for call in message.tool_calls:
            st.chat_message("assistant").caption(f"도구 호출: {call['name']}({call['args']})")
        if message.content:
            st.chat_message("assistant").write(message.content)


def send(thread_id: str, text: str) -> None:
    with st.spinner("비서가 조사하는 중…"):
        try:
            asyncio.run(run_turn(thread_id, text))
        except LLMError as e:
            st.error(f"모든 모델이 응답하지 못했습니다. 잠시 뒤 다시 시도해 주세요. ({e})")


def page_assistant() -> None:
    st.header("조사 비서")
    issue_no = st.session_state.get("chat_issue")
    if issue_no is None:
        st.info("이슈대장에서 [이 건 조사]를 눌러 대화를 시작하세요.")
        return
    issue = query("SELECT * FROM ISSUE WHERE ISSUE_NO = ?", (issue_no,))[0]
    st.caption(f"이슈 #{issue_no} · {issue['VESSEL_ID']} / {issue['ACTION_SEQ']} · {issue['STATUS']} · {issue['INQUIRY']}")
    thread_id = issue_thread_id(issue_no, CURRENT_USER)

    if st.session_state.pop("pending_first_turn", False):
        send(thread_id, f"이슈 #{issue_no}을 조사해 주세요. VESSEL_ID {issue['VESSEL_ID']}, "
                        f"ACTION_SEQ {issue['ACTION_SEQ']}, 문의내용: {issue['INQUIRY']}")
    text = st.chat_input("비서에게 질문")
    if text:
        send(thread_id, text)
    for message in asyncio.run(load_messages(thread_id)):
        show_message(message)


def main() -> None:
    st.set_page_config(page_title="매핑 문의 조사 비서", layout="wide")
    user = query("SELECT USER_NAME, ROLE FROM USER_MST WHERE USER_ID = ?", (CURRENT_USER,))[0]
    _, right = st.columns([4, 1])
    right.markdown(f"**{user['USER_NAME']}** ({user['ROLE']})")
    if config.llm_mode() != "bedrock":
        st.sidebar.caption("가짜 LLM 모드(LLM_MODE=fake)")
    page = st.sidebar.radio("화면", PAGES, key="page")
    {"이슈대장": page_ledger, "신규 문의 등록": page_register, "조사 비서": page_assistant}[page]()


main()
