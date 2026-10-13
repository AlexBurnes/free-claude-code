"""Claude owns permission options and prompt interpretation."""

import pytest

from tests.cli.conftest import LaunchCapture
from tests.cli.test_launcher_workflow import launch


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--permission-mode", "auto", "fix tests"],
        ["--permission-mode=acceptEdits", "fix tests"],
        ["--dangerously-skip-permissions", "fix tests"],
        ["--permission-mode"],
        ["--", "--permission-mode=auto"],
        ["explain --permission-mode auto"],
    ],
)
def test_claude_owns_permission_selection(
    args: list[str], launch_capture: LaunchCapture
) -> None:
    launch("claude", args)
    assert launch_capture.commands == [["claude", *args]]
    env = launch_capture.environments[0]
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8182"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "launcher-test-token"
    assert env["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] == "1"
    assert env["CLAUDE_CODE_DISABLE_ADVISOR_TOOL"] == "1"
    assert env["CLAUDE_CODE_AUTO_MODE_SERVER"] == "0"


@pytest.mark.parametrize(("window", "expected"), [(None, "250000"), (300000, "300000")])
def test_launcher_uses_optional_fcc_window_and_preserves_native_override(
    window, expected, monkeypatch, launch_capture
):
    monkeypatch.setenv("CLAUDE_CODE_AUTO_COMPACT_WINDOW", "250000")
    if window is None:
        monkeypatch.delenv("FCC_CLAUDE_AUTO_COMPACT_WINDOW", raising=False)
    else:
        monkeypatch.setenv("FCC_CLAUDE_AUTO_COMPACT_WINDOW", str(window))
    launch("claude", ["prompt"])
    assert launch_capture.environments[0]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == expected


def test_default_launcher_does_not_force_compact_window(monkeypatch, launch_capture):
    monkeypatch.delenv("FCC_CLAUDE_AUTO_COMPACT_WINDOW", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_AUTO_COMPACT_WINDOW", raising=False)
    launch("claude", ["prompt"])
    assert "CLAUDE_CODE_AUTO_COMPACT_WINDOW" not in launch_capture.environments[0]
