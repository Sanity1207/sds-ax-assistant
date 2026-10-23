import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from app.fake_llm import FakeChatModel, FakeLLMError, fake_llm, tool_call
from app.llm import AllModelsFailed, BedrockChatModel, FallbackLLM, SchemaError, get_llm

MESSAGES = [HumanMessage("안녕")]


class Answer(BaseModel):
    value: int


def test_first_model_fails_second_answers():
    first = FakeChatModel("fake-1", error=FakeLLMError("1순위 장애"))
    second = FakeChatModel("fake-2", [AIMessage("2순위 응답")])
    result = fake_llm(first, second).chat(MESSAGES)
    assert result.model_id == "fake-2"
    assert result.value.content == "2순위 응답"
    assert [(a.model_id, a.ok) for a in result.attempts] == [("fake-1", False), ("fake-2", True)]
    assert "1순위 장애" in result.attempts[0].error
    # 재시도 없이 다음 모델로: 1순위는 정확히 1번만 불렸다.
    assert len(first.calls) == 1 and len(second.calls) == 1


def test_all_models_fail():
    llm = fake_llm(FakeChatModel("fake-1", error=FakeLLMError("a")),
                   FakeChatModel("fake-2", error=TimeoutError("60초 초과")))
    with pytest.raises(AllModelsFailed) as info:
        llm.chat(MESSAGES)
    assert [a.model_id for a in info.value.attempts] == ["fake-1", "fake-2"]
    assert "TimeoutError" in str(info.value)


def test_tool_call_passes_through():
    model = FakeChatModel("fake-1", [tool_call("find_vessel", {"query": "혼합"})])
    result = fake_llm(model).chat(MESSAGES, tools=["dummy"])
    assert result.value.tool_calls[0]["name"] == "find_vessel"
    assert model.calls[0][2] == ["dummy"]


def test_structured_ok_and_fallback():
    llm = fake_llm(FakeChatModel("fake-1", error=FakeLLMError("x")), FakeChatModel("fake-2", [{"value": 3}]))
    result = llm.structured(MESSAGES, Answer)
    assert result.value == Answer(value=3)
    assert result.model_id == "fake-2"


def test_structured_schema_error_does_not_fall_back():
    second = FakeChatModel("fake-2", [{"value": 1}])
    llm = fake_llm(FakeChatModel("fake-1", [{"value": "숫자 아님"}]), second)
    with pytest.raises(SchemaError) as info:
        llm.structured(MESSAGES, Answer)
    assert info.value.model_id == "fake-1"
    assert second.calls == []


def test_empty_model_list_rejected():
    with pytest.raises(ValueError):
        FallbackLLM([])


def test_get_llm_modes(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "fake")
    assert get_llm().model_ids == ["fake-demo"]
    monkeypatch.setenv("LLM_MODE", "bedrock")
    monkeypatch.setenv("BEDROCK_MODEL_IDS", "m-1, m-2")
    llm = get_llm()
    assert llm.model_ids == ["m-1", "m-2"]
    assert all(isinstance(m, BedrockChatModel) for m in llm.models)


def test_bedrock_client_settings():
    # 실제 호출 없이 클라이언트 설정만 확인한다(temperature 0, 60초, botocore 재시도 없음).
    client = BedrockChatModel("us.anthropic.claude-sonnet-4-6")._llm()
    assert client.temperature == 0
    assert client.timeout == 60
    assert client.max_retries == 0
    boto = client.client.meta.config
    assert boto.read_timeout == 60
    assert boto.retries["total_max_attempts"] == 1
