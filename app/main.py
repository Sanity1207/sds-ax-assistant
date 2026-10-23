"""Streamlit 화면. 실행: streamlit run app/main.py

화면은 이슈대장, 신규 문의 등록, 조사 비서 세 개다. 오른쪽 위 드롭다운으로 사용자를 고르고(시작 시 A001, SYSTEM 제외),
권한에 맞는 메뉴·버튼만 보여 준다: 조회 고객 = 이슈대장 / 요청 고객 = + 신규 문의 등록 / 관리자 = + 조사 비서·[이 건 조사].
등록·조사 함수는 화면과 별개로 안에서 권한을 다시 확인한다(app/auth.py).
LLM_MODE=fake(기본)면 데모용 가짜 LLM, bedrock이면 실제 Bedrock을 쓴다.
"""

import asyncio
import sys
import threading
from pathlib import Path

# streamlit run은 이 파일의 폴더(app/)만 경로에 넣으므로, 패키지 import를 위해 저장소 루트를 넣는다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

from app import config  # noqa: E402
from app.agent import (CARD_QUESTION, CONFIRM_QUESTION, MODE_NAMES, REQUEST_TAG, McpUnavailable,  # noqa: E402
                       answer_approval, free_thread_id, issue_mode, issue_thread_id, load_messages, needs_retry,
                       open_thread, pending_approval, retry_turn, run_turn)
from app.resolution import CLOSED_TARGETS  # noqa: E402
from app.auth import DEFAULT_USER, PermissionDenied, can, get_user, selectable_users  # noqa: E402
from app.classify import (FAILED_RESULT, ReclassifyNotAllowed, RegistrationRejected, can_reclassify,  # noqa: E402
                          classify_issue, register_issue)
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


def leave_issue_chat() -> None:
    """메뉴에서 조사 비서를 고르거나 [이슈 없는 대화로]를 누르면 선택된 이슈 없이 대화한다."""
    st.session_state.chat_issue = None


def new_free_chat() -> None:
    """[새 대화]: 이슈 없는 대화 thread를 새로 시작한다(free-<관리자>-<시작 시각>)."""
    st.session_state.setdefault("free_threads", {})[st.session_state.current_user] = free_thread_id(
        st.session_state.current_user)


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


def reclassify(issue_no: int) -> None:
    """[다시 분류] 버튼: 같은 이슈를 다시 분류하고 결과를 이슈대장 위에 알린다(분류 중 로딩 표시)."""
    with st.spinner(f"#{issue_no} 다시 분류 중…"):
        try:
            out = classify_issue(issue_no, user_id=st.session_state.current_user)
        except (PermissionDenied, ReclassifyNotAllowed) as e:
            st.session_state.flash = ("error", f"다시 분류하지 않았습니다. {e}")
        else:
            st.session_state.flash = ("success", f"다시 분류됨 #{out.issue_no}, 처리상태: {out.status}, 처리결과: {out.result}")
    st.rerun()


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
        # [다시 분류]는 관리자에게만, 자동 분류 실패로 확인필요인 건에만 보인다.
        if can_reclassify(r) and st.button("다시 분류", key=f"reclassify_{issue_no}"):
            reclassify(issue_no)


def page_ledger() -> None:
    """이슈대장: 모든 권한이 읽는다. 값을 직접 고치는 위젯은 없다(변경은 처리안 + 승인으로만)."""
    st.header("이슈대장")
    if flash := st.session_state.pop("flash", None):
        getattr(st, flash[0])(flash[1])
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
            st.error("등록하지 않았습니다.\n\n" + "\n".join(f"- {r}" for r in e.reasons))
            return
    st.success(f"접수됨 #{out.issue_no}, 처리상태: {out.status}, 처리결과: {out.result}")
    if out.result == FAILED_RESULT:
        # 분류 실패는 배너(오류)가 아니라 안내로만 알린다. 문의는 저장됐고 관리자가 [다시 분류]할 수 있다.
        st.info("자동 분류를 하지 못해 확인필요로 접수했습니다. 매핑 담당자가 확인하거나 이슈대장에서 [다시 분류]할 수 있습니다.")


def show_message(message) -> None:
    if getattr(message, "name", None) == REQUEST_TAG:  # 첫 질문 y를 Python이 바꾼 조사 요청(관리자가 친 말은 y)
        return
    if isinstance(message, HumanMessage):
        st.chat_message("user").write(message.content)
    elif isinstance(message, ToolMessage):
        st.chat_message("assistant").caption(f"도구 결과({message.name}): {message.content}")
    elif isinstance(message, AIMessage):
        for call in message.tool_calls:
            st.chat_message("assistant").caption(f"도구 호출: {call['name']}({call['args']})")
        if message.content:
            st.chat_message("assistant").write(message.content)


