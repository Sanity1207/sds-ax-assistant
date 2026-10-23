"""처리 기록 출력 명령(개발자용). Streamlit 화면에는 보여 주지 않는다.

    python -m app.trace --issue <no>    → traces/issue_<no>.md   (그 이슈의 세션: 자동 분류 + 모든 관리자의 대화)
    python -m app.trace --thread <id>   → traces/thread_<id>.md  (그 thread의 실행)
    python -m app.trace --list          → 최근 세션 목록(화면 출력)

세션의 실행과 이벤트를 시각순으로 한 파일에 쓰고, 이벤트마다 그 실행의 thread·사용자를 함께 적는다.
CALL_LOG와 MESSAGE_LOG는 실행 안에서 순번을 나눠 쓰므로 (시각, 실행 순서, 순번)으로 정렬하면 일어난 순서가 된다.
시드 이슈는 자동 분류를 거치지 않아 기록이 없다.
"""

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

from app.config import TRACES_DIR, now_kst
from app.db import connect

SEED_NO_TRACE = "시드로 넣은 건이라 기록 없음"


class TraceNotFound(LookupError):
    """출력할 이슈·thread가 없다."""


def _runs(conn: sqlite3.Connection, where: str, params: tuple) -> list[sqlite3.Row]:
    # rowid = 실행을 시작한 순서(PROCESS_LOG는 실행 시작 때 INSERT한다).
    return conn.execute(f"SELECT rowid AS ORDER_NO, * FROM PROCESS_LOG WHERE {where} ORDER BY STARTED_AT, rowid",
                        params).fetchall()


def timeline(conn: sqlite3.Connection, runs: list[sqlite3.Row]) -> list[dict]:
    """실행들의 이벤트(CALL_LOG)와 말(MESSAGE_LOG)을 시각순으로 합친다."""
    by_id = {r["RUN_ID"]: r for r in runs}
    if not by_id:
        return []
    marks = ", ".join("?" for _ in by_id)
    items = []
    for row in conn.execute(f"SELECT * FROM CALL_LOG WHERE RUN_ID IN ({marks})", tuple(by_id)):
        items.append({"type": "event", "row": row, "run": by_id[row["RUN_ID"]]})
    for row in conn.execute(f"SELECT * FROM MESSAGE_LOG WHERE RUN_ID IN ({marks})", tuple(by_id)):
        items.append({"type": "message", "row": row, "run": by_id[row["RUN_ID"]]})
    items.sort(key=lambda i: (i["row"]["CREATED_AT"], i["run"]["ORDER_NO"], i["row"]["SEQ"]))
    return items


def _pretty(text: str | None) -> str:
    """JSON이면 들여쓰기해서, 아니면 그대로."""
    if text is None:
        return ""
    try:
        return json.dumps(json.loads(text), ensure_ascii=False, indent=2)
    except (ValueError, TypeError):
        return text


def _block(label: str, text: str | None) -> list[str]:
    if text is None or text == "":
        return []
    return [f"- {label}:", "", "````", _pretty(text), "````", ""]


def _who(run: sqlite3.Row) -> str:
    return f"thread {run['THREAD_ID'] or '-'} · 사용자 {run['USER_ID']}"


