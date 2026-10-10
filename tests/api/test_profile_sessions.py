"""Independent HTTP checks for persistent profiles and immutable launch sessions."""

import asyncio
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread
from time import monotonic
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from free_claude_code.api import profile_routes
from free_claude_code.api.app import create_app
from free_claude_code.api.profile_routes import ProfileSessions
from free_claude_code.application.model_metadata import ProviderModelInfo
from free_claude_code.application.ports import ModelCatalogSnapshot
from free_claude_code.config.profiles import ProfileNotFoundError, ProfileStore
from free_claude_code.config.settings import Settings
from free_claude_code.core.gateway_model_ids import (
    desktop_model_id,
    gateway_model_id,
    no_thinking_gateway_model_id,
)
from tests.api.model_fallback_support import (
    execution_failure,
    responses_text_stream,
    text_stream,
)
from tests.api.support import create_test_app, provider_manager_for_app

TOKEN = "profile-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def profile_app(tmp_path):
    settings = Settings(
        MODEL="nvidia_nim/global-default",
        MODEL_SONNET="groq/global-sonnet",
        ANTHROPIC_AUTH_TOKEN=TOKEN,
        NVIDIA_NIM_API_KEY="synthetic-test-key",
        GROQ_API_KEY="synthetic-test-key",
    )
    settings.proxy_auth_enabled = True
    original = create_test_app(settings)
    return create_app(
        original.state.services, profile_store=ProfileStore(tmp_path / "profiles.json")
    )


def create_profile(client, name, configuration):
    response = client.post(
        "/admin/api/profiles", json={"name": name, "configuration": configuration}
    )
    assert response.status_code == 201, response.text
    return response.json()


def mint(client, name):
    response = client.post("/api/launch-sessions", headers=AUTH, json={"profile": name})
    assert response.status_code == 201, response.text
    return response.json()


def messages(model="claude-sonnet", *, stream=False):
    return {
        "model": model,
        "messages": [{"role": "user", "content": "Explain profile routing"}],
        "max_tokens": 32,
        "stream": stream,
    }


def test_profile_crud_persists_without_credentials_and_sessions_do_not_restart(
    tmp_path,
):
    app = profile_app(tmp_path)
    with TestClient(
        app, client=("127.0.0.1", 50000), base_url="http://127.0.0.1"
    ) as client:
        listed = client.get("/admin/api/profiles")
        assert listed.status_code == 200
        assert listed.json()["profiles"][0]["name"] == "default"
        assert listed.json()["profiles"][0]["implicit"] is True
        create_profile(client, "deep-work", {"model": "nvidia_nim/deep"})
        assert (
            client.post(
                "/admin/api/profiles",
                json={"name": "deep-work", "configuration": {"model": "groq/other"}},
            ).status_code
            == 409
        )
        copied = client.post(
            "/admin/api/profiles/deep-work/duplicate", json={"name": "copy"}
        )
        assert copied.status_code == 201, copied.text
        assert copied.json()["configuration"]["model"] == "nvidia_nim/deep"
        assert (
            client.put(
                "/admin/api/profiles/copy",
                json={
                    "configuration": {
                        "model": "groq/revised",
                        "reasoning_policy": "off",
                    }
                },
            ).status_code
            == 200
        )
        session = mint(client, "deep-work")
        assert TOKEN not in str(session)
        assert client.delete("/admin/api/profiles/deep-work").status_code == 204
        assert (
            client.get(
                session["base_path"] + "/v1/models?view=messages", headers=AUTH
            ).status_code
            == 200
        )
    persisted = (tmp_path / "profiles.json").read_text()
    assert TOKEN not in persisted
    assert "deep-work" not in persisted
    with TestClient(
        profile_app(tmp_path), client=("127.0.0.1", 50000), base_url="http://127.0.0.1"
    ) as restarted:
        profile = restarted.get("/admin/api/profiles/copy")
        assert profile.status_code == 200
        assert profile.json()["configuration"]["model"] == "groq/revised"
        assert (
            restarted.get(session["base_path"] + "/v1/models", headers=AUTH).status_code
            == 404
        )


