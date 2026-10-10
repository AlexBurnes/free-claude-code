"""Profile selection must preserve native CLI argument semantics."""

import json
import os
import subprocess
from unittest.mock import patch

import pytest

from free_claude_code.cli import local_http
from free_claude_code.cli.launchers.profiles import (
    extract_fcc_profile,
    generate_profile_launchers,
)
from tests.cli.conftest import LaunchCapture
from tests.cli.test_launcher_workflow import launch


@pytest.mark.parametrize("harness", ["claude", "codex"])
@pytest.mark.parametrize(
    "args",
    [
        ["--profile", "native-work", "explain code"],
        ["--profile=native-work", "--model", "native-model", "explain code"],
        ["--", "--fcc-profile", "literal-prompt", "--profile", "literal-native"],
    ],
)
def test_native_profile_and_literal_arguments_pass_through(
    harness: str, args: list[str], launch_capture: LaunchCapture
) -> None:
    launch(harness, args)
    command = launch_capture.commands[0]
    assert command[0] == harness
    assert command[-len(args) :] == args
    assert len(launch_capture.commands) == 1


@pytest.mark.parametrize(
    ("args", "selected", "remaining"),
    [
        (
            ["--fcc-profile", "deep", "--profile", "native"],
            "deep",
            ["--profile", "native"],
        ),
        (
            ["--profile=native", "--fcc-profile=deep", "prompt"],
            "deep",
            ["--profile=native", "prompt"],
        ),
        (["--", "--fcc-profile", "deep"], None, ["--", "--fcc-profile", "deep"]),
        (["explain --fcc-profile deep"], None, ["explain --fcc-profile deep"]),
        (["--fcc-profile", "default"], "default", []),
    ],
)
def test_profile_selector_preserves_native_arguments(args, selected, remaining):
    assert extract_fcc_profile(args) == (selected, remaining)


@pytest.mark.parametrize(
    "args",
    [
        ["--fcc-profile"],
        ["--fcc-profile="],
        ["--fcc-profile", "Bad"],
        ["--fcc-profile", "../escape"],
        ["--fcc-profile", "deep", "--fcc-profile=other"],
        ["--fcc-profile", "--profile", "native"],
    ],
)
def test_invalid_selector_rejected_before_http(args):
    with pytest.raises(ValueError):
        extract_fcc_profile(args)


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_explicit_profile_binds_launch_and_releases_each_session(
    harness, launch_capture
):
    from tests.cli.conftest import JsonResponse

    minted, closed = [], []

    def respond(request, *, timeout):
        if request.get_method() == "POST":
            profile = json.loads(request.data)["profile"]
            minted.append(profile)
            session_id = f"session-{len(minted)}"
            return JsonResponse(
                json.dumps(
                    {
                        "session_id": session_id,
                        "profile": profile,
                        "configuration": {
                            "model": "nvidia_nim/catalog-model:variant",
                            "reasoning_policy": "off",
                        },
                        "base_path": f"/fcc/sessions/{session_id}",
                    }
                ).encode()
            )
        if request.get_method() == "DELETE":
            closed.append(request.full_url)
            return JsonResponse(b"")
        return launch_capture.open(request, timeout=timeout)

    with patch.object(local_http._DIRECT_OPENER, "open", respond):
        for profile in ("deep", "fast"):
            launch(harness, ["--fcc-profile", profile, "--profile", "native", "prompt"])
    assert minted == ["deep", "fast"]
    assert closed == [
        f"http://127.0.0.1:8182/api/launch-sessions/session-{index}" for index in (1, 2)
    ]
    for index, (command, env) in enumerate(
        zip(launch_capture.commands, launch_capture.environments, strict=True), 1
    ):
        assert command[-3:] == ["--profile", "native", "prompt"]
        assert "--fcc-profile" not in command
        if harness == "claude":
            assert (
                env["ANTHROPIC_BASE_URL"]
                == f"http://127.0.0.1:8182/fcc/sessions/session-{index}"
            )
        else:
            assert any(
                f"http://127.0.0.1:8182/fcc/sessions/session-{index}/v1" in arg
                for arg in command
            )


def test_explicit_profile_binding_closes_when_child_start_fails(launch_capture):
    from tests.cli.conftest import JsonResponse

    methods = []

    def respond(request, *, timeout):
        methods.append(request.get_method())
        if request.get_method() == "POST":
            return JsonResponse(
                json.dumps(
                    {
                        "session_id": "failed-launch",
                        "profile": "deep",
                        "configuration": {"model": "nvidia_nim/catalog-model:variant"},
                        "base_path": "/fcc/sessions/failed-launch",
                    }
                ).encode()
            )
        if request.get_method() == "DELETE":
            return JsonResponse(b"")
        return launch_capture.open(request, timeout=timeout)

    def fail_start(command, env):
        raise ValueError("controlled child failure")

    launch_capture.on_start = fail_start
    with patch.object(local_http._DIRECT_OPENER, "open", respond):
        launch("claude", ["--fcc-profile", "deep"], exit_code=1)
    assert methods[-1] == "DELETE"
    assert methods.count("POST") == 1


def test_generated_wrapper_exec_preserves_native_arguments(tmp_path):
    output = tmp_path / "argv.txt"
    fake_bin = tmp_path / "native"
    fake_bin.mkdir()
    fake = fake_bin / "fcc-claude"
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$FCC_TEST_OUTPUT"\n')
    fake.chmod(0o755)
    wrappers = generate_profile_launchers(tmp_path / "wrappers", ["deep", "deep"])
    assert len(wrappers) == 1
    subprocess.run(
        [str(wrappers[0]), "--profile", "native settings", "--", "literal"],
        check=True,
        env={"PATH": f"{fake_bin}:{os.defpath}", "FCC_TEST_OUTPUT": str(output)},
    )
    assert output.read_text().splitlines() == [
        "--fcc-profile",
        "deep",
        "--profile",
        "native settings",
        "--",
        "literal",
    ]
    before = wrappers[0].read_bytes()
    with pytest.raises(FileExistsError):
        generate_profile_launchers(tmp_path / "wrappers", ["deep"])
    assert wrappers[0].read_bytes() == before


@pytest.mark.parametrize(
    ("names", "harness"),
    [
        (["../escape"], "claude"),
        (["default"], "claude"),
        (["Bad"], "claude"),
        (["deep"], "claude;touch"),
    ],
)
def test_invalid_wrapper_names_rejected_without_files(tmp_path, names, harness):
    output = tmp_path / "wrappers"
    with pytest.raises(ValueError):
        generate_profile_launchers(output, names, harness=harness)
    assert not output.exists()
