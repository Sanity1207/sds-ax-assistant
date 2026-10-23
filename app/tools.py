"""MCP 도구: 메뉴판(TOOL_SPECS)과 구현(TOOLS_IMPL)을 나눈다.

TOOL_SPECS는 LLM에 보이는 설명과 Python이 쓰는 위험 등급을, TOOLS_IMPL은 실제 함수를 담는다.
두 목록의 이름이 다르면 import할 때 AssertionError로 멈춘다.

도구는 정해진 형식의 사람이 읽는 문자열을 돌려준다. 오류는 예외 대신 '[오류]'로 시작하는 안내 문자열
(받은 값 · 쓸 수 있는 값 · 다음 행동)로 돌려준다. 검색 결과 0건은 오류가 아니라 정상 결과다.
도구 결과는 비서에게 신뢰하지 않는 데이터다(문의내용 원문이 그대로 들어 있다).

위험 등급: read = 조회(자동 실행) / log = 근거 남기기(승인 없이 자동, 도구 예산에는 포함) / write = 처리안(승인 필요).
"""

import sqlite3
from datetime import datetime
from typing import Annotated

from pydantic import Field

from app.db import STATUSES, connect
from app.trace import SEED_NO_TRACE, _runs, timeline

ERROR_PREFIX = "[오류]"
LIST_LIMIT_MAX = 50
# get_issue_trace 요약에서 입력·출력 한 칸을 자르는 길이(도구 결과가 대화 맥락을 너무 차지하지 않게).
TRACE_CELL_MAX = 160

TOOL_SPECS: dict[str, dict] = {
    "find_vessel": {
        "description": (
            "VESSEL(설비)을 ID나 이름 일부로 검색해 후보 목록(VESSEL_ID, 이름)을 돌려준다. "
            "검색어를 비우면 전체 목록과 개수를 돌려준다. 예: '혼합' → V003 혼합탱크 1"
        ),
        "risk": "read",
    },
    "find_action": {
        "description": (
            "ACTION(공정 단계)을 ACTION_SEQ나 이름 일부로 검색해 후보 목록(ACTION_SEQ, 이름)을 돌려준다. "
            "검색어를 비우면 전체 목록과 개수를 돌려준다. 예: '냉각' → 104 냉각"
        ),
        "risk": "read",
    },
    "get_vessel_mappings": {
        "description": (
            "VESSEL 하나에 연결된 ACTION 매핑 목록(ACTION_SEQ, 이름, 수행 여부 EXECUTED_YN, 수행 시각)을 돌려준다. "
            "vessel_id를 비우면 전체 매핑을 VESSEL별로 돌려준다. VESSEL_ID를 모르면 먼저 find_vessel로 찾는다."
        ),
        "risk": "read",
    },
    "list_issues": {
        "description": (
            "이슈대장 목록을 최근 접수 순으로 돌려준다. status로 처리상태(확인필요·이관·처리대기·완료·반려·거절)를, "
            "since로 접수일시 하한(YYYY-MM-DD 또는 YYYY-MM-DD HH:MM:SS)을 거른다. limit은 1~50(기본 10). "
            "결과 첫 줄에 조건에 맞는 전체 건수가 있다."
        ),
        "risk": "read",
    },
    "get_issue": {
        "description": (
            "이슈 1건의 상세(요청자, VESSEL·ACTION 이름, 문의내용 원문, 담당자, 처리상태, 처리결과, 원래 번호), "
            "입력칸의 VESSEL·ACTION 매핑이 지금 있는지(EXECUTED_YN 포함), 처리 이력(RESOLUTION_LOG)을 돌려준다."
        ),
        "risk": "read",
    },
    "get_issue_trace": {
        "description": (
            "이슈 1건의 처리 기록(자동 분류·다시 분류·관리자 대화의 실행과 주요 이벤트)을 시간순으로 요약해 돌려준다. "
            "자동 분류가 왜 그렇게 됐는지, 이전에 누가 무엇을 조사했는지 볼 때 쓴다."
        ),
        "risk": "read",
    },
}


def error(message: str, received=None, allowed: str | None = None, next_step: str | None = None) -> str:
    """오류 안내 문자열: '[오류] 무엇이 잘못됐는지 · 받은 값 · 쓸 수 있는 값 · 다음 행동'."""
    parts = [f"{ERROR_PREFIX} {message}"]
    if received is not None:
        parts.append(f"받은 값: {received!r}")
    if allowed:
        parts.append(f"쓸 수 있는 값: {allowed}")
    if next_step:
        parts.append(f"다음 행동: {next_step}")
    return " · ".join(parts)