@pytest.mark.parametrize(
    "name", ["", "Default", "default", "../escape", "a/b", "a b", "a" * 65]
)
def test_invalid_profile_names_rejected(tmp_path, name):
    with TestClient(
        profile_app(tmp_path), client=("127.0.0.1", 50000), base_url="http://127.0.0.1"
    ) as client:
        response = client.post(
            "/admin/api/profiles",
            json={"name": name, "configuration": {"model": "nvidia_nim/deep"}},
        )
        assert response.status_code in {409, 422}, response.text
        assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize(
    "configuration",
    [
        {"model": "unknown_provider/model"},
        {"model": "nvidia_nim/"},
        {"model": "unqualified"},
        {"model": "nvidia_nim/deep", "model_fallbacks": ["unknown_provider/model"]},
        {"model": "nvidia_nim/deep", "proxy_auth_token": "do-not-store"},
        {"model": "nvidia_nim/deep", "reasoning_policy": "invalid"},
    ],
)
def test_invalid_profile_configuration_rejected(tmp_path, configuration):
    with TestClient(
        profile_app(tmp_path), client=("127.0.0.1", 50000), base_url="http://127.0.0.1"
    ) as client:
        response = client.post(
            "/admin/api/profiles",
            json={"name": "invalid", "configuration": configuration},
        )
        assert response.status_code == 422, response.text
        assert client.get("/admin/api/profiles/invalid").status_code == 404


def test_rejected_credential_field_is_not_echoed(tmp_path):
    secret = "synthetic-rejected-credential-value"
    with TestClient(
        profile_app(tmp_path), client=("127.0.0.1", 50000), base_url="http://127.0.0.1"
    ) as client:
        response = client.post(
            "/admin/api/profiles",
            json={
                "name": "invalid",
                "configuration": {
                    "model": "nvidia_nim/deep",
                    "proxy_auth_token": secret,
                },
            },
        )
        assert response.status_code == 422
        assert secret not in response.text
        assert not (tmp_path / "profiles.json").exists()


def test_session_lookup_cannot_restore_concurrently_deleted_session(
    tmp_path, monkeypatch
):
    registry = ProfileSessions(ProfileStore(tmp_path / "profiles.json"))
    session = registry.create(
        "default",
        ModelCatalogSnapshot(
            Settings(MODEL="groq/base", GROQ_API_KEY="synthetic-key"),
            (),
        ),
    )
    refreshing = Event()
    release = Event()
    deleting = Event()
    deleted = Event()
    reads = 0

    def clock():
        nonlocal reads
        if current_thread().name.endswith("_0"):
            reads += 1
            if reads == 2:
                refreshing.set()
                assert release.wait(5)
        return monotonic()

    def remove():
        deleting.set()
        registry.delete(session.session_id)
        deleted.set()

    monkeypatch.setattr(profile_routes, "time", SimpleNamespace(monotonic=clock))
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="session-reader") as pool:
        reading = pool.submit(registry.get, session.session_id)
        assert refreshing.wait(5)
        removal = pool.submit(remove)
        try:
            assert deleting.wait(5)
            assert not deleted.wait(0.1), "Deletion raced with an unfinished lookup"
        finally:
            release.set()
        reading.result(timeout=5)
        removal.result(timeout=5)
    with pytest.raises(ProfileNotFoundError):
        registry.get(session.session_id)


def test_default_reserved_unknown_session_and_binding_auth(tmp_path):
    with TestClient(
        profile_app(tmp_path), client=("127.0.0.1", 50000), base_url="http://127.0.0.1"
    ) as client:
        for method in (client.put, client.delete):
            kwargs = (
                {"json": {"configuration": {"model": "groq/new"}}}
                if method == client.put
                else {}
            )
            assert method("/admin/api/profiles/default", **kwargs).status_code == 409
        assert (
            client.post(
                "/api/launch-sessions", headers=AUTH, json={"profile": "missing"}
            ).status_code
            == 404
        )
        assert (
            client.post("/api/launch-sessions", json={"profile": "default"}).status_code
            == 401
        )
        session = mint(client, "default")
        assert client.get(session["base_path"] + "/v1/models").status_code == 401
        assert (
            client.get(
                session["base_path"] + "/v1/models", headers={"x-api-key": TOKEN}
            ).status_code
            == 200
        )
        assert (
            client.delete(
                "/api/launch-sessions/" + session["session_id"], headers=AUTH
            ).status_code
            == 204
        )
        assert (
            client.get(session["base_path"] + "/v1/models", headers=AUTH).status_code
            == 404
        )
        assert (
            client.get("/fcc/sessions/unknown/v1/models", headers=AUTH).status_code
            == 404
        )


