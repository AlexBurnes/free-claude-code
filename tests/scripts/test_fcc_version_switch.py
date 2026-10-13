"""Exercise version switching without the user's services, installs or accounts."""

import json
import os
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import time
from contextlib import closing, suppress
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import pytest

SWITCHER = Path(__file__).resolve().parents[2] / "scripts/fcc-version.py"
INSTALLER = SWITCHER.with_name("fcc-versions-install.sh")
LAUNCHERS = ("fcc-server", "fcc-claude", "fcc-codex", "fcc-pi", "fcc-opencode")

RUNTIME = r"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

root = Path(os.environ["FIXTURE_ROOT"])
role = Path(__file__).parent.parent.name
if sys.argv[1:] == ["--version"]:
    version = root / ("version-" + role)
    print(version.read_text() if version.exists() else "fcc-server " + ("6.10.5" if role == "upstream" else "fork-fixture"))
    raise SystemExit(0)
if Path(__file__).name != "fcc-server":
    print(json.dumps({"runtime": role, "command": Path(__file__).name, "argv": sys.argv[1:]}))
    raise SystemExit(0)

active_env = Path.home() / ".fcc/.env"
schema_file = root / ("schema-" + role)
if schema_file.exists() and active_env.exists():
    expected = schema_file.read_text().strip()
    content = active_env.read_text()
    if expected == "1" and "FCC_CONFIG_SCHEMA=2" in content:
        raise SystemExit("Fixture older runtime rejects schema 2")
    if expected == "2":
        active_env.write_text(content.replace("FCC_CONFIG_SCHEMA=1", "FCC_CONFIG_SCHEMA=2"))
        (root / ("migrated-" + role)).write_text(active_env.read_text())
if (root / ("mutate-accounts-" + role)).exists():
    (Path.home() / ".fcc/accounts.json").write_text('{"fixture_account":"refreshed"}')

if (root / ("mutate-" + role)).exists():
    (root / "config/settings.json").write_text('{"user_edit":"during-start"}')
once = root / ("fail-health-once-" + role)
failed_once = once.exists()
if failed_once:
    once.unlink()

class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        failed = failed_once or (root / ("fail-health-" + role)).exists()
        self.send_response(503 if failed else 200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "failed" if failed else "healthy"}).encode())

    def log_message(self, *args):
        pass

HTTPServer(("127.0.0.1", int(os.environ["FIXTURE_PORT"])), Health).serve_forever()
"""

SYSTEMCTL = r"""
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

root = Path(os.environ["FIXTURE_ROOT"])
state_file = root / "service.json"
state = json.loads(state_file.read_text())
args = sys.argv[1:]
command = next(arg for arg in args if arg in {"show", "start", "stop", "is-active", "daemon-reload", "enable"})
with (root / "events.jsonl").open("a") as handle:
    handle.write(json.dumps({"command": command, "pid": state["pid"], "role": state.get("role")}) + "\n")

def save(value):
    temporary = root / "service.next"
    temporary.write_text(json.dumps(value))
    temporary.replace(state_file)

def occupied():
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", int(os.environ["FIXTURE_PORT"]))) == 0

if command == "show":
    print("ActiveState=" + ("active" if state["pid"] else "inactive"))
    print("SubState=" + ("running" if state["pid"] else "dead"))
    print("MainPID=" + str(state["pid"]))
elif command == "is-active":
    raise SystemExit(0 if state["pid"] else 3)
elif command == "stop":
    if (root / "block-stop").exists():
        (root / "stop-entered").touch()
        deadline = time.monotonic() + 5
        while not (root / "release-stop").exists():
            if time.monotonic() > deadline:
                raise SystemExit(9)
            time.sleep(.01)
    if (root / "fail-stop").exists():
        raise SystemExit(1)
    if state["pid"]:
        cmdline = Path("/proc") / str(state["pid"]) / "cmdline"
        if cmdline.exists() and str(root).encode() in cmdline.read_bytes():
            os.kill(state["pid"], signal.SIGTERM)
        else:
            raise SystemExit("Refusing a PID outside the fixture")
        deadline = time.monotonic() + 2
        while occupied() and time.monotonic() < deadline:
            time.sleep(.01)
    if not (root / "stuck-active").exists():
        save({"pid": 0})
