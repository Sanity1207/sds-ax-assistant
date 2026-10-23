"""처리안(propose_resolution): 검사, 위험 등급, 승인 카드, 실행.

처리안 = 매핑 변경(없거나 1건) + 이슈 상태 변경. 모든 판정은 LLM이 아니라 이 모듈의 Python이 DB를 보고 한다.

검사(check_proposal) — 어긋나면 처리안을 거부하고 '[오류]' 안내 문자열로 돌려준다.
- 그 화면의 이슈만: issue_no가 화면의 이슈와 같아야 한다.
- 상태 전이: 열린 건(확인필요·이관·처리대기) → 완료·반려·거절. 닫힌 건은 다시 열지 않는다.
- 이슈대장·데이터 일치: 매핑 변경이 있으면 새 상태는 완료뿐.
    처리대기 = 변경 있어야 완료, 반려·거절은 변경 없이만 / 확인필요 = 변경 있거나 없이 완료, 반려·거절은 변경 없이만 /
    이관 = 변경 없이만 완료·반려·거절.
- 매핑 변경 대상은 그 이슈의 입력칸 값(VESSEL_ID 칸, ACTION_SEQ 칸)이다. 매핑 수정은 ACTION_SEQ 칸이 바꾸기 전 값이고
  바꿀 ACTION은 new_action_seq. 마스터에 있는 값이어야 하고, 생성은 매핑이 없어야, 삭제·수정은 있어야 한다.
위험 등급(risk): 매핑 삭제·수정의 대상 매핑이 수행됨(EXECUTED_YN='Y')이면 destructive, 그 밖은 write.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from app.config import now_kst
from app.db import connect

OPEN_STATUSES = ("확인필요", "이관", "처리대기")
CLOSED_TARGETS = ("완료", "반려", "거절")
KINDS = {"create": "생성", "update": "수정", "delete": "삭제"}
RESULT_MAX = 500


@dataclass
class CheckResult:
    """검사 결과. ok면 proposal은 정규화한 처리안(위험 등급·제안 시점 EXECUTED_YN 포함)."""

    ok: bool
    errors: list[str] = field(default_factory=list)
    proposal: dict[str, Any] | None = None

    def error_text(self, received: Any = None) -> str:
        from app.tools import error

        return error("처리안 검사에서 거부했습니다: " + " / ".join(self.errors), received, None,
                     "get_issue로 이슈 상태·입력칸·매핑을 확인하고, 규칙에 맞게 고친 처리안으로 다시 부르세요.")


def _parse_change(value) -> tuple[dict | None, str | None]:
    """mapping_change 인자 정리. None·빈 값·'없음'은 변경 없음. JSON 문자열도 받는다."""
    if value in (None, "", {}, "null", "none", "없음"):
        return None, None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None, f"mapping_change를 해석할 수 없습니다(받은 값: {value!r}). 객체 또는 null이어야 합니다."
    if not isinstance(value, dict):
        return None, f"mapping_change는 객체 또는 null이어야 합니다(받은 값: {value!r})."
    change = {k: (str(v).strip() if v is not None else None) for k, v in value.items()}
    return change, None


def mapping_row(conn: sqlite3.Connection, vessel_id: str, action_seq: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM VESSEL_ACTION WHERE VESSEL_ID = ? AND ACTION_SEQ = ?",
                        (vessel_id, action_seq)).fetchone()


def check_proposal(issue_no, mapping_change, new_status, result, screen_issue: int | None,
                   conn: sqlite3.Connection | None = None) -> CheckResult:
    """처리안을 검사하고 위험 등급을 정한다. 문제를 모두 모아 돌려준다."""
    own = conn is None
    conn = conn or connect()
    try:
        return _check(issue_no, mapping_change, new_status, result, screen_issue, conn)
    finally:
        if own:
            conn.close()


def _check(issue_no, mapping_change, new_status, result, screen_issue, conn) -> CheckResult:
    from app.tools import parse_issue_no

    errors: list[str] = []
    n = parse_issue_no(issue_no)
    if isinstance(n, str):
        return CheckResult(False, [f"issue_no가 올바른 이슈 번호가 아닙니다(받은 값: {issue_no!r})"])
    if screen_issue is None:
        return CheckResult(False, ["이슈 없는 대화에서는 처리안을 만들 수 없습니다"])
    if n != screen_issue:
        return CheckResult(False, [f"처리안은 지금 화면의 이슈 #{screen_issue}에만 만들 수 있습니다(받은 번호: #{n})"])
    issue = conn.execute("SELECT * FROM ISSUE WHERE ISSUE_NO = ?", (n,)).fetchone()
    if issue is None:
        return CheckResult(False, [f"이슈 #{n}가 없습니다"])
    if issue["STATUS"] not in OPEN_STATUSES:
        return CheckResult(False, [f"이슈 #{n}는 이미 닫힌 건({issue['STATUS']})이라 처리안을 만들 수 없습니다"])

    status = new_status.strip() if isinstance(new_status, str) else new_status
    if status not in CLOSED_TARGETS:
        errors.append(f"new_status '{new_status}'는 허용되지 않습니다. 열린 건에서 바꿀 수 있는 상태: {', '.join(CLOSED_TARGETS)}")
    text = " ".join(str(result or "").split())
    if not text:
        errors.append("result(처리결과 설명)가 비어 있습니다")
    elif len(text) > RESULT_MAX:
        errors.append(f"result는 {RESULT_MAX}자 이내여야 합니다(받은 길이: {len(text)}자)")

    change, problem = _parse_change(mapping_change)
    if problem:
        errors.append(problem)
    risk, executed_yn = "write", None
    if change is not None:
        kind = change.get("kind")
        vessel, action, new_action = change.get("vessel_id"), change.get("action_seq"), change.get("new_action_seq")
        if kind not in KINDS:
            errors.append(f"mapping_change.kind '{kind}'는 없습니다. 쓸 수 있는 값: create(생성), update(수정), delete(삭제)")
        if vessel != issue["VESSEL_ID"]:
            errors.append(f"매핑 변경의 vessel_id는 이슈의 VESSEL_ID 칸 값 {issue['VESSEL_ID']}이어야 합니다(받은 값: {vessel!r})")
        if action != issue["ACTION_SEQ"]:
            errors.append(f"매핑 변경의 action_seq는 이슈의 ACTION_SEQ 칸 값 {issue['ACTION_SEQ']}이어야 합니다(받은 값: {action!r})."
                          " 매핑 수정이면 바꿀 ACTION은 new_action_seq에 넣습니다")
        if kind in KINDS and vessel == issue["VESSEL_ID"] and action == issue["ACTION_SEQ"]:
            current = mapping_row(conn, vessel, action)
            if kind == "create":
                if current is not None:
                    errors.append(f"{vessel}→{action} 매핑이 이미 있어 생성할 수 없습니다")
                if new_action:
                    errors.append("매핑 생성에는 new_action_seq를 쓰지 않습니다")
            else:
                if current is None:
                    errors.append(f"{vessel}→{action} 매핑이 없어 {KINDS[kind]}할 수 없습니다")
                else:
                    executed_yn = current["EXECUTED_YN"]
                    risk = "destructive" if executed_yn == "Y" else "write"
            if kind == "update":
                if not new_action:
                    errors.append("매핑 수정에는 바꿀 ACTION(new_action_seq)이 필요합니다")
                elif conn.execute("SELECT 1 FROM ACTION_MST WHERE ACTION_SEQ = ?", (new_action,)).fetchone() is None:
                    choices = ", ".join(r[0] for r in conn.execute("SELECT ACTION_SEQ FROM ACTION_MST ORDER BY 1"))
                    errors.append(f"new_action_seq '{new_action}'는 ACTION_MST에 없습니다. 쓸 수 있는 값: {choices}")
                elif new_action == action:
                    errors.append("new_action_seq가 바꾸기 전 ACTION과 같습니다")
                elif mapping_row(conn, vessel, new_action) is not None:
                    errors.append(f"{vessel}→{new_action} 매핑이 이미 있어 그 ACTION으로 바꿀 수 없습니다")
            if kind == "delete" and new_action:
                errors.append("매핑 삭제에는 new_action_seq를 쓰지 않습니다")
        if status in CLOSED_TARGETS and status != "완료":
            errors.append(f"매핑을 바꾸는 처리안의 새 상태는 완료뿐입니다({status}는 매핑 변경 없이만)")
        if issue["STATUS"] == "이관":
            errors.append("이관 건은 매핑 변경 없이만 처리할 수 있습니다(담당자 확인 후 완료·반려·거절)")
    elif issue["STATUS"] == "처리대기" and status == "완료":
        errors.append("처리대기 건(매핑 생성·수정·삭제 요청)은 매핑 변경이 있어야 완료할 수 있습니다. "
                      "요청대로 처리하지 않으면 반려 또는 거절입니다")

    if errors:
        return CheckResult(False, errors)
    proposal = {
        "issue_no": n,
        "mapping_change": None if change is None else {
            "kind": change["kind"], "vessel_id": change["vessel_id"], "action_seq": change["action_seq"],
            **({"new_action_seq": change["new_action_seq"]} if change["kind"] == "update" else {})},
        "new_status": status,
        "result": text,
        "risk": risk,
        "executed_yn": executed_yn,
        "issue_status": issue["STATUS"],
    }
    return CheckResult(True, [], proposal)


def change_text(change: dict | None) -> str:
    """처리결과 첫머리(Python 작성): '[매핑 변경] V003→104 생성' 또는 '[매핑 변경 없음]'."""
    if not change:
        return "[매핑 변경 없음]"
    v, a = change["vessel_id"], change["action_seq"]
    if change["kind"] == "create":
        return f"[매핑 변경] {v}→{a} 생성"
    if change["kind"] == "delete":
        return f"[매핑 변경] {v}→{a} 삭제"
    return f"[매핑 변경] {v}→{a} 삭제, {v}→{change['new_action_seq']} 생성"


def summary(proposal: dict) -> str:
    """처리안 한 줄 요약(도구 결과·대화 맥락용)."""
    return (f"#{proposal['issue_no']} {proposal['issue_status']} → {proposal['new_status']} · "
            f"{change_text(proposal['mapping_change'])} · 위험 등급 {proposal['risk']}")


def _mapping_desc(conn: sqlite3.Connection, vessel_id: str, action_seq: str, new_row: bool = False) -> str:
    """매핑 한 줄: 'V003→103 교반 · 미수행 N' / 'V003→104 없음'. new_row면 반영 후 새로 생길 행."""
    name = conn.execute("SELECT ACTION_DESCRIPTION FROM ACTION_MST WHERE ACTION_SEQ = ?", (action_seq,)).fetchone()
    label = f"{vessel_id}→{action_seq} {name[0] if name else '?'}"
    if new_row:
        return f"{label} · 미수행 N(새 행)"
    row = mapping_row(conn, vessel_id, action_seq)
    if row is None:
        return f"{vessel_id}→{action_seq} 없음"
    done = f"수행됨 Y({row['EXECUTED_AT']})" if row["EXECUTED_YN"] == "Y" else "미수행 N"
    return f"{label} · {done}"


def build_card(proposal: dict, conn: sqlite3.Connection | None = None) -> dict:
    """승인 카드. 이슈·매핑 값은 LLM이 아니라 Python이 DB에서 직접 읽은 원본이다."""
    own = conn is None
    conn = conn or connect()
    try:
        issue = conn.execute(
            "SELECT I.*, V.VESSEL_NAME, A.ACTION_DESCRIPTION FROM ISSUE I JOIN VESSEL_MST V ON V.VESSEL_ID = I.VESSEL_ID"
            " JOIN ACTION_MST A ON A.ACTION_SEQ = I.ACTION_SEQ WHERE I.ISSUE_NO = ?", (proposal["issue_no"],)).fetchone()
        change = proposal["mapping_change"]
        if change is None:
            before, after, executed = ["매핑 변경 없음"], ["매핑 변경 없음"], "-"
        else:
            v, a = change["vessel_id"], change["action_seq"]
            current = mapping_row(conn, v, a)
            executed = current["EXECUTED_YN"] if current is not None else "-"
            if change["kind"] == "create":
                before, after = [_mapping_desc(conn, v, a)], [_mapping_desc(conn, v, a, new_row=True)]
            elif change["kind"] == "delete":
                before, after = [_mapping_desc(conn, v, a)], [f"{v}→{a} 없음"]
            else:
                b = change["new_action_seq"]
                before = [_mapping_desc(conn, v, a), _mapping_desc(conn, v, b)]
                after = [f"{v}→{a} 없음", _mapping_desc(conn, v, b, new_row=True)]
    finally:
        if own:
            conn.close()
    return {
        "risk": proposal["risk"],
        "tool": "propose_resolution",
        "issue_no": proposal["issue_no"],
        "issue_status": issue["STATUS"],
        "new_status": proposal["new_status"],
        "requester_id": issue["REQUESTER_ID"],
        "inquiry": issue["INQUIRY"],
        "input_fields": f"{issue['VESSEL_ID']} {issue['VESSEL_NAME']} / {issue['ACTION_SEQ']} {issue['ACTION_DESCRIPTION']}",
        "change": change_text(change),
        "before": before,
        "after": after,
        "executed_yn": executed,
        "result": f"{change_text(change)} {proposal['result']}",
    }


def card_text(card: dict) -> str:
    """승인 카드를 글로(대화 기록·처리 기록용). 화면은 같은 값을 표로 보여 준다."""
    return "\n".join([
        f"[승인 카드] 위험 등급: {card['risk']} · 도구: {card['tool']}",
        f"- 근거: 이슈 #{card['issue_no']} ({card['issue_status']} → {card['new_status']}) · 요청자 {card['requester_id']}"
        f" · 입력칸 {card['input_fields']}",
        f"- 문의 원문: {card['inquiry']}",
        f"- 대상: {card['change']}",
        f"- 반영 전: {'; '.join(card['before'])}",
        f"- 반영 후: {'; '.join(card['after'])}",
        f"- 현재 수행 상태(EXECUTED_YN): {card['executed_yn']}",
        f"- 처리결과(반영될 문장): {card['result']}",
    ])


# ---- 승인 뒤의 기록과 실행 ----

@dataclass
class ExecuteOutcome:
    ok: bool
    message: str  # 성공: '반영 완료: …' / 실패: 사유
    revalidation: list[str] = field(default_factory=list)


def _log(conn: sqlite3.Connection, issue_no: int, kind: str, content: str, payload: Any, user_id: str) -> None:
    conn.execute("INSERT INTO RESOLUTION_LOG (ISSUE_NO, KIND, CONTENT, PROPOSAL_JSON, USER_ID, LOGGED_AT)"
                 " VALUES (?, ?, ?, ?, ?, ?)",
                 (issue_no, kind, content, json.dumps(payload, ensure_ascii=False), user_id, now_kst()))


def record(issue_no: int, kind: str, content: str, payload: Any, user_id: str) -> None:
    """RESOLUTION_LOG 1행을 짧은 트랜잭션으로 남긴다(승인·취소·직접 편집·실행 실패)."""
    conn = connect()
    try:
        with conn:
            _log(conn, issue_no, kind, content, payload, user_id)
    finally:
        conn.close()


def record_cancel(proposal: dict, user_id: str) -> None:
    record(proposal["issue_no"], "취소", f"취소: {summary(proposal)}", proposal, user_id)


def record_edit(before: dict, after: dict, user_id: str) -> None:
    record(after["issue_no"], "직접 편집", f"직접 편집: {summary(before)} ⇒ {summary(after)}",
           {"before": before, "after": after}, user_id)


def done_text(proposal: dict) -> str:
    """반영 후 Python이 쓰는 정해진 문장. 예: '반영 완료: [매핑 변경] V003→104 생성, #10 완료'."""
    return f"반영 완료: {change_text(proposal['mapping_change'])}, #{proposal['issue_no']} {proposal['new_status']}"


def revalidate(proposal: dict, conn: sqlite3.Connection) -> list[str]:
    """실행 직전 재검증: 이슈가 아직 열린 상태인지, 대상 매핑의 존재/부재, EXECUTED_YN이 제안 시점과 같은지,
    새 상태가 허용된 전이인지. 처리안 검사 전체를 같은 연결(트랜잭션 안)에서 다시 돌리고 위험 등급·수행 상태를 비교한다."""
    again = _check(proposal["issue_no"], proposal["mapping_change"], proposal["new_status"], proposal["result"],
                   proposal["issue_no"], conn)
    if not again.ok:
        return again.errors
    problems = []
    now = again.proposal
    if now["executed_yn"] != proposal["executed_yn"]:
        problems.append(f"대상 매핑의 EXECUTED_YN이 제안 시점({proposal['executed_yn']})과 다릅니다(지금: {now['executed_yn']})")
    if now["issue_status"] != proposal["issue_status"]:
        problems.append(f"이슈 상태가 제안 시점({proposal['issue_status']})과 다릅니다(지금: {now['issue_status']})")
    if now["risk"] != proposal["risk"]:
        problems.append(f"위험 등급이 제안 시점({proposal['risk']})과 다릅니다(지금: {now['risk']})")
    return problems


def execute_proposal(proposal: dict, user_id: str, conn: sqlite3.Connection | None = None) -> ExecuteOutcome:
    """승인된 처리안을 한 트랜잭션으로 반영한다: 재검증 → 매핑 → ISSUE → RESOLUTION_LOG('실행 성공').

    재검증 실패나 DB 오류면 전체를 롤백하고 RESOLUTION_LOG에 '실행 실패(사유)'를 따로 남긴다(부분 처리 상태 없음).
    매핑 수정 = 삭제 후 생성(새 행 EXECUTED_YN='N', EXECUTED_AT=NULL). EXECUTED 값은 바꾸지 않는다.
    """
    own = conn is None
    conn = conn or connect()
    n, change = proposal["issue_no"], proposal["mapping_change"]
    problems: list[str] = []
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")
            problems = revalidate(proposal, conn)
            if problems:
                conn.rollback()
                reason = "재검증 실패: " + " / ".join(problems)
            else:
                if change is not None:
                    v, a = change["vessel_id"], change["action_seq"]
                    if change["kind"] in ("delete", "update"):
                        conn.execute("DELETE FROM VESSEL_ACTION WHERE VESSEL_ID = ? AND ACTION_SEQ = ?", (v, a))
                    if change["kind"] == "create":
                        conn.execute("INSERT INTO VESSEL_ACTION VALUES (?, ?, 'N', NULL)", (v, a))
                    if change["kind"] == "update":
                        conn.execute("INSERT INTO VESSEL_ACTION VALUES (?, ?, 'N', NULL)", (v, change["new_action_seq"]))
                result = f"{change_text(change)} {proposal['result']}"
                conn.execute("UPDATE ISSUE SET STATUS = ?, RESULT = ?, PROCESSED_AT = ?, PROCESSOR_ID = ? WHERE ISSUE_NO = ?",
                             (proposal["new_status"], result, now_kst(), user_id, n))
                _log(conn, n, "실행 성공", done_text(proposal), proposal, user_id)
                conn.commit()
                return ExecuteOutcome(True, done_text(proposal))
        except sqlite3.Error as e:
            conn.rollback()
            reason = f"DB 오류: {type(e).__name__}: {e}"
        record(n, "실행 실패", f"실행 실패({reason})", proposal, user_id)
        return ExecuteOutcome(False, reason, problems)
    finally:
        if own:
            conn.close()
