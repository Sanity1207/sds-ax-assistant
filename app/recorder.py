"""처리 기록 장치: PROCESS_LOG(실행 1회), CALL_LOG(이벤트 1건), MESSAGE_LOG(말 1건)를 남긴다.

- 실행 = 사람의 행동 1회(문의 등록, 다시 분류, 대화 입력 1회, 승인 카드 응답)에 프로그램이 반응해 멈출 때까지.
  `with start_run(...) as run:` 블록 하나가 실행 1회다. 블록이 예외로 끝나도 끝 시각과 결과('오류: …')를 남긴다.
- 기록은 업무 연결과 섞지 않는다. 이벤트마다 새 연결을 열어 바로 커밋하고 닫으므로, 업무 트랜잭션이 롤백되거나
  모든 모델이 실패해도 기록은 남는다. 연결을 매번 여는 덕에 LangGraph가 노드를 다른 스레드에서 돌려도 안전하다.
- SQLite는 쓰는 연결이 한 번에 하나뿐이다. 그래서 업무 코드는 쓰기 트랜잭션을 연 채로 기록하지 않는다
  (트랜잭션은 짧게 열고, 기록은 그 앞이나 뒤에 한다). 겹치면 connect()의 시간 제한만큼 기다린다.
- CALL_LOG와 MESSAGE_LOG는 실행 안에서 순번(SEQ) 하나를 나눠 써서, 출력할 때 시간순으로 섞어 보여 줄 수 있다.
"""

import json
import logging
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage

from app.config import KST, db_path, now_kst
from app.db import connect

log = logging.getLogger(__name__)


def to_text(value: Any) -> str | None:
    """기록용 문자열. 문자열은 그대로, 나머지는 JSON(한글 그대로)으로 바꾼다."""
    if value is None or isinstance(value, str):
        return value
    return json.dumps(_plain(value), ensure_ascii=False, default=str)


def _plain(value: Any) -> Any:
    """메시지·pydantic 객체를 JSON으로 바꿀 수 있는 값으로 푼다."""
    if isinstance(value, BaseMessage):
        out = {"role": value.type, "content": value.content}
        if getattr(value, "tool_calls", None):
            out["tool_calls"] = [{"name": c["name"], "args": c["args"]} for c in value.tool_calls]
        if getattr(value, "name", None):
            out["name"] = value.name
        return out
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        return {k: _plain(getattr(value, k)) for k in value.__dataclass_fields__}
    return value


def new_run_id() -> str:
    """RUN_ID: 사람이 읽을 수 있게 시작 시각 + 짧은 난수."""
    return f"run-{datetime.now(KST):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


