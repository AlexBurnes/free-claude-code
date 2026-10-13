"""Validate Codex history at the actual SDK/HTTP boundary, without accounts."""

import json
from copy import deepcopy
from typing import Any

import httpx2
import pytest

from free_claude_code.core.anthropic.models import MessagesRequest
from free_claude_code.core.failures import ExecutionFailure
from free_claude_code.core.history_replay import ReplayRecord, encode_replay
from free_claude_code.core.openai_responses import OpenAIResponsesRequest
from free_claude_code.core.reasoning import ReasoningPolicy
from free_claude_code.core.stream_recovery import ContinuationSeed
from free_claude_code.providers.history_replay import replay_origin
from free_claude_code.providers.openai_codex.provider import OpenAICodexProvider
from tests.providers.support import immediate_admission
from tests.providers.test_openai_codex_provider import (
    _collect,
    _complete_stream,
    _config,
    _FakeAuth,
)
from tests.providers.test_openai_responses_transport import _client, _transport

MODEL = "gpt-6.1-sol"
THINKING = "Inspect the README before answering."
CIPHER = "synthetic-opaque-legacy-state"
ORIGINAL_REASONING_ID = "rs_fixture"
OTHER_CIPHER = "synthetic-other-opaque-state"
OTHER_REASONING_ID = "rs_fixture_extra"
OTHER_THINKING = "Check the second readable history record."
CALL_ID = "call_read"
ARGS = {"file_path": "/tmp/project/README.md"}


def _messages(signature: str | None, *, redacted: bool = False) -> MessagesRequest:
    thinking = {"type": "thinking", "thinking": THINKING}
    if signature is not None:
        thinking["signature"] = signature
    return MessagesRequest.model_validate(
        {
            "model": MODEL,
            "max_tokens": 1024,
            "stream": True,
            "messages": [
                {"role": "user", "content": "Read the project README."},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "redacted_thinking", "data": signature}
                        if redacted
                        else thinking,
                        {"type": "text", "text": "I will read it."},
                        {
                            "type": "tool_use",
                            "id": CALL_ID,
                            "name": "Read",
                            "input": ARGS,
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": CALL_ID,
                            "content": "README: safe fixture",
                        },
                        {"type": "text", "text": "Now summarize it."},
                    ],
                },
            ],
        }
    )


def _native() -> dict[str, Any]:
    return {
        "type": "reasoning",
        "id": ORIGINAL_REASONING_ID,
        "status": "completed",
        "content": [{"type": "reasoning_text", "text": THINKING}],
        "summary": [{"type": "summary_text", "text": "Read README."}],
        "encrypted_content": CIPHER,
        "extension": {"keep": 17},
    }


def _assert_conversation(body: dict[str, Any]) -> None:
    items = body["input"]
    assert items[0] == {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "Read the project README."}],
    }
    visible = next(
        i
        for i in items
        if i.get("role") == "assistant" and "I will read it." in json.dumps(i)
    )
    assert visible["content"] == [{"type": "output_text", "text": "I will read it."}]
    call = next(i for i in items if i.get("type") == "function_call")
    assert call["call_id"] == CALL_ID
    assert call["name"] == "Read"
    assert json.loads(call["arguments"]) == ARGS
    result = next(i for i in items if i.get("type") == "function_call_output")
    assert result == {
        "type": "function_call_output",
        "call_id": CALL_ID,
        "output": "README: safe fixture",
    }
    assert items[-1] == {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "Now summarize it."}],
    }
    assert (
        items.index(visible) < items.index(call) < items.index(result) < len(items) - 1
    )


def _strict_reply(
    request: httpx2.Request, bodies: list[dict[str, Any]]
) -> httpx2.Response:
    assert str(request.url) == "https://chatgpt.com/backend-api/codex/responses"
    body = json.loads(request.content)
    bodies.append(body)
    for index, item in enumerate(body["input"]):
        if item.get("type") == "reasoning" and item.get("content"):
            return httpx2.Response(
                400,
                json={
                    "error": {
                        "message": f"input[{index}].content: maximum length 0, got 1",
                        "type": "invalid_request_error",
                    }
                },
            )
        expected_id = {
            CIPHER: ORIGINAL_REASONING_ID,
            OTHER_CIPHER: OTHER_REASONING_ID,
        }.get(item.get("encrypted_content"))
        if (
            item.get("type") == "reasoning"
            and expected_id is not None
            and item.get("id") != expected_id
        ):
            return httpx2.Response(
                400,
                json={
                    "error": {
                        "message": (
                            f"The encrypted content for item {item.get('id', expected_id)} "
                            "could not be verified. Reason: Encrypted content item_id "
                            "did not match the target item id."
                        ),
                        "type": "invalid_request_error",
                        "code": "invalid_encrypted_content",
                        "param": None,
                    }
                },
            )
    return httpx2.Response(
        200,
        text=_complete_stream("fixture answer"),
        headers={"Content-Type": "text/event-stream"},
    )


