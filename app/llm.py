"""LLM 호출 래퍼: 모든 LLM 호출은 FallbackLLM을 거친다.

- 모델은 BEDROCK_MODEL_IDS 순서대로 시도한다. 한 모델이 실패하면(예외·60초 초과) 재시도 없이 다음 모델로 넘어간다.
- 모든 호출은 temperature 0이다.
- 결과에는 실제로 응답한 모델 ID와 시도 목록(폴백 포함)을 담는다. 처리 기록(CALL_LOG, T10)이 이 값을 쓴다.
- 모델 하나를 부르는 일(ChatModel)과 순서대로 시도하는 일(FallbackLLM)을 나눠, 가짜 LLM(app/fake_llm.py)도
  같은 FallbackLLM 안에 끼워 폴백 경로를 그대로 시험한다.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel

from app import config

CALL_TIMEOUT_SECONDS = 60


@dataclass
class Attempt:
    """모델 1개에 보낸 호출 1회."""

    model_id: str
    ok: bool
    seconds: float
    error: str = ""


@dataclass
class LLMResult:
    """응답 값(value)과 응답한 모델 ID. chat이면 value는 AIMessage, structured면 스키마 객체."""

    value: Any
    model_id: str
    attempts: list[Attempt] = field(default_factory=list)
    raw: Any = None


class LLMError(Exception):
    """LLM 호출 실패의 공통 부모. attempts에 지금까지의 시도가 있다."""

    def __init__(self, message: str, attempts: list[Attempt]):
        super().__init__(message)
        self.attempts = attempts


class AllModelsFailed(LLMError):
    """목록의 모든 모델이 실패했다."""


class SchemaError(LLMError):
    """모델은 응답했지만 구조화 출력이 스키마로 해석되지 않았다. 다음 모델로 넘어가지 않는다."""

    def __init__(self, message: str, attempts: list[Attempt], model_id: str, raw: Any = None):
        super().__init__(message, attempts)
        self.model_id = model_id
        self.raw = raw


class ChatModel(Protocol):
    """모델 하나. 호출 1회만 하고 재시도하지 않는다. 실패하면 예외를 낸다."""

    model_id: str

    def chat(self, messages: list[BaseMessage], tools: list | None = None) -> AIMessage: ...

    def structured(self, messages: list[BaseMessage], schema: type[BaseModel]) -> tuple[BaseModel | None, Any, str]:
        """(해석된 객체 또는 None, 원본 응답, 해석 오류 문구)를 돌려준다."""
        ...


class BedrockChatModel:
    """AWS Bedrock Converse API의 모델 하나. 자격 증명은 boto3가 환경변수(.env)에서 읽는다."""

    def __init__(self, model_id: str):
        self.model_id = model_id
        self._client = None

    def _llm(self):
        if self._client is None:
            from langchain_aws import ChatBedrockConverse

            # timeout은 botocore의 connect·read 시간 제한, max_retries=0은 botocore 자체 재시도를 끈다.
            self._client = ChatBedrockConverse(
                model=self.model_id,
                temperature=0,
                timeout=CALL_TIMEOUT_SECONDS,
                max_retries=0,
                region_name=config.aws_region(),
            )
        return self._client

    def chat(self, messages, tools=None):
        llm = self._llm()
        if tools:
            llm = llm.bind_tools(tools)
        return llm.invoke(messages)

    def structured(self, messages, schema):
        out = self._llm().with_structured_output(schema, include_raw=True).invoke(messages)
        parsed = out.get("parsed")
        if isinstance(parsed, schema):
            return parsed, out.get("raw"), ""
        return None, out.get("raw"), str(out.get("parsing_error") or "구조화 출력 없음")


class FallbackLLM:
    """모델 목록을 우선순위 순서로 시도한다."""

    def __init__(self, models: list[ChatModel]):
        if not models:
            raise ValueError("모델 목록이 비어 있습니다. .env의 BEDROCK_MODEL_IDS를 확인하세요.")
        self.models = list(models)

    @property
    def model_ids(self) -> list[str]:
        return [m.model_id for m in self.models]

    def chat(self, messages: list[BaseMessage], tools: list | None = None) -> LLMResult:
        """도구 바인딩(있으면) 채팅 호출. 모든 모델이 실패하면 AllModelsFailed."""
        attempts: list[Attempt] = []
        for model in self.models:
            started = time.monotonic()
            try:
                message = model.chat(messages, tools)
            except Exception as e:  # 어떤 오류든 이 모델은 실패로 보고 다음 모델로 간다.
                attempts.append(Attempt(model.model_id, False, time.monotonic() - started, _describe(e)))
                continue
            attempts.append(Attempt(model.model_id, True, time.monotonic() - started))
            return LLMResult(message, model.model_id, attempts, raw=message)
        raise AllModelsFailed(_all_failed_text(attempts), attempts)

    def structured(self, messages: list[BaseMessage], schema: type[BaseModel]) -> LLMResult:
        """구조화 출력 호출. 호출 오류는 다음 모델로, 스키마 해석 실패는 SchemaError로 바로 끝낸다."""
        attempts: list[Attempt] = []
        for model in self.models:
            started = time.monotonic()
            try:
                parsed, raw, parse_error = model.structured(messages, schema)
            except Exception as e:
                attempts.append(Attempt(model.model_id, False, time.monotonic() - started, _describe(e)))
                continue
            seconds = time.monotonic() - started
            if parsed is None:
                attempts.append(Attempt(model.model_id, False, seconds, f"스키마 오류: {parse_error}"))
                raise SchemaError(f"{model.model_id} 응답이 스키마로 해석되지 않음: {parse_error}",
                                  attempts, model.model_id, raw)
            attempts.append(Attempt(model.model_id, True, seconds))
            return LLMResult(parsed, model.model_id, attempts, raw=raw)
        raise AllModelsFailed(_all_failed_text(attempts), attempts)


def _describe(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"


def _all_failed_text(attempts: list[Attempt]) -> str:
    return "모든 모델 실패: " + "; ".join(f"{a.model_id} → {a.error}" for a in attempts)


def get_llm() -> FallbackLLM:
    """LLM_MODE에 맞는 LLM을 만든다. fake(기본)는 데모용 가짜 LLM, bedrock은 BEDROCK_MODEL_IDS 순서의 실제 모델."""
    if config.llm_mode() == "bedrock":
        return FallbackLLM([BedrockChatModel(m) for m in config.bedrock_model_ids()])
    from app.fake_llm import demo_llm

    return demo_llm()