def work(call):
    """비서 작업 1회(대화 입력·다시 시도). 진행 상황(예: '도구 호출 중: get_vessel_mappings(V003) (3/8)')을 보여 준다.

    모든 모델 실패·MCP 연결 실패는 여기서 삼키고, 화면이 thread 상태를 보고 오류 배너와 [다시 시도]를 띄운다.
    """
    with st.status("비서가 조사하는 중…", expanded=True) as status:
        line = st.empty()
        ctx = get_script_run_ctx()

        def progress(message: str) -> None:
            # LangGraph는 동기 노드를 다른 스레드에서 돌린다. 화면을 고치려면 그 스레드에 Streamlit 실행 맥락을 붙인다.
            add_script_run_ctx(threading.current_thread(), ctx)
            line.write(message)
            status.update(label=message)

        try:
            result = asyncio.run(call(progress))
        except (LLMError, McpUnavailable) as e:
            st.session_state.last_failure = str(e)
            status.update(label="응답 실패", state="error", expanded=False)
            return None
        status.update(label="완료", state="complete", expanded=False)
    if result is not None and result.note:
        st.session_state.approval_note = result.note  # 다시 묻기 안내는 승인 카드 위에 보여 준다
    return result


def send(thread_id: str, text: str) -> None:
    work(lambda progress: run_turn(thread_id, text, progress=progress))
    st.rerun()  # 이슈 상태·승인 카드를 새 값으로 다시 그린다


def respond(thread_id: str, text: str | None = None, edited: dict | None = None) -> None:
    """승인 카드 버튼·이중 확인·편집 폼 → 승인 응답 1회(Python 판정)."""
    work(lambda progress: answer_approval(thread_id, text, edited=edited))
    st.rerun()


def show_approval(thread_id: str, pending: dict) -> None:
    """승인 카드: 위험 등급·도구·대상 전후 값·EXECUTED_YN·근거(이슈 번호·문의 원문). 값은 Python이 DB에서 읽은 원본."""
    card, proposal, stage = pending["card"], pending["proposal"], pending["stage"]
    with st.container(border=True):
        st.subheader("승인 카드")
        if note := st.session_state.pop("approval_note", None):
            st.warning(note)
        destructive = card["risk"] == "destructive"
        (st.error if destructive else st.info)(
            f"위험 등급: {card['risk']} · 도구: {card['tool']}"
            + (" · 수행된 매핑을 바꿉니다. 승인 뒤 이슈 번호를 한 번 더 입력해야 실행됩니다." if destructive else ""))
        st.markdown("\n".join([
            f"- 근거: 이슈 #{card['issue_no']} ({card['issue_status']} → {card['new_status']}) · 요청자 {card['requester_id']}",
            f"- 입력칸: {card['input_fields']}",
            f"- 문의 원문: {card['inquiry']}",
            f"- 대상: {card['change']}",
            f"- 반영 전: {'; '.join(card['before'])}",
            f"- 반영 후: {'; '.join(card['after'])}",
            f"- 현재 수행 상태(EXECUTED_YN): {card['executed_yn']}",
            f"- 처리결과(반영될 문장): {card['result']}",
        ]))
        if stage == "card":
            st.caption(CARD_QUESTION + " (대화창에 y/n/e를 입력해도 됩니다)")
            c1, c2, c3 = st.columns(3)
            if c1.button("승인", key="approve_y", type="primary"):
                respond(thread_id, "y")
            if c2.button("취소", key="approve_n"):
                respond(thread_id, "n")
            if c3.button("직접 편집", key="approve_e"):
                respond(thread_id, "e")
        elif stage == "confirm":
            st.warning(CONFIRM_QUESTION.format(n=proposal["issue_no"]))
            with st.form("confirm_form", clear_on_submit=True):
                value = st.text_input("이슈 번호", key="confirm_no")
                confirmed = st.form_submit_button("이중 확인")
            if confirmed:
                respond(thread_id, value)
            if st.button("취소", key="confirm_cancel"):
                respond(thread_id, "n")
        else:
            show_edit_form(thread_id, proposal)


EDIT_KINDS = {"none": "매핑 변경 없음", "create": "생성", "update": "수정(ACTION 교체)", "delete": "삭제"}