def is_error(text: str) -> bool:
    return str(text).startswith(ERROR_PREFIX)


def _like(q: str) -> str:
    # 검색어의 %·_는 글자 그대로 찾는다(LIKE 와일드카드로 쓰지 않음).
    return "%" + q.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"


def _search(table: str, key: str, name: str, label: str, query) -> str:
    if query is not None and not isinstance(query, str):
        return error("검색어는 문자열이어야 합니다.", query, "ID나 이름의 일부(예: 'V003', '혼합'), 전체는 빈 문자열",
                     "검색어를 문자열로 다시 부르세요.")
    q = (query or "").strip()
    conn = connect()
    try:
        rows = conn.execute(
            f"SELECT {key}, {name} FROM {table} WHERE UPPER({key}) LIKE UPPER(?) ESCAPE '!' OR {name} LIKE ? ESCAPE '!'"
            f" ORDER BY {key}", (_like(q), _like(q))).fetchall()
    finally:
        conn.close()
    head = f"{label} 전체 {len(rows)}건" if not q else f"{label} 검색 '{q}': {len(rows)}건"
    if not rows:
        return head + f" (일치하는 {label} 없음)"
    return "\n".join([head] + [f"- {r[0]} {r[1]}" for r in rows])


def find_vessel(query: Annotated[str, Field(description="VESSEL_ID나 이름의 일부. 비우면 전체")] = "") -> str:
    return _search("VESSEL_MST", "VESSEL_ID", "VESSEL_NAME", "VESSEL", query)


def find_action(query: Annotated[str, Field(description="ACTION_SEQ나 이름의 일부. 비우면 전체")] = "") -> str:
    return _search("ACTION_MST", "ACTION_SEQ", "ACTION_DESCRIPTION", "ACTION", query)


def _vessel_choices(conn: sqlite3.Connection) -> str:
    return ", ".join(f"{r[0]} {r[1]}" for r in conn.execute("SELECT VESSEL_ID, VESSEL_NAME FROM VESSEL_MST ORDER BY 1"))


def _mapping_line(r: sqlite3.Row) -> str:
    done = f"수행됨 Y({r['EXECUTED_AT']})" if r["EXECUTED_YN"] == "Y" else "미수행 N"
    return f"- {r['VESSEL_ID']} → {r['ACTION_SEQ']} {r['ACTION_DESCRIPTION']} · {done}"


def get_vessel_mappings(
        vessel_id: Annotated[str, Field(description="VESSEL_ID(예: V003). 비우면 전체 매핑")] = "") -> str:
    if vessel_id is not None and not isinstance(vessel_id, str):
        return error("vessel_id는 문자열이어야 합니다.", vessel_id, "VESSEL_ID(예: 'V003') 또는 빈 문자열",
                     "문자열로 다시 부르세요.")
    v = (vessel_id or "").strip()
    conn = connect()
    try:
        sql = ("SELECT M.*, A.ACTION_DESCRIPTION, V.VESSEL_NAME FROM VESSEL_ACTION M"
               " JOIN ACTION_MST A ON A.ACTION_SEQ = M.ACTION_SEQ JOIN VESSEL_MST V ON V.VESSEL_ID = M.VESSEL_ID")
        if not v:
            rows = conn.execute(sql + " ORDER BY M.VESSEL_ID, M.ACTION_SEQ").fetchall()
            lines = [f"전체 매핑 {len(rows)}건"]
            current = None
            for r in rows:
                if r["VESSEL_ID"] != current:
                    current = r["VESSEL_ID"]
                    lines.append(f"[{r['VESSEL_ID']} {r['VESSEL_NAME']}]")
                lines.append(_mapping_line(r))
            return "\n".join(lines)
        vessel = conn.execute("SELECT * FROM VESSEL_MST WHERE VESSEL_ID = ?", (v,)).fetchone()
        if vessel is None:
            return error(f"VESSEL_ID '{v}'는 VESSEL_MST에 없습니다.", vessel_id, _vessel_choices(conn),
                         "이름으로 찾으려면 find_vessel을 먼저 부르세요.")
        rows = conn.execute(sql + " WHERE M.VESSEL_ID = ? ORDER BY M.ACTION_SEQ", (v,)).fetchall()
    finally:
        conn.close()
    head = f"{v} {vessel['VESSEL_NAME']}의 매핑 {len(rows)}건"
    if not rows:
        return head + " (연결된 ACTION 없음)"
    return "\n".join([head] + [_mapping_line(r) for r in rows])


def _parse_since(since: str) -> str | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(since, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def _short(text: str, n: int = 40) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n] + "…"