class Run:
    """실행 1회의 기록기. start_run()으로 만든다."""

    def __init__(self, run_kind: str, user_id: str, issue_no: int | None = None,
                 thread_id: str | None = None, path: Path | str | None = None):
        self.run_id = new_run_id()
        self.run_kind = run_kind
        self.user_id = user_id
        self.issue_no = issue_no
        self.thread_id = thread_id
        self.path = path or db_path()
        self.result: str | None = None
        self._seq = 0
        self._lock = threading.Lock()
        self._write(
            "INSERT INTO PROCESS_LOG (RUN_ID, ISSUE_NO, THREAD_ID, USER_ID, RUN_KIND, STARTED_AT)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (self.run_id, issue_no, thread_id, user_id, run_kind, now_kst()), strict=True)

    def _write(self, sql: str, params: tuple, strict: bool = False) -> None:
        """새 연결로 바로 커밋한다. 잠금 대기 시간을 넘기면 기록만 버리고 업무는 계속한다(strict면 예외)."""
        conn = connect(self.path)
        try:
            with conn:
                conn.execute(sql, params)
        except sqlite3.OperationalError as e:
            if strict:
                raise
            log.warning("처리 기록 저장 실패(%s): %s", self.run_id, e)
        finally:
            conn.close()

    def _next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def set_issue(self, issue_no: int) -> None:
        """실행 도중 ISSUE가 생기면(문의 등록) 그 번호를 붙인다. ISSUE가 커밋된 뒤에 부른다."""
        self.issue_no = issue_no
        self._write("UPDATE PROCESS_LOG SET ISSUE_NO = ? WHERE RUN_ID = ?", (issue_no, self.run_id))

    def event(self, kind: str, name: str, input: Any = None, output: Any = None, *, model_id: str | None = None,
              success: bool = True, error: str | None = None, elapsed_ms: int | None = None) -> None:
        """CALL_LOG 1행. kind는 branch·llm·tool·approval·commit·finalize."""
        self._write(
            "INSERT INTO CALL_LOG (RUN_ID, SEQ, EVENT_KIND, NAME, INPUT, OUTPUT, MODEL_ID, SUCCESS, ERROR,"
            " ELAPSED_MS, CREATED_AT) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.run_id, self._next_seq(), kind, name, to_text(input), to_text(output), model_id,
             "Y" if success else "N", error, elapsed_ms, now_kst()))

    def llm(self, prompt_name: str, prompt_version: str, messages: list, *, tools: list | None = None,
            result=None, error: Exception | None = None, elapsed_ms: int | None = None) -> None:
        """LLM 호출 1회(폴백 포함). 보낸 메시지 전체, 원본 응답, 해석 결과, 시도한 모델을 남긴다."""
        sent = {"prompt": prompt_name, "version": prompt_version, "messages": messages}
        if tools:
            sent["tools"] = [getattr(t, "name", str(t)) for t in tools]
        if result is not None:
            out = {"raw": result.raw, "parsed": result.value, "attempts": result.attempts}
            self.event("llm", f"{prompt_name}@v{prompt_version}", sent, out, model_id=result.model_id,
                       elapsed_ms=elapsed_ms)
            return
        out = {"raw": getattr(error, "raw", None), "attempts": getattr(error, "attempts", [])}
        self.event("llm", f"{prompt_name}@v{prompt_version}", sent, out, model_id=getattr(error, "model_id", None),
                   success=False, error=f"{type(error).__name__}: {error}", elapsed_ms=elapsed_ms)

    def message(self, speaker: str, content: str) -> None:
        """MESSAGE_LOG 1행. speaker는 관리자·비서."""
        self._write("INSERT INTO MESSAGE_LOG (RUN_ID, SEQ, SPEAKER, CONTENT, CREATED_AT) VALUES (?, ?, ?, ?, ?)",
                    (self.run_id, self._next_seq(), speaker, content, now_kst()))

    def finish(self, result: str, *, model_id: str | None = None, tool_calls: int | None = None,
               stop_reason: str | None = None) -> None:
        """실행 결과를 정한다. 끝 시각은 start_run 블록을 나갈 때 적는다."""
        self.result = result
        self._write(
            "UPDATE PROCESS_LOG SET RESULT = ?, MODEL_ID = COALESCE(?, MODEL_ID), TOOL_CALLS = COALESCE(?, TOOL_CALLS),"
            " STOP_REASON = COALESCE(?, STOP_REASON) WHERE RUN_ID = ?",
            (result, model_id, tool_calls, stop_reason, self.run_id))

    def _close(self, error: BaseException | None) -> None:
        if error is not None and self.result is None:
            self.result = f"오류: {type(error).__name__}: {error}"
            self._write("UPDATE PROCESS_LOG SET RESULT = ? WHERE RUN_ID = ?", (self.result, self.run_id))
        self._write("UPDATE PROCESS_LOG SET ENDED_AT = ? WHERE RUN_ID = ?", (now_kst(), self.run_id))


@contextmanager
def start_run(run_kind: str, user_id: str, issue_no: int | None = None, thread_id: str | None = None,
              path: Path | str | None = None):
    """실행 1회를 연다. 블록을 나가면(예외 포함) 끝 시각을 남긴다."""
    run = Run(run_kind, user_id, issue_no, thread_id, path)
    try:
        yield run
    except BaseException as e:
        run._close(e)
        raise
    run._close(None)


class Timer:
    """걸린 시간(ms) 재기: `t = Timer()` … `t.ms`."""

    def __init__(self):
        self.started = time.monotonic()

    @property
    def ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)