elif command == "start":
    if state["pid"] or occupied():
        (root / "overlap").touch()
        raise SystemExit(17)
    manifest = json.loads((root / "state/manifest.json").read_text())
    role = manifest["selected"]
    override = root / "start-role"
    if override.exists():
        role = override.read_text()
    else:
        unit = root / "installed-units/fcc-server.service"
        if unit.exists():
            for line in unit.read_text().splitlines():
                if line.startswith("# fixture-old-runtime="):
                    role = line.split("=", 1)[1]
    if (root / ("fail-start-" + role)).exists():
        raise SystemExit(1)
    runtime = manifest["runtimes"].get(role)
    executable = Path(runtime["bin"]) / "fcc-server" if runtime else root / "runtimes" / role / "bin/fcc-server"
    process = subprocess.Popen([str(executable)], env=os.environ, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    with (root / "pids.jsonl").open("a") as handle:
        handle.write(str(process.pid) + "\n")
    save({"pid": process.pid, "role": role})
"""


@dataclass
class SwitchFixture:
    root: Path
    port: int
    env: dict[str, str]

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def systemctl(self) -> Path:
        return self.root / "bin/systemctl"

    @property
    def manifest(self) -> dict[str, Any]:
        return json.loads((self.state / "manifest.json").read_text())

    def select(self, role: str) -> None:
        manifest = self.manifest
        manifest["selected"] = role
        (self.state / "manifest.json").write_text(json.dumps(manifest))

    def argv(self, *args: str) -> list[str]:
        return [
            sys.executable,
            str(SWITCHER),
            "--state-dir",
            str(self.state),
            "--config-dir",
            str(self.config),
            "--systemctl",
            str(self.systemctl),
            "--systemd-analyze",
            str(self.root / "bin/systemd-analyze"),
            "--health-url",
            f"http://127.0.0.1:{self.port}/health",
            "--timeout",
            "0.5",
            *args,
        ]

    def command(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.argv(*args),
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

    def start(self, role: str = "fork") -> None:
        (self.root / "start-role").write_text(role)
        try:
            subprocess.run(
                [str(self.systemctl), "--user", "start", "fcc-server.service"],
                env=self.env,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        finally:
            (self.root / "start-role").unlink()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                with urlopen(
                    f"http://127.0.0.1:{self.port}/health", timeout=0.1
                ) as response:
                    if response.status == 200:
                        return
            except URLError, TimeoutError, ConnectionError:
                time.sleep(0.01)
        pytest.fail("Fixture runtime did not become healthy")

    def events(self) -> list[dict[str, Any]]:
        path = self.root / "events.jsonl"
        return (
            [json.loads(line) for line in path.read_text().splitlines()]
            if path.exists()
            else []
        )

    def changes(self) -> list[str]:
        return [
            event["command"] for event in self.events() if event["command"] != "show"
        ]

    def clear_events(self) -> None:
        (self.root / "events.jsonl").write_text("")

    def status(self) -> dict[str, Any]:
        result = self.command("status")
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)


def _executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(0o700)


@pytest.fixture
def switch(tmp_path: Path):
    if sys.platform != "linux":
        pytest.skip("Version switching validates Linux /proc service identity.")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    for role in ("fork", "upstream", "legacy"):
        interpreter = tmp_path / "runtimes" / role / "bin/python"
        interpreter.parent.mkdir(parents=True, exist_ok=True)
        interpreter.symlink_to(sys.executable)
        for launcher in LAUNCHERS:
            _executable(tmp_path / "runtimes" / role / "bin" / launcher, RUNTIME)
    _executable(tmp_path / "bin/systemctl", SYSTEMCTL)
    _executable(
        tmp_path / "bin/systemd-analyze",
        "import os, sys\nfrom pathlib import Path\n"
        'root = Path(os.environ["FIXTURE_ROOT"])\n'
        "assert Path(sys.argv[-1]).is_file()\n"
        '(root / "verified-unit").write_text(Path(sys.argv[-1]).read_text())\n'
        'raise SystemExit(1 if (root / "fail-verify").exists() else 0)\n',
    )
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    config = tmp_path / "config"
    config.mkdir(mode=0o700)
    (config / "settings.json").write_text('{"user_edit":"before"}')
    (tmp_path / "service.json").write_text('{"pid":0}')
    (state / "manifest.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "selected": "fork",
                "service": "fcc-server.service",
                "config_dir": str(config),
                "health_url": f"http://127.0.0.1:{port}/health",
                "runtimes": {
                    role: {
                        "bin": str(tmp_path / "runtimes" / role / "bin"),
                        "version": "6.10.5" if role == "upstream" else "fork-fixture",
                        "source": f"fixture-{role}",
                        "revision": "530eacac36897da543aad02962216212c1daf4c1",
                    }
                    for role in ("fork", "upstream")
                },
            }
        )
    )
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
        "XDG_CONFIG_HOME": str(tmp_path / "home/.config"),
        "XDG_DATA_HOME": str(tmp_path / "home/.local/share"),
        "FIXTURE_ROOT": str(tmp_path),
        "FIXTURE_PORT": str(port),
    }
    fixture = SwitchFixture(tmp_path, port, env)
    try:
        yield fixture
    finally:
        pids = tmp_path / "pids.jsonl"
        for pid in pids.read_text().splitlines() if pids.exists() else []:
            cmdline = Path("/proc") / pid / "cmdline"
            if cmdline.exists() and str(tmp_path).encode() in cmdline.read_bytes():
                with suppress(ProcessLookupError):
                    os.kill(int(pid), signal.SIGTERM)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) != 0:
                    break
            time.sleep(0.01)


def test_switch_stops_before_start_and_persists_choice(switch: SwitchFixture) -> None:
    switch.start()
    original_pid = switch.status()["service"]["MainPID"]
    switch.clear_events()
    result = switch.command("use", "upstream")
    assert result.returncode == 0, result.stdout + result.stderr
    assert switch.changes() == ["stop", "start"]
    assert not (switch.root / "overlap").exists()
    assert switch.manifest["selected"] == "upstream"
    status = switch.status()
    assert status["selected"] == "upstream"
    assert status["active_runtime"] == "upstream"
    assert status["service"]["MainPID"] != original_pid


def test_same_healthy_runtime_is_noop(switch: SwitchFixture) -> None:
    switch.start()
    switch.clear_events()
    result = switch.command("use", "fork")
    assert result.returncode == 0, result.stdout + result.stderr
    assert switch.changes() == []
    assert not (switch.state / "backups").exists()


def test_inactive_service_starts_selected_runtime(switch: SwitchFixture) -> None:
    result = switch.command("use", "upstream")
    assert result.returncode == 0, result.stdout + result.stderr
    assert switch.status()["active_runtime"] == "upstream"
    assert switch.changes()[-1] == "start"


@pytest.mark.parametrize("failure_kind", ["start", "health"])
@pytest.mark.parametrize("initially_active", [False, True])
def test_startup_failure_rolls_back_selection_and_original_service_state(
    switch: SwitchFixture, failure_kind: str, initially_active: bool
) -> None:
    if initially_active:
        switch.start()
    (switch.root / f"fail-{failure_kind}-upstream").touch()
    switch.clear_events()
    result = switch.command("use", "upstream")
    assert result.returncode == 1, result.stdout + result.stderr
    assert switch.manifest["selected"] == "fork"
    status = switch.status()
    assert status["service"]["ActiveState"] == (
        "active" if initially_active else "inactive"
    )
    assert status["active_runtime"] == ("fork" if initially_active else None)
    assert not (switch.root / "overlap").exists()
    assert "restored" in (result.stdout + result.stderr).lower()


def test_rollback_does_not_overwrite_settings_edited_during_start(
    switch: SwitchFixture,
) -> None:
    switch.start()
    (switch.root / "fail-health-upstream").touch()
    (switch.root / "mutate-upstream").touch()
    result = switch.command("use", "upstream")
    assert result.returncode == 1
    assert json.loads((switch.config / "settings.json").read_text()) == {
        "user_edit": "during-start"
    }
    assert switch.status()["active_runtime"] == "fork"


@pytest.mark.parametrize("failure", ["fail-stop", "stuck-active"])
def test_stop_must_finish_before_selection_changes(
    switch: SwitchFixture, failure: str
) -> None:
    switch.start()
    (switch.root / failure).touch()
    switch.clear_events()
    result = switch.command("use", "upstream")
    assert result.returncode == 1
    assert switch.manifest["selected"] == "fork"
    assert switch.changes() and set(switch.changes()) == {"stop"}
    assert not (switch.state / "backups").exists()


def test_missing_target_executable_does_not_stop_running_service(
    switch: SwitchFixture,
) -> None:
    switch.start()
    (switch.root / "runtimes/upstream/bin/fcc-server").unlink()
    switch.clear_events()
    result = switch.command("use", "upstream")
    assert result.returncode != 0
    assert switch.changes() == []
    assert switch.manifest["selected"] == "fork"
    assert switch.status()["active_runtime"] == "fork"


def test_manual_occupied_port_is_not_killed_or_started_over(
    switch: SwitchFixture,
) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", switch.port))
        listener.listen()
        result = switch.command("use", "upstream")
        assert result.returncode == 1
        assert "start" not in switch.changes()
        assert switch.manifest["selected"] == "fork"
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", switch.port)) == 0


def test_concurrent_switch_refuses_without_second_mutation(
    switch: SwitchFixture,
) -> None:
    switch.start()
    (switch.root / "block-stop").touch()
    switch.clear_events()
    first = subprocess.Popen(
        switch.argv("use", "upstream"),
        env=switch.env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 3
        while (
            not (switch.root / "stop-entered").exists() and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert (switch.root / "stop-entered").exists()
        second = switch.command("use", "fork")
        assert second.returncode == 1
        assert switch.changes() == ["stop"]
        assert switch.manifest["selected"] == "fork"
    finally:
        (switch.root / "release-stop").touch()
        stdout, stderr = first.communicate(timeout=8)
    assert first.returncode == 0, stdout + stderr
    assert switch.status()["active_runtime"] == "upstream"
    assert switch.changes() == ["stop", "start"]


@pytest.mark.parametrize("launcher", ["claude", "codex", "pi", "opencode"])
@pytest.mark.parametrize("selected", ["fork", "upstream"])
def test_launcher_preserves_native_argument_boundaries(
    switch: SwitchFixture, launcher: str, selected: str
) -> None:
    switch.select(selected)
    args = [
        "--profile",
        "native profile",
        "--model",
        "a/model",
        "--",
        "literal $HOME; argument",
    ]
    result = switch.command("run", launcher, *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "runtime": selected,
        "command": f"fcc-{launcher}",
        "argv": args,
    }
    assert switch.changes() == []


@pytest.mark.parametrize(
    "selector", [["--fcc-profile", "work"], ["--fcc-profile=work"]]
)
def test_upstream_rejects_fork_profile_selector_before_dispatch(
    switch: SwitchFixture, selector: list[str]
) -> None:
    switch.select("upstream")
    result = switch.command("run", "claude", *selector)
    assert result.returncode == 2
    assert "upstream" in result.stderr.lower()
    assert "--fcc-profile" in result.stderr
    assert '"runtime"' not in result.stdout
    assert switch.changes() == []


def test_fork_profile_selector_is_passed_unchanged(switch: SwitchFixture) -> None:
    result = switch.command(
        "run", "claude", "--fcc-profile", "work", "--profile", "native"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["argv"] == [
        "--fcc-profile",
        "work",
        "--profile",
        "native",
    ]


@pytest.mark.parametrize("running", ["fork", "legacy"])
def test_status_proves_running_identity_independently_of_selection(
    switch: SwitchFixture, running: str
) -> None:
    switch.start(running)
    switch.select("upstream")
    switch.clear_events()
    status = switch.status()
    assert status["selected"] == "upstream"
    assert status["runtimes"]["upstream"]["version"] == "6.10.5"
    assert status["active_runtime"] == ("fork" if running == "fork" else "unknown")
    assert status["service"]["ActiveState"] == "active"
    assert int(status["service"]["MainPID"]) > 0
    assert switch.changes() == []


def test_backup_preserves_private_settings_credentials_and_sqlite_snapshot(
    switch: SwitchFixture,
) -> None:
    credentials = switch.config / "accounts/openai.json"
    credentials.parent.mkdir(mode=0o755)
    secret = '{"refresh_token":"synthetic-refresh-fixture"}'
    credentials.write_text(secret)
    credentials.chmod(0o600)
    other_files = {
        ".env": b"# synthetic fixture\nFCC_TEST_KEY=fixture-only\n",
        "profiles.json": b'{"profiles":{"work":{"model":"fixture/model"}}}\n',
    }
    for name, content in other_files.items():
        (switch.config / name).write_bytes(content)
        (switch.config / name).chmod(0o640)
    settings = (switch.config / "settings.json").read_bytes()
    database = switch.config / "account-state.sqlite3"
    with closing(sqlite3.connect(database)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE tokens(value TEXT)")
        writer.execute("INSERT INTO tokens VALUES ('synthetic-db-fixture')")
        writer.commit()
        assert Path(str(database) + "-wal").exists()
        result = switch.command("use", "upstream")
        assert result.returncode == 0, result.stdout + result.stderr
        snapshots = list((switch.state / "backups").glob("*/config"))
        assert len(snapshots) == 1
        snapshot = snapshots[0]
        assert (snapshot / "settings.json").read_bytes() == settings
        assert (snapshot / "accounts/openai.json").read_text() == secret
        for name, content in other_files.items():
            assert (snapshot / name).read_bytes() == content
        assert not list(snapshot.rglob("*-wal"))
        assert not list(snapshot.rglob("*-shm"))
        with closing(
            sqlite3.connect(
                f"file:{snapshot / database.name}?mode=ro&immutable=1", uri=True
            )
        ) as saved:
            assert saved.execute("SELECT value FROM tokens").fetchall() == [
                ("synthetic-db-fixture",)
            ]
    for path in [
        switch.state / "backups",
        snapshot.parent,
        snapshot,
        *snapshot.rglob("*"),
    ]:
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
    assert credentials.read_text() == secret
    assert stat.S_IMODE(credentials.stat().st_mode) == 0o600
    assert stat.S_IMODE(credentials.parent.stat().st_mode) == 0o755
    for name, content in other_files.items():
        assert (switch.config / name).read_bytes() == content
        assert stat.S_IMODE((switch.config / name).stat().st_mode) == 0o640
    assert (switch.config / "settings.json").read_bytes() == settings
    assert "synthetic-refresh-fixture" not in result.stdout + result.stderr
    assert not list(snapshot.rglob("*-wal"))
    assert not list(snapshot.rglob("*-shm"))


def test_symlink_backup_refusal_never_changes_external_target(
    switch: SwitchFixture,
) -> None:
    outside = switch.root / "outside-account.json"
    outside.write_text("synthetic external fixture")
    outside.chmod(0o640)
    (switch.config / "linked-account.json").symlink_to(outside)
    result = switch.command("use", "upstream")
    assert result.returncode == 1
    assert switch.manifest["selected"] == "fork"
    assert outside.read_text() == "synthetic external fixture"
    assert stat.S_IMODE(outside.stat().st_mode) == 0o640
    assert switch.status()["service"]["ActiveState"] == "inactive"
    assert "start" not in switch.changes()


def test_sqlite_snapshot_is_consistent_while_writer_commits(
    switch: SwitchFixture,
) -> None:
    database = switch.config / "live-accounts.db"
    stop = Event()
    ready = Event()
    failures: list[BaseException] = []

    def mutate() -> None:
        try:
            with closing(sqlite3.connect(database)) as writer:
                counter = 1
                while not stop.is_set():
                    writer.execute("UPDATE pairs SET value = ?", (counter,))
                    writer.commit()
                    counter += 1
                    ready.set()
                    stop.wait(0.001)
        except BaseException as error:
            failures.append(error)
            ready.set()

    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE pairs(id INTEGER, value INTEGER)")
        connection.executemany("INSERT INTO pairs VALUES (?, 0)", [(1,), (2,)])
        connection.commit()
        writer = Thread(target=mutate)
        writer.start()
        try:
            assert ready.wait(3)
            assert failures == []
            result = switch.command("use", "upstream")
        finally:
            stop.set()
            writer.join(timeout=3)
        assert not writer.is_alive()
        assert failures == []
        assert result.returncode == 0, result.stdout + result.stderr
        snapshots = list((switch.state / "backups").glob("*/config/live-accounts.db"))
        assert len(snapshots) == 1
        assert not list(snapshots[0].parent.glob("*-wal"))
        assert not list(snapshots[0].parent.glob("*-shm"))
        with closing(
            sqlite3.connect(f"file:{snapshots[0]}?mode=ro&immutable=1", uri=True)
        ) as saved:
            rows = saved.execute("SELECT value FROM pairs ORDER BY id").fetchall()
            assert len(rows) == 2
            assert rows[0] == rows[1]
            assert rows[0][0] >= 1
            assert saved.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert not list(snapshots[0].parent.glob("*-wal"))
        assert not list(snapshots[0].parent.glob("*-shm"))


def _installation(
    switch: SwitchFixture,
) -> tuple[list[str], dict[Path, tuple[bytes, int]]]:
    node = switch.root / "node-bin/node"
    _executable(node, 'print("fixture-node")\n')
    unit_dir = switch.root / "installed-units"
    bin_dir = switch.root / "installed-bin"
    unit_dir.mkdir()
    bin_dir.mkdir()
    originals = {
        unit_dir / "fcc-server.service": (
            b"[Service]\n# fixture-old-runtime=fork\n",
            0o640,
        ),
        bin_dir / "fcc-version": (b"#!/bin/sh\nprintf old-wrapper\n", 0o700),
        switch.state / "fcc-version.py": (b"# fixture old deployed switcher\n", 0o600),
        switch.state / "manifest.json": (
            (switch.state / "manifest.json").read_bytes(),
            0o600,
        ),
    }
    for path, (content, mode) in originals.items():
        path.write_bytes(content)
        path.chmod(mode)
    args = [
        "install",
        "--fork-bin",
        str(switch.root / "runtimes/fork/bin"),
        "--upstream-bin",
        str(switch.root / "runtimes/upstream/bin"),
        "--bin-dir",
        str(bin_dir),
        "--unit-dir",
        str(unit_dir),
        "--node-bin",
        str(node.parent),
    ]
    return args, originals


def _assert_installation_unchanged(originals: dict[Path, tuple[bytes, int]]) -> None:
    for path, (content, mode) in originals.items():
        assert path.read_bytes() == content, path
        assert stat.S_IMODE(path.stat().st_mode) == mode, path


def test_invalid_node_preflight_leaves_existing_installation_and_service_untouched(
    switch: SwitchFixture,
) -> None:
    args, originals = _installation(switch)
    switch.start()
    original_pid = switch.status()["service"]["MainPID"]
    switch.clear_events()
    args[-1] = str(switch.root / "missing-node-bin")
    result = switch.command(*args)
    assert result.returncode == 1
    assert "node" in result.stderr.lower()
    _assert_installation_unchanged(originals)
    assert switch.changes() == []
    assert switch.status()["service"]["MainPID"] == original_pid
    assert not (switch.state / "backups").exists()


def test_invalid_unit_preflight_leaves_existing_installation_and_service_untouched(
    switch: SwitchFixture,
) -> None:
    args, originals = _installation(switch)
    switch.start()
    original_pid = switch.status()["service"]["MainPID"]
    (switch.root / "fail-verify").touch()
    switch.clear_events()
    result = switch.command(*args)
    assert result.returncode == 1
    assert (switch.root / "verified-unit").exists()
    _assert_installation_unchanged(originals)
    assert switch.changes() == []
    assert switch.status()["service"]["MainPID"] == original_pid
    assert not (switch.state / "backups").exists()


@pytest.mark.parametrize(
    "output",
    [
        "fcc-server 6.16",
        "fcc-server 6.16.0.dev1",
        "fcc-server 6.16.0rc1",
        "fcc-server 6.16.0-beta",
        "fcc-server 6.16.0+build",
        "fcc-server V6.16.0",
        "fcc-server vv6.16.0",
        "fcc-server 06.16.0",
        "fcc-server 6.016.0",
        "fcc-server 6.16.00",
        "fcc-server \N{FULLWIDTH DIGIT SIX}.16.0",
        "fcc-server 6.16.-1",
        "fcc-server unknown",
        "",
    ],
)
def test_invalid_upstream_version_preflight_does_not_modify_installation(
    switch: SwitchFixture,
    output: str,
) -> None:
    args, originals = _installation(switch)
    (switch.root / "version-upstream").write_text(output)
    result = switch.command(*args)
    assert result.returncode == 1
    assert "version" in result.stderr.lower()
    _assert_installation_unchanged(originals)
    assert switch.events() == []
    assert not (switch.state / "backups").exists()


@pytest.mark.parametrize(
    "output",
    [
        "fcc-server 6.10.5",
        "fcc-server 6.10.4",
        "fcc-server 6.16.0",
        "fcc-server v6.16.0",
        "0.0.0",
        "Free Claude Code official release 999.42.7",
    ],
)
def test_install_accepts_stable_upstream_versions_and_records_actual_output(
    switch: SwitchFixture,
    output: str,
) -> None:
    args, originals = _installation(switch)
    (switch.root / "version-upstream").write_text(output)
    result = switch.command(*args, "--no-start")
    assert result.returncode == 0, result.stdout + result.stderr
    assert switch.events() == []
    assert switch.manifest["runtimes"]["upstream"]["version"] == output
    assert switch.manifest["selected"] == "fork"
    previous = list((switch.state / "backups").glob("installation-*/manifest.json"))
    assert len(previous) == 1
    assert previous[0].read_bytes() == originals[switch.state / "manifest.json"][0]


def _active_env(switch: SwitchFixture, content: bytes, mode: int = 0o600) -> Path:
    path = Path(switch.env["HOME"]) / ".fcc/.env"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(mode)
    return path


def _new_upstream(switch: SwitchFixture, args: list[str]) -> Path:
    bin_path = switch.root / "runtimes/upstream-6.16.0/bin"
    bin_path.mkdir(parents=True)
    (bin_path / "python").symlink_to(sys.executable)
    for launcher in LAUNCHERS:
        _executable(bin_path / launcher, RUNTIME)
    (switch.root / "version-upstream-6.16.0").write_text("fcc-server 6.16.0")
    (switch.root / "schema-upstream-6.16.0").write_text("2")
    (switch.root / "schema-fork").write_text("1")
    args[args.index("--upstream-bin") + 1] = str(bin_path)
    return bin_path


def test_schema_round_trip_preserves_independent_runtime_edits(
    switch: SwitchFixture,
) -> None:
    args, _ = _installation(switch)
    _new_upstream(switch, args)
    fork_original = b"FCC_CONFIG_SCHEMA=1\nFCC_TEST_KEY=fork-original\n"
    env = _active_env(switch, fork_original)
    result = switch.command(*args, "--no-start")
    assert result.returncode == 0, result.stdout + result.stderr
    assert env.read_bytes() == fork_original
    assert switch.events() == []
    switch.start()
    result = switch.command("use", "upstream")
    assert result.returncode == 0, result.stdout + result.stderr
    assert b"FCC_CONFIG_SCHEMA=2" in env.read_bytes()
    upstream_edit = b"FCC_CONFIG_SCHEMA=2\nFCC_TEST_KEY=upstream-edited\n"
    env.write_bytes(upstream_edit)
    result = switch.command("use", "fork")
    assert result.returncode == 0, result.stdout + result.stderr
    assert env.read_bytes() == fork_original
    fork_edit = b"FCC_CONFIG_SCHEMA=1\nFCC_TEST_KEY=fork-edited\n"
    env.write_bytes(fork_edit)
    result = switch.command("use", "upstream")
    assert result.returncode == 0, result.stdout + result.stderr
    assert env.read_bytes() == upstream_edit
    result = switch.command("use", "fork")
    assert result.returncode == 0, result.stdout + result.stderr
    assert env.read_bytes() == fork_edit
    saved = switch.state / "envs"
    assert (saved / "fork.env").read_bytes() == fork_edit
    assert (saved / "upstream-6.16.0.env").read_bytes() == upstream_edit
    assert stat.S_IMODE(saved.stat().st_mode) == 0o700
    for path in saved.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not (switch.root / "overlap").exists()


def test_failed_switch_after_migration_restores_fork_environment_and_service(
    switch: SwitchFixture,
) -> None:
    args, _ = _installation(switch)
    _new_upstream(switch, args)
    original = b"FCC_CONFIG_SCHEMA=1\nFCC_TEST_KEY=fork-before-failed-switch\n"
    env = _active_env(switch, original, mode=0o640)
    result = switch.command(*args, "--no-start")
    assert result.returncode == 0, result.stdout + result.stderr
    switch.start()
    before = switch.manifest
    (switch.root / "fail-health-once-upstream-6.16.0").touch()
    result = switch.command("use", "upstream")
    assert result.returncode == 1, result.stdout + result.stderr
    assert (
        b"FCC_CONFIG_SCHEMA=2"
        in (switch.root / "migrated-upstream-6.16.0").read_bytes()
    )
    assert env.read_bytes() == original
    assert stat.S_IMODE(env.stat().st_mode) == 0o640
    assert switch.manifest == before
    status = switch.status()
    assert status["active_runtime"] == "fork"
    assert status["service"]["ActiveState"] == "active"
    assert not (switch.root / "overlap").exists()


def test_missing_older_environment_is_not_seeded_from_schema_two(
    switch: SwitchFixture,
) -> None:
    switch.select("upstream")
    manifest = switch.manifest
    manifest["runtimes"]["upstream"]["version"] = "fcc-server 6.16.0"
    manifest["env_owner"] = "upstream-6.16.0"
    (switch.state / "manifest.json").write_text(json.dumps(manifest))
    original = b"FCC_CONFIG_SCHEMA=2\nFCC_TEST_KEY=latest-only\n"
    env = _active_env(switch, original)
    switch.start("upstream")
    original_pid = switch.status()["service"]["MainPID"]
    switch.clear_events()
    result = switch.command("use", "fork")
    assert result.returncode == 1, result.stdout + result.stderr
    assert env.read_bytes() == original
    assert switch.manifest == manifest
    assert switch.status()["service"]["MainPID"] == original_pid
    assert switch.changes() == []
    assert not (switch.state / "envs/fork.env").exists()


@pytest.mark.parametrize("activate", [False, True])
def test_upstream_upgrade_preserves_old_version_environment_for_rollback(
    switch: SwitchFixture,
    activate: bool,
) -> None:
    switch.select("upstream")
    old_env = b"FCC_CONFIG_SCHEMA=1\nFCC_TEST_KEY=old-upstream-edited\n"
    env = _active_env(switch, old_env)
    args, originals = _installation(switch)
    unit = switch.root / "installed-units/fcc-server.service"
    unit.write_bytes(b"[Service]\n# fixture-old-runtime=upstream\n")
    originals[unit] = (unit.read_bytes(), 0o640)
    new_bin = _new_upstream(switch, args)
    switch.start("upstream")
    original_pid = switch.status()["service"]["MainPID"]
    switch.clear_events()
    result = switch.command(*args, *([] if activate else ["--no-start"]))
    assert result.returncode == 0, result.stdout + result.stderr
    assert switch.manifest["runtimes"]["upstream"]["version"] == "fcc-server 6.16.0"
    assert switch.manifest["runtimes"]["upstream"]["bin"] == str(new_bin)
    saved = switch.state / "envs"
    assert (saved / "upstream-6.10.5.env").read_bytes() == old_env
    assert b"FCC_CONFIG_SCHEMA=1" in (saved / "upstream-6.16.0.env").read_bytes()
    status = switch.status()
    if activate:
        assert b"FCC_CONFIG_SCHEMA=2" in env.read_bytes()
        assert status["active_runtime"] == "upstream"
        assert status["service"]["MainPID"] != original_pid
    else:
        assert env.read_bytes() == old_env
        assert switch.changes() == []
        assert status["service"]["MainPID"] == original_pid
        assert status["active_runtime"] == "unknown"
    previous = list((switch.state / "backups").glob("installation-*/manifest.json"))
    assert len(previous) == 1
    assert previous[0].read_bytes() == originals[switch.state / "manifest.json"][0]
    assert not (switch.root / "overlap").exists()


def test_failed_upstream_upgrade_after_migration_restores_old_env_and_running_version(
    switch: SwitchFixture,
) -> None:
    switch.select("upstream")
    old_env = b"# exact bytes retained\nFCC_CONFIG_SCHEMA=1\nFCC_TEST_KEY=old-edit\n"
    env = _active_env(switch, old_env, mode=0o640)
    args, originals = _installation(switch)
    unit = switch.root / "installed-units/fcc-server.service"
    unit.write_bytes(b"[Service]\n# fixture-old-runtime=upstream\n")
    originals[unit] = (unit.read_bytes(), 0o640)
    _new_upstream(switch, args)
    account = env.parent / "accounts.json"
    account.write_text('{"fixture_account":"before"}')
    switch.start("upstream")
    (switch.root / "fail-health-once-upstream-6.16.0").touch()
    (switch.root / "mutate-accounts-upstream-6.16.0").touch()
    switch.clear_events()
    result = switch.command(*args)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "restored" in (result.stdout + result.stderr).lower()
    _assert_installation_unchanged(originals)
    assert env.read_bytes() == old_env
    assert stat.S_IMODE(env.stat().st_mode) == 0o640
    assert not (switch.root / "fail-health-once-upstream-6.16.0").exists()
    assert (
        b"FCC_CONFIG_SCHEMA=2"
        in (switch.root / "migrated-upstream-6.16.0").read_bytes()
    )
    assert account.read_text() == '{"fixture_account":"refreshed"}'
    status = switch.status()
    assert status["selected"] == "upstream"
    assert status["active_runtime"] == "upstream"
    assert status["service"]["ActiveState"] == "active"
    assert status["runtimes"]["upstream"]["version"] == "6.10.5"
    assert not (switch.root / "overlap").exists()


def test_no_start_downgrade_keeps_new_env_owned_until_old_runtime_activates(
    switch: SwitchFixture,
) -> None:
    args, _ = _installation(switch)
    old_bin = args[args.index("--upstream-bin") + 1]
    _new_upstream(switch, args)
    original = b"FCC_CONFIG_SCHEMA=1\nFCC_TEST_KEY=original\n"
    env = _active_env(switch, original)
    result = switch.command(*args, "--no-start")
    assert result.returncode == 0, result.stdout + result.stderr
    result = switch.command("use", "upstream")
    assert result.returncode == 0, result.stdout + result.stderr
    latest_edit = b"FCC_CONFIG_SCHEMA=2\nFCC_TEST_KEY=latest-edit\n"
    env.write_bytes(latest_edit)
    original_pid = switch.status()["service"]["MainPID"]
    switch.clear_events()
    args[args.index("--upstream-bin") + 1] = old_bin
    result = switch.command(*args, "--no-start")
    assert result.returncode == 0, result.stdout + result.stderr
    assert env.read_bytes() == latest_edit
    assert switch.manifest["env_owner"] == "upstream-6.16.0"
    assert switch.events() == []
    assert switch.status()["service"]["MainPID"] == original_pid
    saved = switch.state / "envs"
    assert (saved / "upstream-6.16.0.env").read_bytes() == latest_edit
    assert (saved / "upstream-6.10.5.env").read_bytes() == original
    result = switch.command("use", "upstream")
    assert result.returncode == 0, result.stdout + result.stderr
    assert env.read_bytes() == original
    assert switch.manifest["env_owner"] == "upstream-6.10.5"
    assert switch.status()["active_runtime"] == "upstream"
    assert (saved / "upstream-6.16.0.env").read_bytes() == latest_edit
    assert not (switch.root / "overlap").exists()


def test_merged_fork_migrates_saved_schema_one_and_resumes_original_schema_two(
    switch: SwitchFixture,
) -> None:
    args, _ = _installation(switch)
    _new_upstream(switch, args)
    (switch.root / "version-fork").write_text("fcc-server 6.16.0.dev1")
    (switch.root / "schema-fork").write_text("2")
    fork_original = b"FCC_CONFIG_SCHEMA=1\nFCC_TEST_KEY=pre-merge-fork\n"
    env = _active_env(switch, fork_original)
    result = switch.command(*args, "--no-start")
    assert result.returncode == 0, result.stdout + result.stderr
    assert env.read_bytes() == fork_original
    result = switch.command("use", "upstream")
    assert result.returncode == 0, result.stdout + result.stderr
    original_edit = b"FCC_CONFIG_SCHEMA=2\nFCC_TEST_KEY=original-edited\n"
    env.write_bytes(original_edit)
    result = switch.command("use", "fork")
    assert result.returncode == 0, result.stdout + result.stderr
    assert env.read_bytes() == fork_original.replace(b"SCHEMA=1", b"SCHEMA=2")
    fork_edit = b"FCC_CONFIG_SCHEMA=2\nFCC_TEST_KEY=merged-fork-edited\n"
    env.write_bytes(fork_edit)
    result = switch.command("use", "upstream")
    assert result.returncode == 0, result.stdout + result.stderr
    assert env.read_bytes() == original_edit
    result = switch.command("use", "fork")
    assert result.returncode == 0, result.stdout + result.stderr
    assert env.read_bytes() == fork_edit
    assert switch.status()["active_runtime"] == "fork"
    assert switch.manifest["runtimes"]["fork"]["version"] == "fcc-server 6.16.0.dev1"
    assert (switch.state / "envs/fork.env").read_bytes() == fork_edit
    assert (switch.state / "envs/upstream-6.16.0.env").read_bytes() == original_edit
    launcher = switch.command(
        "run", "claude", "--fcc-profile", "work", "--resume", "fixture"
    )
    assert launcher.returncode == 0, launcher.stderr
    assert json.loads(launcher.stdout)["argv"] == [
        "--fcc-profile",
        "work",
        "--resume",
        "fixture",
    ]
    assert not (switch.root / "overlap").exists()


@pytest.mark.parametrize(
    "fork_version,accepts_two",
    [
        ("6.14.9", False),
        ("6.15.0", True),
        ("6.16.0.dev1", True),
    ],
)
def test_missing_fork_slot_seeds_schema_two_only_for_compatible_runtime(
    switch: SwitchFixture,
    fork_version: str,
    accepts_two: bool,
) -> None:
    manifest = switch.manifest
    manifest["selected"] = "upstream"
    manifest["runtimes"]["upstream"]["version"] = "fcc-server 6.16.0"
    manifest["runtimes"]["fork"]["version"] = "fcc-server " + fork_version
    (switch.state / "manifest.json").write_text(json.dumps(manifest))
    (switch.root / "schema-fork").write_text("2" if accepts_two else "1")
    original = b"FCC_CONFIG_SCHEMA=2\nFCC_TEST_KEY=original-schema-two\n"
    env = _active_env(switch, original)
    switch.start("upstream")
    original_pid = switch.status()["service"]["MainPID"]
    switch.clear_events()
    result = switch.command("use", "fork")
    assert result.returncode == (0 if accepts_two else 1), result.stdout + result.stderr
    assert env.read_bytes() == original
    if accepts_two:
        assert switch.status()["active_runtime"] == "fork"
        assert (switch.state / "envs/fork.env").read_bytes() == original
    else:
        assert "schema-1" in result.stderr
        assert switch.status()["service"]["MainPID"] == original_pid
        assert switch.changes() == []
        assert not (switch.state / "envs/fork.env").exists()


def test_missing_latest_original_slot_seeds_merged_fork_schema_two(
    switch: SwitchFixture,
) -> None:
    manifest = switch.manifest
    manifest["runtimes"]["upstream"]["version"] = "fcc-server 6.16.0"
    manifest["runtimes"]["fork"]["version"] = "fcc-server 6.16.0.dev1"
    (switch.state / "manifest.json").write_text(json.dumps(manifest))
    for role in ("fork", "upstream"):
        (switch.root / ("schema-" + role)).write_text("2")
    original = b"FCC_CONFIG_SCHEMA=2\nFCC_TEST_KEY=merged-fork-baseline\n"
    env = _active_env(switch, original)
    switch.start()
    result = switch.command("use", "upstream")
    assert result.returncode == 0, result.stdout + result.stderr
    assert env.read_bytes() == original
    assert (switch.state / "envs/upstream-6.16.0.env").read_bytes() == original
    assert (switch.state / "envs/fork.env").read_bytes() == original
    assert switch.status()["active_runtime"] == "upstream"


def test_saved_schema_two_slot_is_rejected_for_retained_original_6105(
    switch: SwitchFixture,
) -> None:
    manifest = switch.manifest
    manifest["runtimes"]["fork"]["version"] = "fcc-server 6.16.0.dev1"
    (switch.state / "manifest.json").write_text(json.dumps(manifest))
    (switch.root / "schema-fork").write_text("2")
    original = b"FCC_CONFIG_SCHEMA=2\nFCC_TEST_KEY=merged-fork\n"
    env = _active_env(switch, original)
    saved = switch.state / "envs/upstream-6.10.5.env"
    saved.parent.mkdir(mode=0o700)
    saved.write_bytes(b"FCC_CONFIG_SCHEMA=2\nFCC_TEST_KEY=incompatible-old-slot\n")
    before = saved.read_bytes()
    switch.start()
    original_pid = switch.status()["service"]["MainPID"]
    switch.clear_events()
    result = switch.command("use", "upstream")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "schema-1" in result.stderr
    assert env.read_bytes() == original
    assert saved.read_bytes() == before
    assert switch.manifest == manifest
    assert switch.status()["service"]["MainPID"] == original_pid
    assert switch.changes() == []


@pytest.mark.parametrize("initial_runtime", [None, "fork", "legacy"])
def test_failed_installer_activation_restores_old_artifacts_and_service_state(
    switch: SwitchFixture, initial_runtime: str | None
) -> None:
    args, originals = _installation(switch)
    if initial_runtime == "legacy":
        unit = switch.root / "installed-units/fcc-server.service"
        content = b"[Service]\n# fixture-old-runtime=legacy\n"
        unit.write_bytes(content)
        originals[unit] = (content, 0o640)
    if initial_runtime is not None:
        switch.start(initial_runtime)
    (switch.root / "fail-health-once-fork").touch()
    switch.clear_events()
    result = switch.command(*args)
    assert result.returncode == 1, result.stdout + result.stderr
    _assert_installation_unchanged(originals)
    status = switch.status()
    assert status["selected"] == "fork"
    assert status["service"]["ActiveState"] == (
        "active" if initial_runtime is not None else "inactive"
    )
    expected_runtime = "unknown" if initial_runtime == "legacy" else initial_runtime
    assert status["active_runtime"] == expected_runtime
    assert "daemon-reload" in switch.changes()
    assert "restored" in (result.stdout + result.stderr).lower()
    assert not (switch.root / "overlap").exists()


def test_installer_no_start_has_no_service_actions_and_preserves_existing_launchers(
    switch: SwitchFixture,
) -> None:
    args, _ = _installation(switch)
    launcher = switch.root / "installed-bin/fcc-claude"
    launcher.write_text("fixture original launcher")
    launcher.chmod(0o700)
    result = switch.command(*args, "--no-start")
    assert result.returncode == 0, result.stdout + result.stderr
    assert switch.events() == []
    assert launcher.read_text() == "fixture original launcher"
    assert stat.S_IMODE(launcher.stat().st_mode) == 0o700
    assert (switch.root / "installed-bin/fcc-version").is_file()
    assert (switch.state / "fcc-version.py").is_file()
    assert switch.manifest["selected"] == "fork"
    assert "revision" not in switch.manifest["runtimes"]["upstream"]


def test_shell_installer_forwards_fixture_paths_without_service_activation(
    switch: SwitchFixture,
) -> None:
    args, _ = _installation(switch)
    result = subprocess.run(
        ["/bin/bash", str(INSTALLER), *switch.argv()[2:], *args[1:], "--no-start"],
        env=switch.env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert switch.events() == []
    assert switch.manifest["selected"] == "fork"
    assert (switch.root / "installed-bin/fcc-version").is_file()
