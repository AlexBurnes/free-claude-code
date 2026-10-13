"""Provider corrections target explicit history failures, never arbitrary 400s."""

from copy import deepcopy
from typing import Any

import httpx2
import pytest
from openai import BadRequestError

from free_claude_code.providers.endpoint_types import HttpEndpoint
from free_claude_code.providers.history_replay import history_retry_body, replay_origin


def _error(message, *, code="invalid_request_error", param=None, status=400):
    return BadRequestError(
        message,
        response=httpx2.Response(
            status, request=httpx2.Request("POST", "https://example.invalid")
        ),
        body={"error": {"message": message, "code": code, "param": param}},
    )


def test_legacy_invalid_id_then_ciphertext_rejection_keeps_original():
    body = {
        "model": "synthetic",
        "input": [
            {"role": "user", "content": "hello"},
            {
                "type": "reasoning",
                "id": "rs_a:rs_b",
                "encrypted_content": "opaque-a",
                "summary": [{"type": "summary_text", "text": "look up 17"}],
            },
            {"role": "assistant", "content": "17"},
            {"role": "user", "content": "continue"},
        ],
    }
    original = deepcopy(body)
    fixed = history_retry_body(
        _error(
            "Invalid 'input[1].id': 'rs_a:rs_b'. Expected an ID that contains letters, numbers, underscores, or dashes",
            code="invalid_value",
            param="input[1].id",
        ),
        body,
        "responses",
    )
    assert fixed is not None
    assert ":" not in fixed["input"][1]["id"]
    assert fixed["input"][1]["encrypted_content"] == "opaque-a"
    cleaned = history_retry_body(
        _error(
            "The encrypted content could not be verified.",
            code="invalid_encrypted_content",
        ),
        fixed,
        "responses",
    )
    assert cleaned is not None
    assert cleaned["input"][1] == {
        "role": "assistant",
        "content": "look up 17",
    }
    assert cleaned["input"][2:] == original["input"][2:]
    assert body == original


@pytest.mark.parametrize(
    "message,code",
    [
        ("Invalid request", "invalid_request_error"),
        ("Tool schema has invalid parameters", "invalid_request_error"),
        ("Too many tokens", "context_length_exceeded"),
    ],
)
def test_unrelated_validation_does_not_drop_history(message, code):
    body = {
        "input": [{"type": "reasoning", "id": "rs_a", "encrypted_content": "opaque"}]
    }
    assert history_retry_body(_error(message, code=code), body, "responses") is None


def test_unidentified_cipher_failure_with_multiple_records_is_not_guessed():
    body = {
        "input": [
            {"type": "reasoning", "id": f"rs_{i}", "encrypted_content": f"opaque{i}"}
            for i in range(2)
        ]
    }
    assert (
        history_retry_body(
            _error(
                "Cannot decrypt encrypted content", code="invalid_encrypted_content"
            ),
            body,
            "responses",
        )
        is None
    )


@pytest.mark.parametrize("rejected_id", ["rs_fixture", "rs_fixture_extra"])
def test_unquoted_cipher_item_id_rejection_repairs_only_exact_record(rejected_id):
    body = {
        "input": [
            {
                "type": "reasoning",
                "id": item_id,
                "encrypted_content": f"opaque-{item_id}",
                "content": [{"type": "reasoning_text", "text": f"Readable {item_id}"}],
                "summary": [],
            }
            for item_id in ["rs_fixture", "rs_fixture_extra"]
        ]
        + [{"role": "user", "content": "Keep user history."}],
    }
    original = deepcopy(body)
    fixed = history_retry_body(
        _error(
            f"The encrypted content for item {rejected_id} could not be verified. "
            "Reason: Encrypted content item_id did not match the target item id.",
            code="invalid_encrypted_content",
        ),
        body,
        "responses",
    )
    assert fixed is not None
    rejected_index = 0 if rejected_id == "rs_fixture" else 1
    assert fixed["input"][rejected_index] == {
        "role": "assistant",
        "content": f"Readable {rejected_id}",
    }
    assert fixed["input"][1 - rejected_index] == original["input"][1 - rejected_index]
    assert fixed["input"][-1] == original["input"][-1]
    assert body == original


