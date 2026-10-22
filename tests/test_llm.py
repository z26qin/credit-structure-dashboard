import json

import httpx
import pytest
from pydantic import BaseModel

from creditdash import llm
from creditdash.errors import LlmError
from tests.conftest import with_settings


class Labels(BaseModel):
    labels: str


def reply(content: str, usage: dict | None = None) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                                     "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5}})


class Recorder(httpx.MockTransport):
    def __init__(self, replies):
        self.requests: list[httpx.Request] = []
        replies = iter(replies)
        super().__init__(lambda r: self.requests.append(r) or next(replies))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)


def test_request_body(settings):
    t = Recorder([reply('{"labels": "draft"}')])
    obj, info = llm.ask("question", "be precise", Labels, settings=settings, transport=t)
    assert obj == Labels(labels="draft") and info["cached"] is False
    req = t.requests[0]
    body = json.loads(req.content)
    assert str(req.url) == "https://api.deepseek.com/chat/completions"
    assert req.headers["Authorization"] == "Bearer k"
    assert body["response_format"] == {"type": "json_object"} and body["max_tokens"] == 32000
    assert body["model"] == "deepseek-flash" and body["reasoning_effort"] == "low" and "thinking" not in body
    assert body["messages"][0]["role"] == "system" and "Reply with only a JSON object" in body["messages"][0]["content"]
    assert body["messages"][1] == {"role": "user", "content": "question"}


def test_retry_invalid_then_ok(settings):
    t = Recorder([reply("{}"), reply('{"labels": "ok"}')])
    obj, _ = llm.ask("q", "", Labels, settings=settings, transport=t)
    assert obj.labels == "ok" and len(t.requests) == 2


def cut_off() -> httpx.Response:
    """What deepseek-flash returns when hidden reasoning uses the whole max_tokens budget."""
    return httpx.Response(200, json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                                     "usage": {"completion_tokens": 32000,
                                               "completion_tokens_details": {"reasoning_tokens": 32000}}})


def test_cut_off_retries_without_thinking(settings):
    t = Recorder([cut_off(), reply('{"labels": "ok"}')])
    obj, _ = llm.ask("q", "", Labels, settings=settings, transport=t)
    assert obj.labels == "ok"
    retry = json.loads(t.requests[1].content)
    assert retry["thinking"] == {"type": "disabled"} and "reasoning_effort" not in retry


def test_cut_off_twice_names_the_cause(settings):
    t = Recorder([cut_off(), cut_off()])
    with pytest.raises(LlmError, match="finish_reason=length"):
        llm.ask("q", "", Labels, settings=settings, transport=t)


def test_two_failures_raise(settings):
    t = Recorder([httpx.Response(500), httpx.Response(500)])
    with pytest.raises(LlmError):
        llm.ask("q", "", Labels, settings=settings, transport=t)
    assert len(t.requests) == 2


def test_client_error_not_retried(settings):
    t = Recorder([httpx.Response(401)])
    with pytest.raises(LlmError, match="401"):
        llm.ask("q", "", Labels, settings=settings, transport=t)
    assert len(t.requests) == 1


def test_cache_hit(settings):
    t = Recorder([reply('{"labels": "x"}')])
    llm.ask("q", "s", Labels, settings=settings, transport=t)
    obj, info = llm.ask("q", "s", Labels, settings=settings, transport=t)
    assert obj.labels == "x" and len(t.requests) == 1
    assert info["cached"] is True and info["cost_usd"] == 0
    log = (settings.home / "llm_log.jsonl").read_text().splitlines()
    assert len(log) == 2 and json.loads(log[1])["cached"] is True


def test_oversize_refused(settings):
    t = Recorder([])
    with pytest.raises(LlmError, match="LLM_MAX_INPUT_TOKENS"):
        llm.ask("x" * 400_004, "", Labels, settings=settings, transport=t)
    assert t.requests == []


def test_missing_key(settings):
    with pytest.raises(LlmError, match="LLM_API_KEY"):
        llm.ask("q", "", Labels, settings=with_settings(settings, llm_api_key=None), transport=Recorder([]))


def test_cost():
    usage = {"prompt_cache_miss_tokens": 1_000_000, "completion_tokens": 1_000_000}
    assert llm.cost_usd("deepseek-flash", usage) == pytest.approx(1.5)


def test_uses_llm_schema_when_defined(settings):
    class WithSchema(Labels):
        @classmethod
        def llm_schema(cls):
            return {"custom": True}

    t = Recorder([reply('{"labels": "x"}')])
    llm.ask("q", "", WithSchema, settings=settings, transport=t)
    assert '{"custom": true}' in json.loads(t.requests[0].content)["messages"][0]["content"]


@pytest.mark.parametrize("key", ["CRWV 的债务情况怎么样", "sk-abc”", "sk-abc def"])
def test_key_that_cannot_be_a_header_is_named_not_sent(settings, key):
    t = Recorder([])
    with pytest.raises(LlmError, match="does not look like an API key") as e:
        llm.ask("q", "", Labels, settings=with_settings(settings, llm_api_key=key), transport=t)
    assert key not in str(e.value) and t.requests == []