def _run_table(runs: list[sqlite3.Row]) -> list[str]:
    lines = ["| # | RUN_ID | 실행 종류 | 이슈 | thread | 사용자 | 시작 | 끝 | 결과 | 도구 호출 | 응답 모델 | 멈춤 사유 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for i, r in enumerate(runs, 1):
        cells = [i, r["RUN_ID"], r["RUN_KIND"], f"#{r['ISSUE_NO']}" if r["ISSUE_NO"] else "-", r["THREAD_ID"] or "-",
                 r["USER_ID"], r["STARTED_AT"], r["ENDED_AT"] or "(끝나지 않음)", r["RESULT"] or "", r["TOOL_CALLS"],
                 r["MODEL_ID"] or "-", r["STOP_REASON"] or "-"]
        lines.append("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in cells) + " |")
    return lines


def _timeline_lines(items: list[dict]) -> list[str]:
    lines = []
    for item in items:
        row, run = item["row"], item["run"]
        if item["type"] == "message":
            lines += [f"### {row['CREATED_AT']} · [말] {row['SPEAKER']} · {_who(run)}", "", f"- RUN_ID: {run['RUN_ID']}",
                      *_block("내용", row["CONTENT"])]
            continue
        head = f"### {row['CREATED_AT']} · [{row['EVENT_KIND']}] {row['NAME']} · {_who(run)}"
        meta = [f"RUN_ID: {run['RUN_ID']}", f"순번 {row['SEQ']}", f"성공 {row['SUCCESS']}"]
        if row["MODEL_ID"]:
            meta.append(f"모델 {row['MODEL_ID']}")
        if row["ELAPSED_MS"] is not None:
            meta.append(f"{row['ELAPSED_MS']} ms")
        lines += [head, "", "- " + " · ".join(meta)]
        if row["ERROR"]:
            lines.append(f"- 오류: {row['ERROR']}")
        lines += [""] + _block("입력", row["INPUT"]) + _block("출력", row["OUTPUT"])
    return lines


def render_issue(issue_no: int, conn: sqlite3.Connection | None = None) -> str:
    own = conn is None
    conn = conn or connect()
    try:
        issue = conn.execute("SELECT * FROM ISSUE WHERE ISSUE_NO = ?", (issue_no,)).fetchone()
        if issue is None:
            raise TraceNotFound(f"이슈 #{issue_no}가 없습니다.")
        runs = _runs(conn, "ISSUE_NO = ?", (issue_no,))
        lines = [f"# 이슈 #{issue_no} 처리 기록", "",
                 f"- 이슈: #{issue_no} · {issue['VESSEL_ID']} / {issue['ACTION_SEQ']} · 요청자 {issue['REQUESTER_ID']}"
                 f" · 처리상태 {issue['STATUS']} · 담당자 {issue['OWNER_ROLE'] or '(빈칸)'}",
                 f"- 문의내용: {issue['INQUIRY']}",
                 f"- 출력 시각: {now_kst()}", ""]
        if not runs:
            return "\n".join(lines + [SEED_NO_TRACE, ""])
        return "\n".join(lines + [f"## 실행 {len(runs)}회", ""] + _run_table(runs) + ["", "## 시간순 이벤트", ""]
                         + _timeline_lines(timeline(conn, runs)))
    finally:
        if own:
            conn.close()


def render_thread(thread_id: str, conn: sqlite3.Connection | None = None) -> str:
    own = conn is None
    conn = conn or connect()
    try:
        runs = _runs(conn, "THREAD_ID = ?", (thread_id,))
        if not runs:
            raise TraceNotFound(f"thread '{thread_id}'의 기록이 없습니다.")
        lines = [f"# thread {thread_id} 처리 기록", "", f"- 출력 시각: {now_kst()}", "",
                 f"## 실행 {len(runs)}회", ""]
        return "\n".join(lines + _run_table(runs) + ["", "## 시간순 이벤트", ""] + _timeline_lines(timeline(conn, runs)))
    finally:
        if own:
            conn.close()


def _safe(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]", "_", name)


def export_issue(issue_no: int, out_dir: Path | str = TRACES_DIR) -> Path:
    path = Path(out_dir) / f"issue_{issue_no}.md"
    text = render_issue(issue_no)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def export_thread(thread_id: str, out_dir: Path | str = TRACES_DIR) -> Path:
    path = Path(out_dir) / f"thread_{_safe(thread_id)}.md"
    text = render_thread(thread_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def list_sessions(limit: int = 20, conn: sqlite3.Connection | None = None) -> str:
    """최근 세션 목록. 이슈 세션(ISSUE_NO별), 이슈 없는 대화(THREAD_ID별), 이슈 없는 등록 거부를 마지막 활동 순으로."""
    own = conn is None
    conn = conn or connect()
    try:
        rows = conn.execute(
            """
            SELECT CASE WHEN ISSUE_NO IS NOT NULL THEN '--issue ' || ISSUE_NO
                        WHEN THREAD_ID IS NOT NULL THEN '--thread ' || THREAD_ID
                        ELSE '(이슈 없음: 등록 거부)' END AS SESSION,
                   COUNT(*) AS RUNS, MIN(STARTED_AT) AS FIRST, MAX(STARTED_AT) AS LAST,
                   GROUP_CONCAT(DISTINCT USER_ID) AS USERS
            FROM PROCESS_LOG GROUP BY SESSION ORDER BY LAST DESC, MAX(rowid) DESC LIMIT ?
            """, (limit,)).fetchall()
    finally:
        if own:
            conn.close()
    if not rows:
        return "처리 기록이 없습니다."
    lines = [f"최근 세션 {len(rows)}개 (마지막 활동 순)"]
    for r in rows:
        lines.append(f"- {r['SESSION']} · 실행 {r['RUNS']}회 · {r['FIRST']} ~ {r['LAST']} · 사용자 {r['USERS']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.trace", description="처리 기록을 시간순 md 파일로 출력한다.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--issue", type=int, help="이슈 번호: 그 이슈의 세션 → traces/issue_<no>.md")
    group.add_argument("--thread", help="thread ID: 그 thread의 실행 → traces/thread_<id>.md")
    group.add_argument("--list", action="store_true", help="최근 세션 목록")
    parser.add_argument("--out", default=str(TRACES_DIR), help="출력 폴더(기본 traces/)")
    parser.add_argument("--limit", type=int, default=20, help="--list에서 보여 줄 세션 수")
    args = parser.parse_args(argv)

    if args.list:
        print(list_sessions(args.limit))
        return 0
    try:
        path = export_issue(args.issue, args.out) if args.issue is not None else export_thread(args.thread, args.out)
    except TraceNotFound as e:
        print(e, file=sys.stderr)
        return 1
    print(f"출력: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
