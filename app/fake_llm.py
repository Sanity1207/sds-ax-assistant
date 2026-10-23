"""가짜 LLM: Bedrock을 부르지 않고 ChatModel 인터페이스를 흉내 낸다.

- FakeChatModel: 테스트용. 정해 둔 응답(script)을 호출 순서대로 돌려준다. chat·structured 호출을 합쳐 센다.
  응답 항목은 AIMessage(chat), BaseModel·dict(structured, dict는 스키마로 검사), str(structured에서는 스키마 오류),
  Exception(그 호출 실패), 또는 (messages, tools_or_schema)를 받아 위 값 중 하나를 돌려주는 함수다.
  error를 주면 모든 호출이 그 예외로 실패한다(모델 장애 흉내).
- DemoChatModel: LLM_MODE=fake로 앱을 띄울 때 쓰는 규칙 기반 가짜. 분류는 늘 '매핑 생성', 비서는 입력에
  이슈 번호(#N)가 있으면 get_issue, 아니면 find_vessel을 한 번 부른 뒤 그 결과를 답한다.
"""

import re
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel, ValidationError

from app.llm import FallbackLLM


class FakeLLMError(Exception):
    """가짜 모델이 실패할 때 내는 기본 예외."""


def tool_call(name: str, args: dict, call_id: str | None = None) -> AIMessage:
    """도구 호출 하나를 담은 AIMessage를 만든다."""
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id or f"call_{name}"}])


class FakeChatModel:
    def __init__(self, model_id: str = "fake-1", script: Sequence[Any] = (), error: Exception | None = None):
        self.model_id = model_id
        self.script = list(script)
        self.error = error
        self.calls: list[tuple[str, list, Any]] = []

    def _next(self, kind: str, messages, extra):
        self.calls.append((kind, list(messages), extra))
        if self.error is not None:
            raise self.error
        if not self.script:
            raise FakeLLMError(f"{self.model_id}: 정해 둔 응답을 모두 썼습니다.")
        item = self.script.pop(0)
        if callable(item) and not isinstance(item, (BaseModel, type)):
            item = item(messages, extra)
        if isinstance(item, Exception):
            raise item
        return item

    def chat(self, messages, tools=None):
        item = self._next("chat", messages, tools)
        return item if isinstance(item, AIMessage) else AIMessage(content=str(item))

    def structured(self, messages, schema):
        item = self._next("structured", messages, schema)
        if isinstance(item, schema):
            return item, item, ""
        if isinstance(item, dict):
            try:
                return schema.model_validate(item), item, ""
            except ValidationError as e:
                return None, item, str(e)
        return None, item, f"스키마로 해석할 수 없는 응답: {item!r}"


def fake_llm(*models: FakeChatModel) -> FallbackLLM:
    return FallbackLLM(list(models))


_VESSEL_ID = re.compile(r"\bV\d{3}\b", re.IGNORECASE)
_ISSUE_NO = re.compile(r"#(\d+)")


class DemoChatModel:
    """앱 데모용 가짜 모델. 호출마다 입력을 보고 응답을 만든다."""

    model_id = "fake-demo"

    def chat(self, messages, tools=None):
        last_human = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
        results = [m for m in messages[last_human + 1:] if isinstance(m, ToolMessage)]
        if results:
            body = "\n".join(str(m.content) for m in results)
            return AIMessage(content=f"(가짜 LLM) 조사 결과입니다.\n{body}")
        names = {getattr(t, "name", None) for t in tools or []}
        text = str(messages[last_human].content) if last_human >= 0 else ""
        issue = _ISSUE_NO.search(text)
        if issue and "get_issue" in names:
            return tool_call("get_issue", {"issue_no": int(issue.group(1))})
        if "find_vessel" in names and last_human >= 0:
            found = _VESSEL_ID.search(text)
            words = text.split()
            query = found.group(0).upper() if found else (words[0] if words else "")
            return tool_call("find_vessel", {"query": query})
        return AIMessage(content="(가짜 LLM) 도구 없이 답할 수 있는 내용이 없습니다.")

    def structured(self, messages, schema):
        values = {"type": "매핑 생성", "master_target": None, "multiple_requests": False,
                  "id_mismatch": False, "reason": "(가짜 LLM) 매핑 생성 요청으로 가정"}
        parsed = schema.model_validate(values)
        return parsed, values, ""


def demo_llm() -> FallbackLLM:
    return FallbackLLM([DemoChatModel()])