class RecordingProvider:
    """Hold two real requests at execution, then force profile-specific fallback."""

    def __init__(self):
        self.calls = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.hold = True

    async def stream_messages(
        self, request, input_tokens=0, *, reasoning, **kwargs
    ) -> AsyncIterator[str]:
        self.calls.append(
            (
                request.model,
                reasoning.control.value,
                "messages",
                reasoning.effort.value if reasoning.effort else None,
            )
        )
        if request.model.endswith("primary"):
            if self.hold:
                if len(self.calls) == 2:
                    self.entered.set()
                await asyncio.wait_for(self.release.wait(), 5)
            raise execution_failure("controlled profile primary failure")
        for chunk in text_stream(
            request.model, model=kwargs.get("response_model") or request.model
        ):
            yield chunk

    async def stream_responses(
        self, request, input_tokens=0, *, reasoning, **kwargs
    ) -> AsyncIterator[str]:
        self.calls.append(
            (
                request.model,
                reasoning.control.value,
                "responses",
                reasoning.effort.value if reasoning.effort else None,
            )
        )
        for chunk in responses_text_stream(
            request.model, model=kwargs.get("response_model") or request.model
        ):
            yield chunk


@pytest.mark.asyncio
async def test_overlapping_sessions_keep_routes_reasoning_fallback_count_and_discovery_after_edits(
    tmp_path,
):
    app = profile_app(tmp_path)
    provider = RecordingProvider()
    configurations = {
        "alpha": {
            "model": "nvidia_nim/alpha-base",
            "model_sonnet": "nvidia_nim/alpha-sonnet-primary",
            "model_fallbacks": ["groq/alpha-fallback"],
            "reasoning_policy": "off",
        },
        "beta": {
            "model": "groq/beta-base",
            "model_sonnet": "groq/beta-sonnet-primary",
            "model_fallbacks": ["nvidia_nim/beta-fallback"],
            "reasoning_policy": "high",
        },
    }
    with (
        patch("free_claude_code.api.routes.resolve_provider", return_value=provider),
        patch("free_claude_code.api.routes.get_token_count", return_value=7),
        patch("free_claude_code.api.handlers.token_count.trace_event") as token_trace,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 50000)),
            base_url="http://127.0.0.1",
        ) as client:
            sessions = {}
            catalogs = {}
            for name, configuration in configurations.items():
                created = await client.post(
                    "/admin/api/profiles",
                    json={"name": name, "configuration": configuration},
                )
                assert created.status_code == 201, created.text
                bound = await client.post(
                    "/api/launch-sessions", headers=AUTH, json={"profile": name}
                )
                assert bound.status_code == 201, bound.text
                sessions[name] = bound.json()["base_path"]
                catalog = await client.get(
                    sessions[name] + "/v1/models?view=messages", headers=AUTH
                )
                assert catalog.status_code == 200, catalog.text
                catalogs[name] = catalog.json()
                refs = {row["provider_model_ref"] for row in catalogs[name]["data"]}
                assert refs == {
                    configuration["model"],
                    configuration["model_sonnet"],
                    *configuration["model_fallbacks"],
                }
            tasks = [
                asyncio.create_task(
                    client.post(path + "/v1/messages", headers=AUTH, json=messages())
                )
                for path in sessions.values()
            ]
            try:
                await asyncio.wait_for(provider.entered.wait(), 5)
                assert {call[:2] for call in provider.calls} == {
                    ("alpha-sonnet-primary", "off"),
                    ("beta-sonnet-primary", "on"),
                }
                for name in configurations:
                    changed = await client.put(
                        "/admin/api/profiles/" + name,
                        json={
                            "configuration": {
                                "model": "groq/edited-" + name,
                                "reasoning_policy": "off",
                            }
                        },
                    )
                    assert changed.status_code == 200, changed.text
                assert (
                    await client.delete("/admin/api/profiles/alpha")
                ).status_code == 204
            finally:
                provider.release.set()
            responses = await asyncio.gather(*tasks)
            for name, response in zip(sessions, responses, strict=True):
                assert response.status_code == 200, response.text
                assert response.json()["content"][0]["text"] == name + "-fallback"
            assert set(provider.calls) == {
                ("alpha-sonnet-primary", "off", "messages", None),
                ("alpha-fallback", "off", "messages", None),
                ("beta-sonnet-primary", "on", "messages", "high"),
                ("beta-fallback", "on", "messages", "high"),
            }
            provider.hold = False
            for name, path in sessions.items():
                repeated = await client.post(
                    path + "/v1/messages", headers=AUTH, json=messages()
                )
                assert repeated.status_code == 200, repeated.text
                assert repeated.json()["content"][0]["text"] == name + "-fallback"
                counted = await client.post(
                    path + "/v1/messages/count_tokens", headers=AUTH, json=messages()
                )
                assert counted.status_code == 200, counted.text
                assert counted.json()["input_tokens"] == 7
                after = await client.get(
                    path + "/v1/models?view=messages", headers=AUTH
                )
                assert after.json() == catalogs[name]
                response = await client.post(
                    path + "/v1/responses",
                    headers=AUTH,
                    json={
                        "model": configurations[name]["model"],
                        "input": "Explain profile routing",
                        "stream": True,
                    },
                )
                assert response.status_code == 200, response.text
                assert (name + "-base") in response.text
            token_routes = [
                call.kwargs["provider_model_ref"]
                for call in token_trace.call_args_list
                if call.kwargs.get("stage") == "routing"
            ]
            assert token_routes == [
                "nvidia_nim/alpha-sonnet-primary",
                "groq/beta-sonnet-primary",
            ]
            assert (
                await client.post(
                    "/api/launch-sessions", headers=AUTH, json={"profile": "alpha"}
                )
            ).status_code == 404
            fresh = await client.post(
                "/api/launch-sessions", headers=AUTH, json={"profile": "beta"}
            )
            assert fresh.json()["configuration"]["model"] == "groq/edited-beta"


