"""Frozen named sessions retain fallback key/effort records at HTTP egress."""

import asyncio
import json

import httpx
import httpx2
import pytest

from free_claude_code.api.app import create_app
from free_claude_code.config.profiles import ProfileStore
from free_claude_code.config.settings import Settings
from tests.api.support import create_test_app
from tests.api.test_delivered_stream_recovery import text_events

CUSTOM_ID = "custom_" + "e" * 32
MODEL = CUSTOM_ID + "/same-model"
KEYS = ["synthetic-primary", "synthetic-second", "synthetic-third"]
AUTH = {"Authorization": "Bearer synthetic-session-token"}


@pytest.mark.asyncio
async def test_overlapping_bound_sessions_preserve_fallback_keys_and_efforts_after_edits(
    tmp_path,
    monkeypatch,
):
    calls = []
    entered, release = asyncio.Event(), asyncio.Event()
    hold = True
    primary_calls = 0

    async def reply(request, library):
        nonlocal primary_calls
        if request.method == "GET":
            return library.Response(200, json={"data": []})
        token = request.headers["authorization"].removeprefix("Bearer ")
        body = json.loads(request.content)
        calls.append((token, body))
        if token == KEYS[0]:
            primary_calls += 1
            if hold:
                if primary_calls == 2:
                    entered.set()
                await asyncio.wait_for(release.wait(), 5)
            return library.Response(
                403,
                json={
                    "error": {
                        "type": "permission_error",
                        "message": "Synthetic primary unavailable",
                    }
                },
            )
        label = "second key" if token == KEYS[1] else "third key"
        events = text_events("chat", label)
        payload = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        return library.Response(
            200, text=payload, headers={"content-type": "text/event-stream"}
        )

    for library in (httpx, httpx2):
        original_init = library.AsyncClient.__init__

        async def local_reply(request, _library=library):
            return await reply(request, _library)

        def local_http(
            self,
            *args,
            _library=library,
            _init=original_init,
            _handler=local_reply,
            **kwargs,
        ):
            if not isinstance(kwargs.get("transport"), httpx.ASGITransport):
                kwargs["transport"] = _library.MockTransport(_handler)
            _init(self, *args, **kwargs)

        monkeypatch.setattr(library.AsyncClient, "__init__", local_http)

    settings = Settings(
        MODEL=MODEL,
        ANTHROPIC_AUTH_TOKEN="synthetic-session-token",
        PROVIDER_RATE_LIMIT=1000,
        PROVIDER_RATE_WINDOW=1,
        FCC_CUSTOM_PROVIDERS=[
            {
                "provider_id": CUSTOM_ID,
                "display_name": "Fixture gateway",
                "base_url": "https://fixture.invalid/v1",
                "api_format": "openai_chat",
                "reasoning_format": "openai_effort",
                "api_keys": KEYS,
            }
        ],
    )
    settings.proxy_auth_enabled = True
    original = create_test_app(settings)
    app = create_app(
        original.state.services, profile_store=ProfileStore(tmp_path / "profiles.json")
    )
    paths = {}
    expected = {"alpha": (2, "low"), "beta": (3, "high")}

    def configuration(number, effort):
        return {
            "model": MODEL,
            "model_fallbacks": [
                {
                    "model": MODEL,
                    "api_key_number": number,
                    "reasoning": effort,
                }
            ],
        }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 50000)),
        base_url="http://127.0.0.1",
    ) as client:
        for name, (number, effort) in expected.items():
            created = await client.post(
                "/admin/api/profiles",
                json={
                    "name": name,
                    "configuration": configuration(number, effort),
                },
            )
            assert created.status_code == 201, created.text
            bound = await client.post(
                "/api/launch-sessions", headers=AUTH, json={"profile": name}
            )
            assert bound.status_code == 201, bound.text
            paths[name] = bound.json()["base_path"]

        async def infer(path):
            return await client.post(
                path + "/v1/messages",
                headers=AUTH,
                json={
                    "model": "claude-sonnet",
                    "messages": [{"role": "user", "content": "Keep this text"}],
                    "max_tokens": 64,
                    "stream": True,
                },
            )

        tasks = [asyncio.create_task(infer(path)) for path in paths.values()]
        try:
            await asyncio.wait_for(entered.wait(), 5)
            changed = await client.put(
                "/admin/api/profiles/beta",
                json={"configuration": configuration(2, "low")},
            )
            assert changed.status_code == 200, changed.text
            assert (await client.delete("/admin/api/profiles/alpha")).status_code == 204
        finally:
            release.set()
            responses = await asyncio.gather(*tasks, return_exceptions=True)
        for response, label in zip(responses, ["second key", "third key"], strict=True):
            assert isinstance(response, httpx.Response), repr(response)
            assert response.status_code == 200, response.text
            assert label in response.text
        hold = False
        calls.clear()
        for name, path in paths.items():
            response = await infer(path)
            assert response.status_code == 200, response.text
            number, effort = expected[name]
            assert calls[-1][0] == KEYS[number - 1]
            assert calls[-1][1]["reasoning_effort"] == effort
            assert all(key not in response.text for key in KEYS)
        new_session = await client.post(
            "/api/launch-sessions", headers=AUTH, json={"profile": "beta"}
        )
        assert new_session.status_code == 201, new_session.text
        response = await infer(new_session.json()["base_path"])
        assert response.status_code == 200, response.text
        assert calls[-1][0] == KEYS[1]
        assert calls[-1][1]["reasoning_effort"] == "low"
        assert all(body["model"] == "same-model" for _, body in calls)
        assert all(
            "Keep this text" in json.dumps(body["messages"]) for _, body in calls
        )