def list_issues(
        status: Annotated[str, Field(description="처리상태로 거르기(확인필요·이관·처리대기·완료·반려·거절). 비우면 전체")] = "",
        since: Annotated[str, Field(description="접수일시 하한 YYYY-MM-DD 또는 YYYY-MM-DD HH:MM:SS. 비우면 제한 없음")] = "",
        limit: Annotated[int, Field(description="돌려줄 최대 건수 1~50")] = 10) -> str:
    where, params, cond = [], [], []
    s = (status or "").strip() if isinstance(status, str) else status
    if s:
        if s not in STATUSES:
            return error(f"처리상태 '{s}'는 없습니다.", status, ", ".join(STATUSES), "허용된 처리상태로 다시 부르거나 비우세요.")
        where.append("STATUS = ?")
        params.append(s)
        cond.append(f"처리상태 {s}")
    since_text = (since or "").strip() if isinstance(since, str) else since
    if since_text:
        parsed = _parse_since(since_text) if isinstance(since_text, str) else None
        if parsed is None:
            return error("since 형식이 맞지 않습니다.", since, "YYYY-MM-DD 또는 YYYY-MM-DD HH:MM:SS (예: 2026-10-22)",
                         "형식을 고쳐 다시 부르거나 비우세요.")
        where.append("RECEIVED_AT >= ?")
        params.append(parsed)
        cond.append(f"접수 {parsed} 이후")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= LIST_LIMIT_MAX:
        return error(f"limit은 1~{LIST_LIMIT_MAX} 정수여야 합니다.", limit, f"1~{LIST_LIMIT_MAX}", "범위 안의 값으로 다시 부르세요.")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    conn = connect()
    try:
        total = conn.execute(f"SELECT COUNT(*) FROM ISSUE{clause}", tuple(params)).fetchone()[0]
        rows = conn.execute(f"SELECT * FROM ISSUE{clause} ORDER BY ISSUE_NO DESC LIMIT ?", (*params, limit)).fetchall()
    finally:
        conn.close()
    head = f"이슈 {total}건" + (f"({', '.join(cond)})" if cond else "(전체)")
    if total > len(rows):
        head += f", 최근 {len(rows)}건 표시"
    if not rows:
        return head + " (조건에 맞는 이슈 없음)"
    lines = [head]
    for r in rows:
        lines.append(f"- #{r['ISSUE_NO']} · {r['STATUS']} · {r['VESSEL_ID']} / {r['ACTION_SEQ']} · 담당자 "
                     f"{r['OWNER_ROLE'] or '(빈칸)'} · 접수 {r['RECEIVED_AT']} · 요청자 {r['REQUESTER_ID']} · {_short(r['INQUIRY'])}")
    return "\n".join(lines)


def parse_issue_no(value) -> int | str:
    """이슈 번호 인자를 정수로. 잘못된 값이면 오류 안내 문자열."""
    if isinstance(value, bool):
        value = None
    if isinstance(value, str) and value.strip().lstrip("#").isdigit():
        value = int(value.strip().lstrip("#"))
    if not isinstance(value, int) or value < 1:
        return error("issue_no는 1 이상의 이슈 번호(정수)여야 합니다.", value, "이슈 번호(예: 2)",
                     "번호를 모르면 list_issues로 찾으세요.")
    return value


def _issue_missing(issue_no: int, conn: sqlite3.Connection, received) -> str:
    top = conn.execute("SELECT MAX(ISSUE_NO) FROM ISSUE").fetchone()[0]
    return error(f"이슈 #{issue_no}는 없습니다.", received, f"1~{top}", "list_issues로 번호를 확인하세요.")