@pytest.mark.parametrize(
    "model",
    [
        "groq/outside",
        "unknown_provider/outside",
        gateway_model_id("groq/outside"),
        desktop_model_id("groq/outside"),
        no_thinking_gateway_model_id("groq/outside"),
        "github_models/vendor/opus",
        gateway_model_id("github_models/vendor/opus"),
        desktop_model_id("github_models/vendor/opus"),
    ],
)
def test_named_scope_rejects_outside_direct_encoded_and_retired_model_ids(
    tmp_path, model
):
    with TestClient(
        profile_app(tmp_path), client=("127.0.0.1", 50000), base_url="http://127.0.0.1"
    ) as client:
        create_profile(client, "restricted", {"model": "nvidia_nim/allowed"})
        path = mint(client, "restricted")["base_path"]
        counted = client.post(
            path + "/v1/messages/count_tokens", headers=AUTH, json=messages(model)
        )
        assert counted.status_code == 400, counted.text
        generated = client.post(
            path + "/v1/messages", headers=AUTH, json=messages(model)
        )
        assert generated.status_code == 400, generated.text
        responses = client.post(
            path + "/v1/responses",
            headers=AUTH,
            json={"model": model, "input": "Hello", "stream": True},
        )
        assert responses.status_code == 400, responses.text


