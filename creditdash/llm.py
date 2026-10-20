"""The only place creditdash talks to an LLM: DeepSeek's OpenAI-style chat-completions, as in research-relay.

    obj, info = ask(prompt, system, MySchema, settings=settings)

JSON mode guarantees valid JSON; the schema in the system prompt says which JSON, and pydantic checks it.
Identical requests are served from a disk cache ($CREDITDASH_HOME/cache/llm/); every call is logged with
its cost to $CREDITDASH_HOME/llm_log.jsonl.
"""
from __future__ import annotations

import hashlib
import json
import time

import httpx
from pydantic import BaseModel, ValidationError

from .config import Settings
from .errors import LlmError

MAX_ATTEMPTS = 2
MAX_TOKENS = 16000  # includes hidden reasoning tokens
TIMEOUT_S = 180

# USD per 1M tokens: (input cache hit, input cache miss, output).
PRICES = {
    "deepseek-flash": (0.006, 0.3, 1.2),
    "deepseek-v4-pro": (0.044, 1.32, 3.96),
}


def ask(prompt: str, system: str, schema: type[BaseModel], *, settings: Settings,
        transport: httpx.BaseTransport | None = None, use_cache: bool = True) -> tuple[BaseModel, dict]:
    if not settings.llm_api_key:
        raise LlmError("LLM_API_KEY not set (see .env.example)")
    start = time.time()
    request = build_request(prompt, system, schema, settings.llm_model)
    chars = sum(len(m["content"]) for m in request["messages"])
    if chars / 4 > settings.llm_max_input_tokens:
        raise LlmError(f"input is ~{chars // 4} tokens, over LLM_MAX_INPUT_TOKENS={settings.llm_max_input_tokens}")

    key = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    cache_path = settings.home / "cache" / "llm" / f"{key}.json"
    cached = use_cache and cache_path.exists()
    if cached:  # a hit costs nothing: usage is zeroed so cost_usd() reports 0
        hit = json.loads(cache_path.read_text())
        result, finish_reason, usage = schema.model_validate_json(hit["output"]), hit["finish_reason"], {}
    else:
        result, finish_reason, usage = _call_with_retries(request, schema, settings, transport)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({"output": result.model_dump_json(), "finish_reason": finish_reason,
                                          "usage": usage}))

    info = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": settings.llm_model,
        "tokens_in": usage.get("prompt_tokens", 0),
        "tokens_out": usage.get("completion_tokens", 0),
        "cost_usd": round(cost_usd(settings.llm_model, usage), 6),
        "latency_ms": int((time.time() - start) * 1000),
        "finish_reason": finish_reason,
        "cached": cached,
    }
    _log(settings, info)
    return result, info


def build_request(prompt: str, system: str, schema: type[BaseModel], model: str) -> dict:
    schema_json = schema.llm_schema() if hasattr(schema, "llm_schema") else schema.model_json_schema()
    system = f"{system}\n\nReply with only a JSON object matching this JSON schema:\n{json.dumps(schema_json)}"
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system.strip()}, {"role": "user", "content": prompt}],
    }


def _call_with_retries(request: dict, schema: type[BaseModel], settings: Settings,
                       transport: httpx.BaseTransport | None) -> tuple[BaseModel, str, dict]:
    """A reply that doesn't match `schema` counts as transient: the model usually gets it right next time."""
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = _call_api(request, settings, transport)
            choice = response["choices"][0]
            return (schema.model_validate_json(choice["message"]["content"] or ""), choice["finish_reason"],
                    response.get("usage", {}))
        except Exception as e:
            if attempt == MAX_ATTEMPTS - 1 or not _retryable(e):
                raise LlmError(f"{type(e).__name__}: {e}"[:500]) from e
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def _call_api(request: dict, settings: Settings, transport: httpx.BaseTransport | None) -> dict:
    with httpx.Client(transport=transport, timeout=TIMEOUT_S) as client:
        r = client.post(f"{settings.llm_base_url}/chat/completions", json=request,
                        headers={"Authorization": f"Bearer {settings.llm_api_key}"})
    r.raise_for_status()
    return r.json()


def _retryable(e: Exception) -> bool:
    if isinstance(e, httpx.HTTPStatusError):  # rate limit or server error
        return e.response.status_code == 429 or e.response.status_code >= 500
    return isinstance(e, (httpx.TransportError, ValidationError))


def cost_usd(model: str, usage: dict) -> float:
    hit, miss, out = PRICES.get(model, (0, 0, 0))
    return (usage.get("prompt_cache_hit_tokens", 0) * hit + usage.get("prompt_cache_miss_tokens", 0) * miss
            + usage.get("completion_tokens", 0) * out) / 1_000_000


def _log(settings: Settings, info: dict) -> None:
    settings.home.mkdir(parents=True, exist_ok=True)
    with (settings.home / "llm_log.jsonl").open("a") as f:
        f.write(json.dumps(info) + "\n")
