"""Routing profiles preserve snapshots and serialize independent writers."""

import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from free_claude_code.application.errors import InvalidRequestError
from free_claude_code.application.routing import ModelRouter
from free_claude_code.config.model_refs import ModelFallback
from free_claude_code.config.profiles import (
    ProfileConflictError,
    ProfileNotFoundError,
    ProfileStore,
    RoutingProfile,
)
from free_claude_code.config.reasoning import ReasoningPreference
from free_claude_code.config.settings import Settings


def save_from_process(path: str, index: int) -> None:
    """Use explicit paths: spawned workers never access the user's configuration."""
    ProfileStore(Path(path)).save(
        f"worker-{index}",
        RoutingProfile(model=f"nvidia_nim/model-{index}"),
        Settings(),
        create=True,
    )


def test_parallel_process_writers_do_not_lose_profiles(tmp_path):
    path = tmp_path / "profiles.json"
    with ProcessPoolExecutor(
        max_workers=3, mp_context=multiprocessing.get_context("spawn")
    ) as pool:
        futures = [
            pool.submit(save_from_process, str(path), index) for index in range(9)
        ]
        for future in futures:
            future.result(timeout=20)
    profiles = ProfileStore(path).list(Settings())
    assert set(profiles) == {"default", *(f"worker-{index}" for index in range(9))}
    for index in range(9):
        assert profiles[f"worker-{index}"].model == f"nvidia_nim/model-{index}"


def test_failed_atomic_replace_preserves_previous_document(tmp_path):
    path = tmp_path / "profiles.json"
    store = ProfileStore(path)
    settings = Settings()
    store.save("work", RoutingProfile(model="nvidia_nim/before"), settings, create=True)
    before = path.read_bytes()
    with (
        patch(
            "free_claude_code.config.profiles.os.replace",
            side_effect=OSError("disk failure"),
        ),
        pytest.raises(OSError, match="disk failure"),
    ):
        store.save("work", RoutingProfile(model="groq/after"), settings)
    assert path.read_bytes() == before
    assert store.get("work", settings).model == "nvidia_nim/before"
    assert list(tmp_path.glob("profiles-*.json")) == []


def test_profile_overlay_is_frozen_clears_global_tiers_and_keeps_credentials_shared():
    settings = Settings(
        MODEL="nvidia_nim/global",
        MODEL_SONNET="groq/global-tier",
        MODEL_FALLBACKS=(ModelFallback(model="deepseek/global-fallback"),),
        NVIDIA_NIM_API_KEY="synthetic-provider-key",
    )
    profile = RoutingProfile(model="nvidia_nim/private", reasoning_policy="off")
    overlay = profile.overlay(settings, "private")
    assert (
        ModelRouter(overlay).resolve("claude-sonnet").primary.provider_model_ref
        == "nvidia_nim/private"
    )
    assert ModelRouter(overlay).resolve("claude-sonnet").fallbacks == ()
    assert overlay.nvidia_nim_api_keys[0].get_secret_value() == "synthetic-provider-key"
    assert "synthetic-provider-key" not in profile.model_dump_json()
    with pytest.raises(ValidationError):
        overlay.__setattr__("model", "groq/mutation")
    with pytest.raises(ValidationError):
        profile.__setattr__("model", "groq/mutation")
    settings.model = "groq/global-edited"
    assert (
        ModelRouter(overlay).resolve("unqualified-alias").primary.provider_model_ref
        == "nvidia_nim/private"
    )


def test_default_is_synthesized_and_profile_storage_contains_only_routing(tmp_path):
    path = tmp_path / "profiles.json"
    store = ProfileStore(path)
    settings = Settings(
        MODEL="nvidia_nim/global", ANTHROPIC_AUTH_TOKEN="synthetic-secret"
    )
    store.save("work", RoutingProfile(model="groq/work"), settings, create=True)
    assert store.get("default", settings).model == "nvidia_nim/global"
    settings.model = "deepseek/new-global"
    assert store.get("default", settings).model == "deepseek/new-global"
    document = json.loads(path.read_text())
    assert set(document["profiles"]) == {"work"}
    assert "synthetic-secret" not in path.read_text()
    with pytest.raises(ProfileConflictError):
        store.save("default", RoutingProfile(model="groq/work"), settings, create=True)
    with pytest.raises(ProfileNotFoundError):
        store.get("missing", settings)


