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
