"""FCC-only profile selection and explicitly generated convenience commands."""

import json
import re
import shlex
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request

from free_claude_code.cli.local_http import open_local_request
from free_claude_code.config.profiles import RoutingProfile, validate_profile_name

from .catalog_http import CATALOG_TIMEOUT_SECONDS


def extract_fcc_profile(args: Sequence[str]) -> tuple[str | None, list[str]]:
    """Consume only FCC's selector; keep every native harness argument intact."""
    selected: str | None = None
    remaining: list[str] = []
    index = 0
    while index < len(args):
        argument = args[index]
        if argument == "--":
            remaining.extend(args[index:])
            break
        if argument == "--fcc-profile" or argument.startswith("--fcc-profile="):
            if selected is not None:
                raise ValueError("Specify --fcc-profile only once.")
            if argument == "--fcc-profile":
                index += 1
                if index >= len(args):
                    raise ValueError("--fcc-profile requires a profile name.")
                selected = args[index]
            else:
                selected = argument.partition("=")[2]
            validate_profile_name(selected)
        else:
            remaining.append(argument)
        index += 1
    return selected, remaining


@dataclass(frozen=True, slots=True)
class BoundLaunchSession:
    session_id: str
    profile: str
    configuration: RoutingProfile
    proxy_root_url: str


def create_launch_session(
    proxy_root_url: str, auth_token: str, profile: str
) -> BoundLaunchSession:
    validate_profile_name(profile)
    request = Request(
        f"{proxy_root_url.rstrip('/')}/api/launch-sessions",
        data=json.dumps({"profile": profile}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with open_local_request(request, timeout=CATALOG_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Invalid launch-session response.")
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]+", session_id
    ):
        raise ValueError("Invalid launch-session identity.")
    base_path = f"/fcc/sessions/{session_id}"
    if payload.get("base_path") != base_path or payload.get("profile") != profile:
        raise ValueError("Launch-session binding does not match requested profile.")
    return BoundLaunchSession(
        session_id,
        profile,
        RoutingProfile.model_validate(payload.get("configuration")),
        f"{proxy_root_url.rstrip('/')}{base_path}",
    )


def close_launch_session(proxy_root_url: str, auth_token: str, session_id: str) -> None:
    request = Request(
        f"{proxy_root_url.rstrip('/')}/api/launch-sessions/{session_id}",
        headers={"Authorization": f"Bearer {auth_token}"},
        method="DELETE",
    )
    try:
        with open_local_request(request, timeout=5.0):
            pass
    except (OSError, ValueError) as exc:
        print(
            f"Could not close FCC launch session ({type(exc).__name__}); it will expire automatically.",
            file=sys.stderr,
        )


def generate_profile_launchers(
    output_dir: Path, names: Sequence[str], harness: str = "claude"
) -> list[Path]:
    """Write wrappers only where explicitly requested; never install implicitly."""
    if not re.fullmatch(r"[a-z][a-z0-9-]*", harness):
        raise ValueError("Invalid harness command name.")
    for name in names:
        validate_profile_name(name, allow_default=False)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for name in dict.fromkeys(names):
        path = output_dir / f"fcc-{harness}-{name}"
        with path.open("x", encoding="utf-8") as handle:
            handle.write(
                f'#!/bin/sh\nexec {shlex.quote(f"fcc-{harness}")} --fcc-profile {shlex.quote(name)} "$@"\n'
            )
        path.chmod(0o755)
        paths.append(path)
    return paths