def test_legacy_profile_document_loads_without_rewrite_and_saves_as_records(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": {
                    "legacy": {
                        "model": "groq/primary",
                        "model_fallbacks": ["groq/backup", "deepseek/other"],
                    }
                },
            }
        )
    )
    before = path.read_bytes(), path.stat().st_mtime_ns
    store = ProfileStore(path)
    settings = Settings(GROQ_API_KEY=["synthetic-first", "synthetic-second"])
    profile = store.get("legacy", settings)
    assert profile.model_fallbacks == (
        ModelFallback(model="groq/backup"),
        ModelFallback(model="deepseek/other"),
    )
    assert store.list(settings)["legacy"] == profile
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    store.save("legacy", profile, settings)
    document = json.loads(path.read_text())
    assert document["version"] == 1
    assert document["profiles"]["legacy"]["model_fallbacks"] == [
        {"model": "groq/backup", "reasoning": "inherit", "api_key_number": 1},
        {"model": "deepseek/other", "reasoning": "inherit", "api_key_number": 1},
    ]
    assert "synthetic-first" not in path.read_text()


def test_profile_record_round_trip_preserves_same_model_keys_effort_and_scope(tmp_path):
    settings = Settings(
        MODEL="deepseek/global",
        GROQ_API_KEY=["synthetic-first", "synthetic-second", "synthetic-third"],
        MODEL_FALLBACKS=[{"model": "deepseek/global-backup", "reasoning": "off"}],
    )
    profile = RoutingProfile.model_validate(
        {
            "model": "groq/shared",
            "model_fallbacks": [
                {"model": "groq/shared", "reasoning": "low", "api_key_number": 2},
                {"model": "groq/shared", "reasoning": "high", "api_key_number": 3},
            ],
        }
    )
    store = ProfileStore(tmp_path / "profiles.json")
    store.save("keys", profile, settings, create=True)
    loaded = store.get("keys", settings)
    assert loaded == profile
    assert loaded.allowed_models == ("groq/shared",)
    overlay = loaded.overlay(settings, "keys")
    route = ModelRouter(overlay).resolve("claude-sonnet")
    assert route.primary.provider_model_ref == "groq/shared"
    assert [
        (
            candidate.target.provider_model_ref,
            candidate.target.api_key_number,
            candidate.reasoning_preference,
        )
        for candidate in route.fallbacks
    ] == [
        ("groq/shared", 2, ReasoningPreference.LOW),
        ("groq/shared", 3, ReasoningPreference.HIGH),
    ]
    assert overlay.routing_allowed_models == ("groq/shared",)
    assert loaded.model_fallbacks is not None
    with pytest.raises(ValidationError):
        loaded.model_fallbacks[0].__setattr__("api_key_number", 1)
    with pytest.raises(InvalidRequestError):
        ModelRouter(overlay).resolve("deepseek/global-backup")


def test_default_profile_synthesizes_frozen_fallback_records_from_settings(tmp_path):
    settings = Settings(
        MODEL="groq/primary",
        GROQ_API_KEY=["first", "second"],
        MODEL_FALLBACKS=[
            {"model": "groq/primary", "reasoning": "max", "api_key_number": 2}
        ],
    )
    store = ProfileStore(tmp_path / "profiles.json")
    profile = store.list(settings)["default"]
    assert profile == store.get("default", settings)
    assert profile.model_fallbacks == (
        ModelFallback(
            model="groq/primary", reasoning=ReasoningPreference.MAX, api_key_number=2
        ),
    )
    settings.model_fallbacks = (ModelFallback(model="deepseek/changed"),)
    assert profile.model_fallbacks[0].api_key_number == 2
    assert profile.model_fallbacks[0].reasoning is ReasoningPreference.MAX
    assert not store.path.exists()


@pytest.mark.parametrize("number", [0, -1, True, "2", 3])
def test_invalid_profile_key_selection_is_rejected_before_persistence(tmp_path, number):
    store = ProfileStore(tmp_path / "profiles.json")
    settings = Settings(GROQ_API_KEY=["synthetic-first", "synthetic-second"])
    with pytest.raises(ValidationError):
        profile = RoutingProfile.model_validate(
            {
                "model": "groq/primary",
                "model_fallbacks": [{"model": "groq/backup", "api_key_number": number}],
            }
        )
        store.save("invalid", profile, settings, create=True)
    assert not store.path.exists()