def test_unknown_unquoted_cipher_item_id_does_not_repair_single_other_record():
    body = {
        "input": [
            {"type": "reasoning", "id": "rs_known", "encrypted_content": "opaque"}
        ]
    }
    original = deepcopy(body)
    assert (
        history_retry_body(
            _error(
                "The encrypted content for item rs_unknown could not be verified. "
                "Reason: Encrypted content item_id did not match the target item id.",
                code="invalid_encrypted_content",
            ),
            body,
            "responses",
        )
        is None
    )
    assert body == original


@pytest.mark.parametrize("mismatch", ["id", "encrypted_content", "ambiguous"])
def test_readable_recovery_source_requires_unique_wire_id_and_cipher(mismatch):
    wire: dict[str, Any] = {
        "input": [
            {"type": "reasoning", "id": "rs_fixture", "encrypted_content": "opaque"}
        ]
    }
    readable = deepcopy(wire)
    readable["input"][0]["content"] = [
        {"type": "reasoning_text", "text": "Readable source must not be guessed."}
    ]
    if mismatch == "ambiguous":
        readable["input"].append(deepcopy(readable["input"][0]))
    else:
        readable["input"][0][mismatch] = "other-identity"
    original_wire = deepcopy(wire)
    original_readable = deepcopy(readable)
    assert (
        history_retry_body(
            _error(
                "The encrypted content for item rs_fixture could not be verified. "
                "Reason: Encrypted content item_id did not match the target item id.",
                code="invalid_encrypted_content",
            ),
            wire,
            "responses",
            readable_body=readable,
        )
        is None
    )
    assert wire == original_wire
    assert readable == original_readable


def test_missing_reference_targets_its_item_only():
    body = {
        "input": [
            {"type": "reasoning", "id": f"rs_{i}", "encrypted_content": f"opaque{i}"}
            for i in range(2)
        ]
    }
    result = history_retry_body(
        _error("Referenced reasoning item 'rs_1' was not found or has expired."),
        body,
        "responses",
    )
    assert result is not None
    assert result["input"] == [body["input"][0]]


def test_messages_signature_path_retains_readable_text_and_other_blocks():
    body = {
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "read the file",
                        "signature": "old-signature",
                    },
                    {"type": "text", "text": "answer"},
                ],
            }
        ]
    }
    result = history_retry_body(
        _error(
            "messages.0.content.0: Invalid signature in thinking block. The block is bound to a different conversation."
        ),
        body,
        "messages",
    )
    assert result is not None
    assert result["messages"][0]["content"] == [
        {"type": "text", "text": "read the file"},
        body["messages"][0]["content"][1],
    ]


def test_origin_does_not_expose_credentials_and_survives_account_token_refresh():
    first = replay_origin(
        "provider",
        "responses",
        "actual",
        endpoint=HttpEndpoint(
            "https://example.org/v1",
            {"Authorization": "Bearer secret1"},
            account_id="account1",
        ),
    )
    refreshed = replay_origin(
        "provider",
        "responses",
        "different-model",
        endpoint=HttpEndpoint(
            "https://example.org/v1",
            {"Authorization": "Bearer secret2"},
            account_id="account1",
        ),
    )
    other = replay_origin(
        "provider",
        "responses",
        "actual",
        endpoint=HttpEndpoint(
            "https://example.org/v1",
            {"Authorization": "Bearer secret3"},
            account_id="account2",
        ),
    )
    assert first.accepts(refreshed)
    assert not first.accepts(other)
    assert "secret" not in repr(first) and "account1" not in repr(first)


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_non_validation_failures_cannot_discard_history(status):
    body = {"input": [{"type": "reasoning", "encrypted_content": "opaque"}]}
    assert (
        history_retry_body(
            _error(
                "Could not verify encrypted content",
                code="invalid_encrypted_content",
                status=status,
            ),
            body,
            "responses",
        )
        is None
    )