def show_edit_form(thread_id: str, proposal: dict) -> None:
    """처리안 편집 폼: 변경 종류·VESSEL·ACTION·바꿀 ACTION은 마스터 목록에서, 새 상태는 허용 값에서, 설명은 자유 텍스트."""
    issue = query("SELECT * FROM ISSUE WHERE ISSUE_NO = ?", (proposal["issue_no"],))[0]
    vessels = {r["VESSEL_ID"]: r["VESSEL_NAME"] for r in query("SELECT * FROM VESSEL_MST ORDER BY VESSEL_ID")}
    actions = {r["ACTION_SEQ"]: r["ACTION_DESCRIPTION"] for r in query("SELECT * FROM ACTION_MST ORDER BY ACTION_SEQ")}
    change = proposal["mapping_change"] or {}
    kinds, v_list, a_list = list(EDIT_KINDS), list(vessels), list(actions)
    statuses = list(CLOSED_TARGETS)
    with st.form("edit_form"):
        kind = st.selectbox("매핑 변경 종류", kinds, index=kinds.index(change.get("kind", "none")),
                            format_func=EDIT_KINDS.get, key="edit_kind")
        vessel = st.selectbox("VESSEL", v_list, index=v_list.index(change.get("vessel_id", issue["VESSEL_ID"])),
                              format_func=lambda v: f"{v} {vessels[v]}", key="edit_vessel")
        action = st.selectbox("ACTION(대상, 수정이면 바꾸기 전)", a_list,
                              index=a_list.index(change.get("action_seq", issue["ACTION_SEQ"])),
                              format_func=lambda a: f"{a} {actions[a]}", key="edit_action")
        target = st.selectbox("바꿀 ACTION(수정일 때만)", a_list,
                              index=a_list.index(change.get("new_action_seq") or a_list[0]),
                              format_func=lambda a: f"{a} {actions[a]}", key="edit_new_action")
        status = st.selectbox("새 상태", statuses, index=statuses.index(proposal["new_status"]), key="edit_status")
        result = st.text_area("처리결과 설명", value=proposal["result"], key="edit_result")
        submitted = st.form_submit_button("편집 완료")
    if submitted:
        mapping = None if kind == "none" else {"kind": kind, "vessel_id": vessel, "action_seq": action,
                                               **({"new_action_seq": target} if kind == "update" else {})}
        respond(thread_id, edited={"mapping_change": mapping, "new_status": status, "result": result})
    if st.button("편집 취소", key="edit_cancel"):
        respond(thread_id, "n")


def retry(thread_id: str) -> None:
    work(lambda progress: retry_turn(thread_id, progress=progress))


def show_failure_banner(thread_id: str) -> None:
    """실패로 멈춘 thread: 오류 배너 + [다시 시도](멈춘 지점부터, 관리자 재입력 없이)."""
    detail = st.session_state.get("last_failure")
    st.error("모든 모델이 응답하지 못했거나 MCP 서버 연결이 끊겨 대화가 멈췄습니다. 대화 상태는 보존되어 있습니다. "
             "[다시 시도]를 누르면 멈춘 지점부터 이어 갑니다." + (f"\n\n({detail})" if detail else ""))
    if st.button("다시 시도", key="retry_turn"):
        st.session_state.pop("last_failure", None)
        retry(thread_id)
        st.rerun()


def page_assistant() -> None:
    """조사 비서. [이 건 조사]로 들어오면 그 이슈의 thread, 메뉴로 들어오면 이슈 없는 대화 thread(관리자별)."""
    st.header("조사 비서")
    user_id = st.session_state.current_user
    issue_no = st.session_state.get("chat_issue")
    if issue_no is None:
        threads = st.session_state.setdefault("free_threads", {})
        if user_id not in threads:
            threads[user_id] = free_thread_id(user_id)
        thread_id = threads[user_id]
        left, right = st.columns([4, 1])
        left.caption(f"{MODE_NAMES['none']} · thread {thread_id}")
        right.button("새 대화", key="new_chat", on_click=new_free_chat)
    else:
        issue = query("SELECT * FROM ISSUE WHERE ISSUE_NO = ?", (issue_no,))[0]
        st.caption(f"이슈 #{issue_no} · {issue['VESSEL_ID']} / {issue['ACTION_SEQ']} · {issue['STATUS']} · {issue['INQUIRY']}")
        thread_id = issue_thread_id(issue_no, user_id)
        left, right = st.columns([4, 1])
        left.caption(f"{MODE_NAMES[issue_mode(issue_no)]} · thread {thread_id}")
        right.button("이슈 없는 대화로", key="leave_issue", on_click=leave_issue_chat)

    asyncio.run(open_thread(thread_id))  # 열린 이슈의 새 thread면 첫 질문(y/n)을 남긴다
    text = st.chat_input("비서에게 질문")
    if text:
        send(thread_id, text)
    for message in asyncio.run(load_messages(thread_id)):
        show_message(message)
    if asyncio.run(needs_retry(thread_id)):
        show_failure_banner(thread_id)
    if (pending := asyncio.run(pending_approval(thread_id))) is not None:
        show_approval(thread_id, pending)


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
    page = st.sidebar.radio("화면", pages, key="page", on_change=leave_issue_chat)
    {"이슈대장": page_ledger, "신규 문의 등록": page_register, "조사 비서": page_assistant}[page]()


main()
