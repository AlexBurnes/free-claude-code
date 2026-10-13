"""Hot compaction-window updates affect future managed sessions only."""

from unittest.mock import AsyncMock, patch

import pytest

from free_claude_code.cli.managed.manager import ManagedClaudeSessionManager
from tests.api.support import create_test_app, runtime_for_app

KEY = "FCC_CLAUDE_AUTO_COMPACT_WINDOW"


@pytest.mark.asyncio
async def test_hot_apply_updates_future_sessions_and_preserves_active_session(tmp_path):
    runtime = runtime_for_app(create_test_app())
    manager = ManagedClaudeSessionManager(
        str(tmp_path), "http://localhost:8082", auth_token="test-token"
    )
    runtime._messaging._cli_manager = manager
    existing, existing_id, created = await manager.get_or_create_session()
    assert created and existing.config.auto_compact_window is None
    applied = await runtime.apply_admin_config({KEY: "300000"})
    assert applied["applied"] is True, applied
    future, _, created = await manager.get_or_create_session()
    assert created and future.config.auto_compact_window == 300000
    reused, _, created = await manager.get_or_create_session(existing_id)
    assert reused is existing and created is False
    assert reused.config.auto_compact_window is None
    rejected = await runtime.apply_admin_config({KEY: "99999"})
    assert rejected["valid"] is False
    assert manager.auto_compact_window == 300000
    reset = await runtime.apply_admin_config({KEY: None})
    assert reset["applied"] is True, reset
    automatic, _, _ = await manager.get_or_create_session()
    assert automatic.config.auto_compact_window is None
    assert future.config.auto_compact_window == 300000


@pytest.mark.asyncio
async def test_failed_provider_publication_does_not_change_manager_window(tmp_path):
    runtime = runtime_for_app(create_test_app())
    manager = ManagedClaudeSessionManager(
        str(tmp_path),
        "http://localhost:8082",
        auth_token="test-token",
        auto_compact_window=250000,
    )
    runtime._messaging._cli_manager = manager
    with (
        patch.object(
            runtime.provider_manager,
            "replace",
            AsyncMock(side_effect=RuntimeError("controlled publication failure")),
        ),
        pytest.raises(RuntimeError, match="controlled publication failure"),
    ):
        await runtime.apply_admin_config({KEY: "300000"})
    assert manager.auto_compact_window == 250000
    future, _, _ = await manager.get_or_create_session()
    assert future.config.auto_compact_window == 250000
