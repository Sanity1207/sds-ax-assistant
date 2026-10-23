"""Streamlit 화면. 실행: streamlit run app/main.py

화면은 이슈대장, 신규 문의 등록, 조사 비서 세 개다. 오른쪽 위 드롭다운으로 사용자를 고르고(시작 시 A001, SYSTEM 제외),
권한에 맞는 메뉴·버튼만 보여 준다: 조회 고객 = 이슈대장 / 요청 고객 = + 신규 문의 등록 / 관리자 = + 조사 비서·[이 건 조사].
등록·조사 함수는 화면과 별개로 안에서 권한을 다시 확인한다(app/auth.py).
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
from app.auth import DEFAULT_USER, can, get_user, selectable_users  # noqa: E402
from app.classify import RegistrationRejected, register_issue  # noqa: E402
from app.db import STATUSES, connect  # noqa: E402
from app.llm import LLMError  # noqa: E402

# 화면 → 필요한 행동 권한(app/auth.py PERMISSIONS)
PAGES = {"이슈대장": "read", "신규 문의 등록": "register", "조사 비서": "investigate"}
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


def current_user():
    """지금 고른 사용자 행(USER_ID·USER_NAME·ROLE)."""
    return get_user(st.session_state.current_user)


def ledger_rows(statuses: list[str], mine_only: bool, user_id: str) -> list:
    """이슈대장 조회. 처리상태(여러 개 고르면 그중 하나)와 '내 요청만'(요청자 = 현재 사용자)으로 거른다."""
    where, params = [], []
    if statuses:
        where.append(f"STATUS IN ({', '.join('?' for _ in statuses)})")
        params += statuses
    if mine_only:
        where.append("REQUESTER_ID = ?")
        params.append(user_id)
    sql = f"SELECT {', '.join(LEDGER_COLUMNS)} FROM ISSUE"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return query(sql + " ORDER BY ISSUE_NO DESC", tuple(params))


def show_issue_detail(issue_no: int, admin: bool) -> None:
    """이슈 상세: 모든 칸(이름 포함), 담당자 연락처, 처리 이력(RESOLUTION_LOG). 값은 읽기만 한다."""
    r = query(
        "SELECT I.*, V.VESSEL_NAME, A.ACTION_DESCRIPTION, R.USER_NAME AS REQUESTER_NAME, P.USER_NAME AS PROCESSOR_NAME"
        " FROM ISSUE I JOIN VESSEL_MST V ON V.VESSEL_ID = I.VESSEL_ID JOIN ACTION_MST A ON A.ACTION_SEQ = I.ACTION_SEQ"
        " JOIN USER_MST R ON R.USER_ID = I.REQUESTER_ID LEFT JOIN USER_MST P ON P.USER_ID = I.PROCESSOR_ID"
        " WHERE I.ISSUE_NO = ?", (issue_no,))[0]
    # 담당자 칸이 'VESSEL 담당자, ACTION 담당자'면 두 사람의 연락처를 모두 보여 준다.
    roles = [x.strip() for x in r["OWNER_ROLE"].split(",") if x.strip()]
    contacts = [f"{o['OWNER_ROLE']} {o['OWNER_NAME']}({o['CONTACT']})" for o in query(
        f"SELECT * FROM OWNER_MST WHERE OWNER_ROLE IN ({', '.join('?' for _ in roles)})", tuple(roles))] if roles else []
    lines = [
        f"- 접수일시: {r['RECEIVED_AT']}",
        f"- 요청자: {r['REQUESTER_ID']} {r['REQUESTER_NAME']}",
        f"- VESSEL_ID: {r['VESSEL_ID']} {r['VESSEL_NAME']}",
        f"- ACTION_SEQ: {r['ACTION_SEQ']} {r['ACTION_DESCRIPTION']}",
        f"- 담당자: {r['OWNER_ROLE'] or '(빈칸)'}" + (f" — 연락처: {', '.join(contacts)}" if contacts else ""),
        f"- 처리상태: {r['STATUS']}",
        f"- 처리결과: {r['RESULT'] or '-'}",
        f"- 처리일시: {r['PROCESSED_AT'] or '-'}",
        f"- 처리자: {(r['PROCESSOR_ID'] + ' ' + (r['PROCESSOR_NAME'] or '')) if r['PROCESSOR_ID'] else '-'}",
    ]
    if r["PARENT_ISSUE_NO"]:
        lines.append(f"- 원래 번호: #{r['PARENT_ISSUE_NO']}")
    st.markdown(f"**문의내용**: {r['INQUIRY']}")
    st.markdown("\n".join(lines))
    history = query("SELECT * FROM RESOLUTION_LOG WHERE ISSUE_NO = ? ORDER BY LOG_ID", (issue_no,))
    if history:
        st.markdown("**처리 이력**")
        st.markdown("\n".join(f"- {h['LOGGED_AT']} · {h['KIND']} · {h['USER_ID']} · {h['CONTENT']}" for h in history))
    if admin:
        st.button("이 건 조사", key=f"investigate_{issue_no}", on_click=start_investigation, args=(issue_no,))


def page_ledger() -> None:
    """이슈대장: 모든 권한이 읽는다. 값을 직접 고치는 위젯은 없다(변경은 처리안 + 승인으로만)."""
    st.header("이슈대장")
    user_id = st.session_state.current_user
    left, right = st.columns([3, 1])
    statuses = left.multiselect("처리상태", STATUSES, key="ledger_status", placeholder="전체")
    mine_only = right.checkbox("내 요청만", key="ledger_mine")
    rows = ledger_rows(statuses, mine_only, user_id)
    if not rows:
        st.info("조건에 맞는 이슈가 없습니다.")
        return
    st.caption(f"{len(rows)}건")
    table = [{LEDGER_COLUMNS[k]: r[k] for k in LEDGER_COLUMNS} for r in rows]
    st.dataframe(table, hide_index=True, width="stretch")

    st.subheader("상세")
    admin = can(current_user()["ROLE"], "investigate")
    for r in rows:
        with st.expander(f"#{r['ISSUE_NO']} · {r['STATUS']} · {r['VESSEL_ID']} / {r['ACTION_SEQ']} · {r['INQUIRY'][:40]}"):
            show_issue_detail(r["ISSUE_NO"], admin)


def page_register() -> None:
    st.header("신규 문의 등록")
    vessels = {r["VESSEL_ID"]: r["VESSEL_NAME"] for r in query("SELECT * FROM VESSEL_MST ORDER BY VESSEL_ID")}
    actions = {r["ACTION_SEQ"]: r["ACTION_DESCRIPTION"] for r in query("SELECT * FROM ACTION_MST ORDER BY ACTION_SEQ")}
    user_id = st.session_state.current_user
    with st.form("register", clear_on_submit=True):
        st.text_input("요청자 ID", value=user_id, disabled=True)
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
        try:
            out = register_issue(user_id, vessel_id, action_seq, inquiry.strip())
        except RegistrationRejected as e:
            st.error(f"등록하지 않았습니다. {e}")
            return
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
    thread_id = issue_thread_id(issue_no, st.session_state.current_user)

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
    users = {u["USER_ID"]: u for u in selectable_users()}
    if st.session_state.get("current_user") not in users:
        st.session_state.current_user = DEFAULT_USER
    _, right = st.columns([4, 1])
    right.selectbox("현재 사용자", list(users), key="current_user",
                    format_func=lambda u: f"{users[u]['USER_NAME']} ({users[u]['ROLE']})")
    role = users[st.session_state.current_user]["ROLE"]

    if config.llm_mode() != "bedrock":
        st.sidebar.caption("가짜 LLM 모드(LLM_MODE=fake)")
    pages = [p for p, action in PAGES.items() if can(role, action)]
    if st.session_state.get("page") not in pages:  # 권한이 없는 화면에 있던 채로 사용자를 바꾸면 이슈대장으로
        st.session_state.page = pages[0]
    page = st.sidebar.radio("화면", pages, key="page")
    {"이슈대장": page_ledger, "신규 문의 등록": page_register, "조사 비서": page_assistant}[page]()


main()
