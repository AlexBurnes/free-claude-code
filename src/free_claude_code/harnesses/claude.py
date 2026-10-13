"""Shared Claude Code environment policy for FCC client surfaces."""

from collections.abc import Mapping

from free_claude_code.harnesses.environment import client_environment

CLAUDE_AUTO_COMPACT_WINDOW_ENV = "CLAUDE_CODE_AUTO_COMPACT_WINDOW"
CLAUDE_BINARY_NAME = "claude"


def claude_proxy_values(
    proxy_root_url: str,
    auth_token: str,
    *,
    auto_compact_window: int | None = None,
) -> dict[str, str]:
    """Settings shared by launched Claude sessions and the VS Code extension."""
    values = {
        "ANTHROPIC_BASE_URL": proxy_root_url,
        "ANTHROPIC_AUTH_TOKEN": auth_token,
        "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
        # FCC uses Claude's separate classifier requests, not Anthropic server checks.
        "CLAUDE_CODE_AUTO_MODE_SERVER": "0",
        "CLAUDE_CODE_DISABLE_ADVISOR_TOOL": "1",
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_FEEDBACK_COMMAND": "1",
        "DISABLE_ERROR_REPORTING": "1",
    }
    if auto_compact_window is not None:
        values[CLAUDE_AUTO_COMPACT_WINDOW_ENV] = str(auto_compact_window)
    return values


def build_claude_proxy_env(
    *,
    proxy_root_url: str,
    auth_token: str,
    base_env: Mapping[str, str],
    auto_compact_window: int | None = None,
) -> dict[str, str]:
    """Return the canonical environment for Claude Code proxy sessions."""

    # Claude's aggregate traffic flag also suppresses gateway model discovery.
    return client_environment(
        base_env,
        proxy_root_url=proxy_root_url,
        remove_prefixes=("ANTHROPIC_",),
        remove_keys=("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",),
        updates=claude_proxy_values(
            proxy_root_url, auth_token, auto_compact_window=auto_compact_window
        ),
    )