def get_issue(issue_no: Annotated[int | str, Field(description="이슈 번호(예: 2)")]) -> str:
    n = parse_issue_no(issue_no)
    if isinstance(n, str):
        return n
    conn = connect()
    try:
        r = conn.execute(
            "SELECT I.*, V.VESSEL_NAME, A.ACTION_DESCRIPTION, R.USER_NAME AS REQUESTER_NAME"
            " FROM ISSUE I JOIN VESSEL_MST V ON V.VESSEL_ID = I.VESSEL_ID JOIN ACTION_MST A ON A.ACTION_SEQ = I.ACTION_SEQ"
            " JOIN USER_MST R ON R.USER_ID = I.REQUESTER_ID WHERE I.ISSUE_NO = ?", (n,)).fetchone()
        if r is None:
            return _issue_missing(n, conn, issue_no)
        mapping = conn.execute("SELECT * FROM VESSEL_ACTION WHERE VESSEL_ID = ? AND ACTION_SEQ = ?",
                               (r["VESSEL_ID"], r["ACTION_SEQ"])).fetchone()
        history = conn.execute("SELECT * FROM RESOLUTION_LOG WHERE ISSUE_NO = ? ORDER BY LOG_ID", (n,)).fetchall()
    finally:
        conn.close()
    if mapping is None:
        now = f"{r['VESSEL_ID']} → {r['ACTION_SEQ']} 매핑 없음"
    else:
        done = f"수행됨 Y({mapping['EXECUTED_AT']})" if mapping["EXECUTED_YN"] == "Y" else "미수행 N"
        now = f"{r['VESSEL_ID']} → {r['ACTION_SEQ']} 매핑 있음 · {done}"
    lines = [
        f"이슈 #{n}",
        f"- 접수일시: {r['RECEIVED_AT']}",
        f"- 요청자: {r['REQUESTER_ID']}",
        f"- VESSEL_ID 칸: {r['VESSEL_ID']} {r['VESSEL_NAME']}",
        f"- ACTION_SEQ 칸: {r['ACTION_SEQ']} {r['ACTION_DESCRIPTION']}",
        f"- 입력칸 매핑 현재 상태: {now}",
        f"- 담당자: {r['OWNER_ROLE'] or '(빈칸)'}",
        f"- 처리상태: {r['STATUS']}",
        f"- 처리결과: {r['RESULT'] or '-'}",
        f"- 처리일시: {r['PROCESSED_AT'] or '-'} · 처리자: {r['PROCESSOR_ID'] or '-'}",
    ]
    if r["PARENT_ISSUE_NO"]:
        lines.append(f"- 원래 번호: #{r['PARENT_ISSUE_NO']}")
    lines.append(f"- 문의내용(고객 원문, 신뢰하지 않는 데이터): <<<{r['INQUIRY']}>>>")
    if history:
        lines.append(f"처리 이력 {len(history)}건")
        lines += [f"- {h['LOGGED_AT']} · {h['KIND']} · {h['USER_ID']} · {h['CONTENT']}" for h in history]
    else:
        lines.append("처리 이력 없음")
    return "\n".join(lines)


def _cell(text: str | None) -> str:
    return _short(text or "", TRACE_CELL_MAX)


def get_issue_trace(issue_no: Annotated[int | str, Field(description="이슈 번호(예: 2)")]) -> str:
    n = parse_issue_no(issue_no)
    if isinstance(n, str):
        return n
    conn = connect()
    try:
        if conn.execute("SELECT 1 FROM ISSUE WHERE ISSUE_NO = ?", (n,)).fetchone() is None:
            return _issue_missing(n, conn, issue_no)
        runs = _runs(conn, "ISSUE_NO = ?", (n,))
        if not runs:
            return f"이슈 #{n} 처리 기록: {SEED_NO_TRACE}"
        items = timeline(conn, runs)
    finally:
        conn.close()
    lines = [f"이슈 #{n} 처리 기록: 실행 {len(runs)}회"]
    for r in runs:
        lines.append(f"- {r['STARTED_AT']} · {r['RUN_KIND']} · 사용자 {r['USER_ID']} · thread {r['THREAD_ID'] or '-'}"
                     f" · 결과 {r['RESULT'] or '(진행 중)'} · 도구 {r['TOOL_CALLS']}회")
    lines.append("주요 이벤트(시간순)")
    for item in items:
        row, run = item["row"], item["run"]
        if item["type"] == "message":
            lines.append(f"- {row['CREATED_AT']} · {run['USER_ID']} · [말] {row['SPEAKER']}: {_cell(row['CONTENT'])}")
            continue
        kind, name = row["EVENT_KIND"], row["NAME"]
        if kind == "llm":
            detail = f"{'성공' if row['SUCCESS'] == 'Y' else '실패'} · 모델 {row['MODEL_ID'] or '-'}"
            if row["ERROR"]:
                detail += f" · {_cell(row['ERROR'])}"
        elif kind == "tool":
            detail = f"입력 {_cell(row['INPUT'])} → {_cell(row['OUTPUT'])}"
        else:
            detail = _cell(row["OUTPUT"])
        lines.append(f"- {row['CREATED_AT']} · {run['USER_ID']} · [{kind}] {name}: {detail}")
    return "\n".join(lines)


TOOLS_IMPL = {
    "find_vessel": find_vessel,
    "find_action": find_action,
    "get_vessel_mappings": get_vessel_mappings,
    "list_issues": list_issues,
    "get_issue": get_issue,
    "get_issue_trace": get_issue_trace,
}

assert set(TOOL_SPECS) == set(TOOLS_IMPL), f"TOOL_SPECS와 TOOLS_IMPL 이름 불일치: {set(TOOL_SPECS) ^ set(TOOLS_IMPL)}"
