"""Routing profiles preserve snapshots and serialize independent writers."""

import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from free_claude_code.application.routing import ModelRouter
from free_claude_code.config.profiles import (
    ProfileConflictError,
    ProfileNotFoundError,
    ProfileStore,
    RoutingProfile,
)
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
        MODEL_FALLBACKS=("deepseek/global-fallback",),
        NVIDIA_NIM_API_KEY="synthetic-provider-key",
    )
    profile = RoutingProfile(model="nvidia_nim/private", reasoning_policy="off")
    overlay = profile.overlay(settings, "private")
    assert (
        ModelRouter(overlay).resolve("claude-sonnet").primary.provider_model_ref
        == "nvidia_nim/private"
    )
    assert ModelRouter(overlay).resolve("claude-sonnet").fallbacks == ()
    assert overlay.nvidia_nim_api_key == "synthetic-provider-key"
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