@pytest.mark.parametrize("signature_kind", ["unsigned", "legacy", "redacted", "scoped"])
@pytest.mark.parametrize("retry", [False, True])
@pytest.mark.asyncio
async def test_messages_history_satisfies_codex_wire_schema(
    signature_kind: str,
    retry: bool,
) -> None:
    bodies: list[dict[str, Any]] = []
    auth = _FakeAuth()

    def reply(request: httpx2.Request) -> httpx2.Response:
        accepted = _strict_reply(request, bodies)
        if accepted.status_code == 200 and retry and auth.recovery_calls == 0:
            return httpx2.Response(
                401, json={"error": {"message": "synthetic expired token"}}
            )
        return accepted

    provider = OpenAICodexProvider(
        _config(),
        auth=auth,
        admission=immediate_admission(
            provider_name="OpenAI",
            max_attempts=1 + int(signature_kind in {"legacy", "redacted"}) + int(retry),
        ),
        transport=httpx2.MockTransport(reply),
    )
    native = _native()
    original_native = deepcopy(native)
    signature = None if signature_kind == "unsigned" else CIPHER
    if signature_kind == "scoped":
        endpoint = await provider._endpoint().endpoint()
        origin = replay_origin(
            "OpenAI", "responses", MODEL, client=provider._client, endpoint=endpoint
        )
        signature = encode_replay(ReplayRecord(origin, native))
    request = _messages(signature, redacted=signature_kind == "redacted")
    original = request.model_dump(mode="json")
    try:
        output = await _collect(provider.stream_messages(request, input_tokens=11))
    finally:
        await provider.cleanup()
    assert "fixture answer" in output
    assert len(bodies) == 1 + int(signature_kind in {"legacy", "redacted"}) + int(retry)
    assert auth.recovery_calls == int(retry)
    for body in bodies:
        _assert_conversation(body)
    if signature_kind in {"legacy", "redacted"}:
        rejected = bodies[0]["input"][1]
        assert rejected["encrypted_content"] == CIPHER
        assert rejected["id"] != ORIGINAL_REASONING_ID
        assert "content" not in rejected
    else:
        assert all(body["input"] == bodies[0]["input"] for body in bodies)
    reasoning = [i for i in bodies[-1]["input"] if i.get("type") == "reasoning"]
    if signature_kind in {"unsigned", "legacy"}:
        assert reasoning == []
        context = bodies[-1]["input"][1]
        assert context["role"] == "assistant"
        assert THINKING in context["content"]
        assert context["content"] == THINKING
    elif signature_kind == "redacted":
        assert reasoning == []
        assert CIPHER not in json.dumps(bodies[-1])
    else:
        assert len(reasoning) == 1
        assert reasoning[0]["encrypted_content"] == CIPHER
        assert "content" not in reasoning[0]
        assert THINKING not in json.dumps(bodies[-1])
        if signature_kind == "scoped":
            assert reasoning[0] == {k: v for k, v in native.items() if k != "content"}
    assert request.model_dump(mode="json") == original
    assert native == original_native


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("retry", [False, True])
@pytest.mark.asyncio
async def test_native_responses_restored_history_survives_retry(
    scoped: bool, retry: bool
) -> None:
    bodies: list[dict[str, Any]] = []
    auth = _FakeAuth()

    def reply(request: httpx2.Request) -> httpx2.Response:
        accepted = _strict_reply(request, bodies)
        if accepted.status_code == 200 and retry and len(bodies) == 1:
            return httpx2.Response(
                401, json={"error": {"message": "synthetic expired token"}}
            )
        return accepted

    provider = OpenAICodexProvider(
        _config(),
        auth=auth,
        admission=immediate_admission(provider_name="OpenAI", max_attempts=2),
        transport=httpx2.MockTransport(reply),
    )
    native = _native()
    inbound = deepcopy(native)
    if scoped:
        endpoint = await provider._endpoint().endpoint()
        origin = replay_origin(
            "OpenAI", "responses", MODEL, client=provider._client, endpoint=endpoint
        )
        inbound["encrypted_content"] = encode_replay(ReplayRecord(origin, native))
    history = [
        {"role": "user", "content": "Read the project README."},
        inbound,
        {
            "type": "function_call",
            "call_id": CALL_ID,
            "name": "Read",
            "arguments": json.dumps(ARGS),
        },
        {
            "type": "function_call_output",
            "call_id": CALL_ID,
            "output": "README: safe fixture",
        },
        {"role": "assistant", "content": "I will read it."},
        {"role": "user", "content": "Now summarize it."},
    ]
    request = OpenAIResponsesRequest.model_validate(
        {"model": MODEL, "input": history, "stream": True}
    )
    original = request.model_dump(mode="json")
    try:
        output = await _collect(provider.stream_responses(request))
    finally:
        await provider.cleanup()
    assert "fixture answer" in output
    assert len(bodies) == (2 if retry else 1)
    assert auth.recovery_calls == int(retry)
    for body in bodies:
        assert body["input"][1] == {k: v for k, v in native.items() if k != "content"}
        assert body["input"][:1] + body["input"][2:] == history[:1] + history[2:]
    assert request.model_dump(mode="json") == original


