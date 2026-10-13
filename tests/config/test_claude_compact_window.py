"""Automatic Claude compaction is the default; explicit windows persist safely."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from free_claude_code.config.loader import ManagedConfigStore
from free_claude_code.config.settings import Settings
from tests.api.support import create_test_app

KEY = "FCC_CLAUDE_AUTO_COMPACT_WINDOW"


@pytest.mark.parametrize("value", [None, 100000, 190000, 1000000])
def test_compact_window_accepts_automatic_and_inclusive_range(value):
    assert Settings.model_validate({KEY: value}).claude_auto_compact_window == value


@pytest.mark.parametrize("value", [0, 99999, 1000001, -1, True, 200000.5, "automatic"])
def test_compact_window_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        Settings.model_validate({KEY: value})


def test_blank_compact_window_means_automatic():
    assert Settings.model_validate({KEY: ""}).claude_auto_compact_window is None


def test_compact_window_persists_and_reset_survives_store_reload():
    store = ManagedConfigStore()
    store.initialize({})
    managed = dict(store.read({}).managed)
    store.commit(managed | {KEY: "300000"})
    assert ManagedConfigStore().read({}).settings.claude_auto_compact_window == 300000
    store.commit(managed | {KEY: ""})
    assert ManagedConfigStore().read({}).settings.claude_auto_compact_window is None


def test_admin_exposes_nullable_window_and_apply_reset_persist():
    app = create_test_app()
    with (
        TestClient(
            app, client=("127.0.0.1", 50000), base_url="http://127.0.0.1"
        ) as client,
        patch(
            "free_claude_code.api.admin_routes._check_local_provider",
            AsyncMock(return_value={}),
        ),
    ):
        config = client.get("/admin/api/config")
        assert config.status_code == 200, config.text
        field = next(field for field in config.json()["fields"] if field["key"] == KEY)
        assert field["value"] is None
        assert field["section"] == "models"
        assert field["nullable"] is True
        assert field["type"] == "number"
        applied = client.post(
            "/admin/api/config/apply", json={"values": {KEY: "300000"}}
        )
        assert applied.status_code == 200, applied.text
        assert (
            ManagedConfigStore().read({}).settings.claude_auto_compact_window == 300000
        )
        reset = client.post("/admin/api/config/apply", json={"values": {KEY: None}})
        assert reset.status_code == 200, reset.text
        assert ManagedConfigStore().read({}).settings.claude_auto_compact_window is None