@pytest.mark.parametrize(
    "model",
    [
        "nvidia_nim/allowed",
        gateway_model_id("nvidia_nim/allowed"),
        desktop_model_id("nvidia_nim/allowed"),
        no_thinking_gateway_model_id("nvidia_nim/allowed"),
        "arbitrary-alias",
    ],
)
def test_named_allowed_models_and_aliases_count_through_same_route(tmp_path, model):
    with TestClient(
        profile_app(tmp_path), client=("127.0.0.1", 50000), base_url="http://127.0.0.1"
    ) as client:
        create_profile(client, "restricted", {"model": "nvidia_nim/allowed"})
        path = mint(client, "restricted")["base_path"]
        with (
            patch("free_claude_code.api.routes.get_token_count", return_value=11),
            patch("free_claude_code.api.handlers.token_count.trace_event") as trace,
        ):
            counted = client.post(
                path + "/v1/messages/count_tokens", headers=AUTH, json=messages(model)
            )
        assert counted.status_code == 200, counted.text
        assert counted.json()["input_tokens"] == 11
        routes = [
            call.kwargs["provider_model_ref"]
            for call in trace.call_args_list
            if call.kwargs.get("stage") == "routing"
        ]
        assert routes == ["nvidia_nim/allowed"]


def test_unbound_default_preserves_explicit_model_and_global_tier(tmp_path):
    with TestClient(
        profile_app(tmp_path), client=("127.0.0.1", 50000), base_url="http://127.0.0.1"
    ) as client:
        with (
            patch("free_claude_code.api.routes.get_token_count", return_value=3),
            patch("free_claude_code.api.handlers.token_count.trace_event") as trace,
        ):
            for model in ("deepseek/explicit-outside", "claude-sonnet"):
                response = client.post(
                    "/v1/messages/count_tokens", headers=AUTH, json=messages(model)
                )
                assert response.status_code == 200, response.text
        routes = [
            call.kwargs["provider_model_ref"]
            for call in trace.call_args_list
            if call.kwargs.get("stage") == "routing"
        ]
        assert routes == ["deepseek/explicit-outside", "groq/global-sonnet"]


@pytest.mark.asyncio
async def test_session_discovery_metadata_is_retained_across_provider_catalog_refresh(
    tmp_path,
):
    app = profile_app(tmp_path)
    manager = provider_manager_for_app(app)
    manager.cache_model_infos(
        "nvidia_nim",
        {
            ProviderModelInfo(
                "allowed",
                supports_thinking=True,
                context_window_tokens=32000,
                max_output_tokens=4096,
            ),
            ProviderModelInfo("outside", context_window_tokens=64000),
        },
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 50000)),
        base_url="http://127.0.0.1",
    ) as client:
        created = await client.post(
            "/admin/api/profiles",
            json={
                "name": "restricted",
                "configuration": {"model": "nvidia_nim/allowed"},
            },
        )
        assert created.status_code == 201, created.text
        bound = await client.post(
            "/api/launch-sessions", headers=AUTH, json={"profile": "restricted"}
        )
        assert bound.status_code == 201, bound.text
        first = bound.json()["base_path"]
        before = await client.get(first + "/v1/models?view=messages", headers=AUTH)
        assert before.status_code == 200, before.text
        assert {row["provider_model_ref"] for row in before.json()["data"]} == {
            "nvidia_nim/allowed"
        }
        assert before.json()["data"][0]["contextWindow"] == 32000
        manager.cache_model_infos(
            "nvidia_nim",
            {
                ProviderModelInfo(
                    "allowed",
                    supports_thinking=False,
                    context_window_tokens=128000,
                    max_output_tokens=8192,
                )
            },
        )
        after = await client.get(first + "/v1/models?view=messages", headers=AUTH)
        assert after.json() == before.json()
        second_bound = await client.post(
            "/api/launch-sessions", headers=AUTH, json={"profile": "restricted"}
        )
        assert second_bound.status_code == 201, second_bound.text
        second = second_bound.json()["base_path"]
        latest = await client.get(second + "/v1/models?view=messages", headers=AUTH)
        assert latest.json()["data"][0]["contextWindow"] == 128000
        for suffix in ("/v1/models", "/muse-code/models"):
            response = await client.get(first + suffix, headers=AUTH)
            assert response.status_code == 200, response.text
            assert "outside" not in response.text