@pytest.mark.asyncio
async def test_continuation_reasoning_is_quoted_before_codex_request() -> None:
    bodies: list[dict[str, Any]] = []
    provider = OpenAICodexProvider(
        _config(),
        auth=_FakeAuth(),
        admission=immediate_admission(provider_name="OpenAI", max_attempts=1),
        transport=httpx2.MockTransport(lambda request: _strict_reply(request, bodies)),
    )
    request = MessagesRequest.model_validate(
        {
            "model": MODEL,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "Continue the answer."}],
        }
    )
    original = request.model_dump(mode="json")
    try:
        await _collect(
            provider.stream_messages(
                request,
                input_tokens=11,
                continuation=ContinuationSeed(
                    text="Visible partial answer.", thinking=THINKING
                ),
            )
        )
    finally:
        await provider.cleanup()
    assert len(bodies) == 1
    wire = json.dumps(bodies[0]["input"])
    assert "Continue the answer." in wire
    assert "Visible partial answer." in wire
    assert THINKING in wire
    assert not any(i.get("type") == "reasoning" for i in bodies[0]["input"])
    assert request.model_dump(mode="json") == original


@pytest.mark.asyncio
async def test_standard_responses_endpoint_keeps_supported_reasoning_content() -> None:
    bodies: list[dict[str, Any]] = []

    def reply(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        return httpx2.Response(
            200,
            text=_complete_stream("fixture answer"),
            headers={"Content-Type": "text/event-stream"},
        )

    client = _client(reply)
    try:
        await _collect(
            _transport(client, max_attempts=1).stream_messages(
                _messages(CIPHER),
                input_tokens=11,
                request_id="request_fixture",
                response_model=MODEL,
                reasoning=ReasoningPolicy(),
            )
        )
    finally:
        await client.close()
    reasoning = bodies[0]["input"][1]
    assert reasoning["content"] == [{"type": "reasoning_text", "text": THINKING}]
    assert reasoning["encrypted_content"] == CIPHER
    _assert_conversation(bodies[0])


@pytest.mark.parametrize("max_attempts", [2, 3])
@pytest.mark.asyncio
async def test_two_legacy_cipher_failures_recover_in_order_with_bounded_budget(
    max_attempts: int,
) -> None:
    bodies: list[dict[str, Any]] = []
    chunks: list[str] = []

    def reply(request: httpx2.Request) -> httpx2.Response:
        assert chunks == [], "Rejected history must recover before public delivery."
        return _strict_reply(request, bodies)

    provider = OpenAICodexProvider(
        _config(),
        auth=_FakeAuth(),
        admission=immediate_admission(
            provider_name="OpenAI", max_attempts=max_attempts
        ),
        transport=httpx2.MockTransport(reply),
    )
    raw = _messages(CIPHER).model_dump(mode="json")
    raw["messages"][1]["content"].insert(
        1, {"type": "thinking", "thinking": OTHER_THINKING, "signature": OTHER_CIPHER}
    )
    request = MessagesRequest.model_validate(raw)
    original = request.model_dump(mode="json")

    async def consume() -> None:
        nonlocal chunks
        async for chunk in provider.stream_messages(request, input_tokens=11):
            chunks += [chunk]

    try:
        if max_attempts == 2:
            with pytest.raises(ExecutionFailure) as failure:
                await consume()
            assert failure.value.status_code == 400
            assert chunks == []
        else:
            await consume()
            assert "fixture answer" in "".join(chunks)
    finally:
        await provider.cleanup()
    assert len(bodies) == max_attempts
    for body in bodies:
        _assert_conversation(body)
    first_reasoning = [i for i in bodies[0]["input"] if i.get("type") == "reasoning"]
    assert len(first_reasoning) == 2
    assert first_reasoning[0]["encrypted_content"] == CIPHER
    assert first_reasoning[1]["encrypted_content"] == OTHER_CIPHER
    assert THINKING in json.dumps(bodies[1]["input"])
    second_reasoning = [i for i in bodies[1]["input"] if i.get("type") == "reasoning"]
    assert second_reasoning == [first_reasoning[1]]
    if max_attempts == 3:
        assert not any(i.get("type") == "reasoning" for i in bodies[2]["input"])
        assert OTHER_THINKING in json.dumps(bodies[2]["input"])
        assert THINKING in json.dumps(bodies[2]["input"])
    assert request.model_dump(mode="json") == original


@pytest.mark.parametrize("ingress", ["messages", "responses"])
@pytest.mark.asyncio
async def test_targeted_cipher_recovery_keeps_other_native_pair(ingress: str) -> None:
    bodies: list[dict[str, Any]] = []
    chunks: list[str] = []

    def reply(request: httpx2.Request) -> httpx2.Response:
        assert chunks == [], "Rejected history must recover before public delivery."
        return _strict_reply(request, bodies)

    provider = OpenAICodexProvider(
        _config(),
        auth=_FakeAuth(),
        admission=immediate_admission(provider_name="OpenAI", max_attempts=2),
        transport=httpx2.MockTransport(reply),
    )
    other = _native()
    other.update(
        id=OTHER_REASONING_ID,
        encrypted_content=OTHER_CIPHER,
        content=[{"type": "reasoning_text", "text": OTHER_THINKING}],
    )
    endpoint = await provider._endpoint().endpoint()
    origin = replay_origin(
        "OpenAI", "responses", MODEL, client=provider._client, endpoint=endpoint
    )
    carrier = encode_replay(ReplayRecord(origin, other))
    if ingress == "messages":
        raw = _messages(CIPHER).model_dump(mode="json")
        raw["messages"][1]["content"].insert(
            1, {"type": "thinking", "thinking": OTHER_THINKING, "signature": carrier}
        )
        request = MessagesRequest.model_validate(raw)
        stream = provider.stream_messages(request, input_tokens=11)
    else:
        rejected = _native()
        rejected["id"] = "rs_rejected_fixture"
        request = OpenAIResponsesRequest.model_validate(
            {
                "model": MODEL,
                "input": [
                    {"role": "user", "content": "Read the project README."},
                    rejected,
                    {**other, "encrypted_content": carrier},
                    {"role": "assistant", "content": "I will read it."},
                    {
                        "type": "function_call",
                        "call_id": CALL_ID,
                        "name": "Read",
                        "arguments": json.dumps(ARGS),
                    },
                    {
                        "type": "function_call_output",
                        "call_id": CALL_ID,
                        "output": "README: safe fixture",
                    },
                    {"role": "user", "content": "Now summarize it."},
                ],
            }
        )
        stream = provider.stream_responses(request)
    original = request.model_dump(mode="json")
    try:
        async for chunk in stream:
            chunks += [chunk]
    finally:
        await provider.cleanup()
    assert "fixture answer" in "".join(chunks)
    assert len(bodies) == 2
    expected_other = {k: v for k, v in other.items() if k != "content"}
    assert bodies[0]["input"][2] == expected_other
    assert bodies[1]["input"][2] == expected_other
    expected_context = THINKING
    if ingress == "responses":
        expected_context += "\n\nRead README."
    assert bodies[1]["input"][1] == {
        "role": "assistant",
        "content": expected_context,
    }
    assert (
        bodies[1]["input"][:1] + bodies[1]["input"][2:]
        == bodies[0]["input"][:1] + bodies[0]["input"][2:]
    )
    assert request.model_dump(mode="json") == original
