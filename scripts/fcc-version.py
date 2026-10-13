#!/usr/bin/env python3
"""Select one FCC runtime behind one user service, preserving its managed settings."""

import argparse
import fcntl
import json
import os
import re
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

SERVICE = "fcc-server.service"
LAUNCHERS = ("claude", "codex", "pi", "opencode")
RELEASE_VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"


class SwitchError(Exception):
    """An operation failed without permission to alter unrelated processes."""


def private_dir(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def atomic_write(path, text, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            temporary.chmod(mode)
            handle.write(text.encode("utf-8") if isinstance(text, str) else text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def switch_lock(state):
    private_dir(state)
    with (state / "switch.lock").open("a") as handle:
        (state / "switch.lock").chmod(0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise SwitchError("Another FCC version switch is in progress.") from error
        yield


def read_manifest(state):
    try:
        manifest = json.loads((state / "manifest.json").read_text())
        if manifest["schema"] != 1 or manifest["service"] != SERVICE:
            raise ValueError("unsupported manifest")
        if manifest["selected"] not in {"fork", "upstream"}:
            raise ValueError("invalid selection")
        for runtime in ("fork", "upstream"):
            if not Path(manifest["runtimes"][runtime]["bin"]).is_absolute():
                raise ValueError("runtime bin must be absolute")
        if "env_owner" in manifest:
            validate_env_key(manifest["env_owner"])
        return manifest
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SwitchError(
            "Missing or invalid manifest; run fcc-versions-install.sh."
        ) from error


def save_manifest(state, manifest):
    atomic_write(state / "manifest.json", json.dumps(manifest, indent=2) + "\n")


def validate_env_key(key):
    if not isinstance(key, str) or not re.fullmatch(
        rf"fork|upstream-{RELEASE_VERSION}", key
    ):
        raise SwitchError("Invalid managed environment identity.")
    return key


def env_key(manifest, runtime):
    if runtime == "fork":
        return "fork"
    version = manifest["runtimes"][runtime]["version"].split()[-1]
    return validate_env_key("upstream-" + version.removeprefix("v"))


def env_owner(manifest):
    return validate_env_key(
        manifest.get("env_owner", env_key(manifest, manifest["selected"]))
    )


def read_env(path):
    if path.is_symlink() or path.parent.is_symlink():
        raise SwitchError("Managed environment must not be a symlink.")
    if not path.exists():
        return None
    if not path.is_file():
        raise SwitchError("Managed environment must be a regular file.")
    return path.read_bytes(), path.stat().st_mode & 0o777


def restore_env(path, original):
    read_env(path)  # Refuse newly introduced symlinks before replacing anything.
    if original is None:
        path.unlink(missing_ok=True)
    else:
        atomic_write(path, original[0], original[1])


def schema_one(original):
    return (
        original is not None
        and re.search(
            rb"(?m)^\s*(?:export\s+)?FCC_CONFIG_SCHEMA\s*=\s*(?:1|\"1\"|'1')\s*(?:#.*)?$",
            original[0],
        )
        is not None
    )


def supports_schema_two(manifest, runtime):
    version = manifest["runtimes"][runtime]["version"].split()[-1]
    match = re.match(r"v?(\d+)\.(\d+)\.(\d+)(?:\D|$)", version)
    return match is not None and tuple(map(int, match.groups())) >= (6, 15, 0)


def compatible_env(original, manifest, runtime):
    if schema_one(original):
        return True
    return (
        original is not None
        and supports_schema_two(manifest, runtime)
        and re.search(
            rb"(?m)^\s*(?:export\s+)?FCC_CONFIG_SCHEMA\s*=\s*(?:2|\"2\"|'2')\s*(?:#.*)?$",
            original[0],
        )
        is not None
    )


def prepare_env(state, previous, manifest, target, original):
    """Save the current owner and preflight a target before stopping its service."""
    directory = state / "envs"
    if directory.is_symlink():
        raise SwitchError("Managed environment snapshots must not be symlinks.")
    private_dir(directory)
    owner = env_owner(previous) if previous else "fork"
    current_slot = directory / (owner + ".env")
    read_env(current_slot)
    if original is not None:
        atomic_write(current_slot, original[0])
    fork_slot = directory / "fork.env"
    baseline = read_env(fork_slot)
    if baseline is None and compatible_env(original, manifest, "fork"):
        atomic_write(fork_slot, original[0])
        baseline = original
    key = env_key(manifest, target)
    slot = directory / (key + ".env")
    saved = read_env(slot)
    if saved is None:
        saved = original if compatible_env(original, manifest, target) else baseline
        if saved is not None and not compatible_env(saved, manifest, target):
            saved = None
        if saved is None and original is not None:
            raise SwitchError(
                "No saved schema-1 managed environment is available for this runtime; "
                "refusing to reuse migrated settings."
            )
        if saved is not None:
            atomic_write(slot, saved[0])
    elif not compatible_env(saved, manifest, target):
        raise SwitchError(
            "Saved settings are incompatible with this runtime; "
            "older runtimes require a schema-1 managed environment."
        )
    return key, (saved[0], 0o600) if saved is not None else None


def executable(manifest, runtime, name):
    path = Path(manifest["runtimes"][runtime]["bin"]) / name
    if not path.is_file() or not os.access(path, os.X_OK):
        raise SwitchError(f"Runtime executable is unavailable: {path}")
    return path


def systemctl(args, command):
    try:
        result = subprocess.run(
            [args.systemctl, "--user", *command],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SwitchError(f"systemctl {command[0]} failed.") from error
    if result.returncode:
        raise SwitchError(f"systemctl {command[0]} failed (exit {result.returncode}).")
    return result.stdout


def service_state(args):
    output = systemctl(
        args,
        ["show", SERVICE, "-p", "ActiveState", "-p", "SubState", "-p", "MainPID"],
    )
    values = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    if not all(key in values for key in ("ActiveState", "SubState", "MainPID")):
        raise SwitchError(
            "Service state is incomplete; refusing to infer its identity."
        )
    try:
        values["MainPID"] = int(values["MainPID"])
    except ValueError as error:
        raise SwitchError("Service MainPID is invalid.") from error
    return values


def service_command(service):
    pid = service["MainPID"]
    if not pid:
        return None
    try:
        command = (Path("/proc") / str(pid) / "cmdline").read_bytes().split(b"\0")
    except OSError:
        return None
    return [os.fsdecode(value) for value in command if value][:2]


def active_runtime(manifest, service):
    if not service["MainPID"]:
        return None
    arguments = service_command(service) or []
    for runtime, info in manifest["runtimes"].items():
        if str(Path(info["bin"]) / "fcc-server") in arguments[:2]:
            return runtime
    return "unknown"


def health_address(manifest, args):
    url = args.health_url or manifest["health_url"]
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.path != "/health"
        or parsed.query
        or parsed.fragment
    ):
        raise SwitchError("Health URL must be a local HTTP /health endpoint.")
    return url, parsed.hostname, parsed.port or 80


def healthy(url):
    # Local health checks must not follow redirects or use shell proxy settings.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(url, timeout=0.5) as response:
            return (
                response.status == 200
                and json.load(response).get("status") == "healthy"
            )
    except OSError, ValueError, AttributeError:
        return False


def wait_healthy(manifest, args, runtime, *, prior_command=None):
    url, _, _ = health_address(manifest, args)
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        service = service_state(args)
        matching = (
            service_command(service) == prior_command
            if prior_command is not None
            else active_runtime(manifest, service) == runtime
        )
        if service["ActiveState"] == "active" and matching and healthy(url):
            return
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    raise SwitchError(f"{runtime} did not become healthy with a matching service PID.")


def stop_service(args):
    systemctl(args, ["stop", SERVICE])
    service = service_state(args)
    if service["MainPID"] or service["ActiveState"] not in {"inactive", "failed"}:
        raise SwitchError("FCC service did not stop fully; selection is unchanged.")


def ensure_port_free(manifest, args):
    _, host, port = health_address(manifest, args)
    try:
        with socket.create_connection((host, port), timeout=0.5):
            pass
    except ConnectionRefusedError:
        return
    except OSError as error:
        raise SwitchError("Cannot verify the configured local port.") from error
    raise SwitchError(f"Port {port} is occupied; no unrelated process was stopped.")


def backup_config(config, state, timeout):
    if state.resolve().is_relative_to(config.resolve()):
        raise SwitchError(
            "Switcher state must be outside the shared FCC configuration."
        )
    destination = (
        state / "backups" / (time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex)
    )
    private_dir(destination)
    snapshot = destination / "config"
    private_dir(snapshot)
    if not config.exists():
        return destination
    if config.is_symlink() or not config.is_dir():
        raise SwitchError("Shared FCC configuration must be a real directory.")
    paths = sorted(config.rglob("*"))
    databases = set()
    for path in paths:
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise SwitchError(
                "FCC backup contains a symlink or special file; refusing unsafe copy."
            )
        if path.is_file():
            with path.open("rb") as handle:
                if handle.read(16) == b"SQLite format 3\x00":
                    databases.add(path)
    deadline = time.monotonic() + timeout

    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise SwitchError("SQLite backup timed out.")

    for path in paths:
        target = snapshot / path.relative_to(config)
        if path.is_dir():
            private_dir(target)
            continue
        if any(
            str(path).endswith(suffix) and Path(str(path)[: -len(suffix)]) in databases
            for suffix in ("-wal", "-shm", "-journal")
        ):
            continue
        if path in databases:
            with (
                closing(
                    sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
                ) as source,
                closing(sqlite3.connect(target)) as output,
            ):
                source.backup(output, pages=128, progress=progress)
                output.execute("PRAGMA journal_mode=DELETE")
        else:
            shutil.copyfile(path, target)
        target.chmod(0o600)
    return destination


def status(args, manifest):
    service = service_state(args)
    print(
        json.dumps(
            {
                "selected": manifest["selected"],
                "runtimes": manifest["runtimes"],
                "service": {"name": SERVICE, **service},
                "active_runtime": active_runtime(manifest, service),
                "config_dir": args.config_dir or manifest["config_dir"],
                "health_url": args.health_url or manifest["health_url"],
            },
            indent=2,
        )
    )


def use(args, state, manifest):
    target = args.runtime
    executable(manifest, target, "fcc-server")
    health_address(manifest, args)
    previous = manifest["selected"]
    service = service_state(args)
    was_active = service["ActiveState"] == "active"
    if (
        target == previous
        and was_active
        and active_runtime(manifest, service) == target
    ):
        wait_healthy(manifest, args, target)
        status(args, manifest)
        return
    env_path = Path.home() / ".fcc" / ".env"
    original_env = read_env(env_path)
    previous_owner = manifest.get("env_owner")
    target_key, target_env = prepare_env(
        state, manifest, manifest, target, original_env
    )
    if service["MainPID"] or service["ActiveState"] not in {"inactive", "failed"}:
        stop_service(args)
    else:
        # No managed process exists; reject a manual listener before any mutation.
        ensure_port_free(manifest, args)
    try:
        ensure_port_free(manifest, args)
        backup = backup_config(
            Path(args.config_dir or manifest["config_dir"]).resolve(),
            state,
            args.timeout,
        )
        print(f"Private FCC snapshot: {backup}", file=sys.stderr)
        restore_env(env_path, target_env)
        manifest["selected"] = target
        manifest["env_owner"] = target_key
        save_manifest(state, manifest)
        systemctl(args, ["start", SERVICE])
        wait_healthy(manifest, args, target)
    except Exception as error:
        rollback_error = None
        try:
            stop_error = None
            try:
                current = service_state(args)
                if current["MainPID"] or current["ActiveState"] not in {
                    "inactive",
                    "failed",
                }:
                    stop_service(args)
            except SwitchError as failed:
                stop_error = failed
            manifest["selected"] = previous
            if previous_owner is None:
                manifest.pop("env_owner", None)
            else:
                manifest["env_owner"] = previous_owner
            save_manifest(state, manifest)
            if stop_error is not None:
                raise stop_error
            restore_env(env_path, original_env)
            if was_active:
                systemctl(args, ["start", SERVICE])
                wait_healthy(manifest, args, previous)
        except Exception as failed:
            rollback_error = failed
        if rollback_error:
            raise SwitchError(
                f"Switch failed: {error} Rollback also failed: {rollback_error} "
                "Shared authentication and databases were not restored."
            ) from error
        raise SwitchError(
            f"Switch failed: {error} Rollback restored prior selection and service state; "
            "The prior managed environment was restored; shared authentication and databases were not restored."
        ) from error
    status(args, manifest)


def run(args, manifest):
    command = executable(manifest, manifest["selected"], f"fcc-{args.launcher}")
    os.execv(command, [str(command), *args.native_args])


def runtime_info(bin_dir, runtime):
    server = bin_dir / "fcc-server"
    if not server.is_file() or not os.access(server, os.X_OK):
        raise SwitchError(f"Runtime executable is unavailable: {server}")
    result = subprocess.run(
        [str(server), "--version"], capture_output=True, text=True, timeout=10
    )
    if result.returncode or not result.stdout.strip():
        raise SwitchError(f"Cannot read {runtime} runtime version.")
    version = result.stdout.strip()
    if runtime == "upstream" and not re.fullmatch(
        rf"v?{RELEASE_VERSION}",
        version.split()[-1],
    ):
        raise SwitchError(
            "Upstream runtime must report a stable release version X.Y.Z."
        )
    return {
        "bin": str(bin_dir),
        "version": version,
        "source": str(
            bin_dir.parent.parent if bin_dir.parent.name == ".venv" else bin_dir.parent
        ),
    }


def systemd_quote(value):
    if "\n" in value or "\r" in value:
        raise SwitchError("Installation paths cannot contain line breaks.")
    return (
        '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'
    )


def install(args, state):
    fork = Path(args.fork_bin).expanduser().resolve()
    upstream = Path(args.upstream_bin).expanduser().resolve()
    runtimes = {
        "fork": runtime_info(fork, "fork"),
        "upstream": runtime_info(upstream, "upstream"),
    }
    interpreter = fork / "python"
    if not interpreter.is_file() or not os.access(interpreter, os.X_OK):
        raise SwitchError("Fork runtime must provide an executable Python interpreter.")
    config = Path(args.config_dir or Path.home() / ".fcc").expanduser().resolve()
    url = args.health_url or "http://127.0.0.1:8082/health"
    manifest = {
        "schema": 1,
        "selected": "fork",
        "service": SERVICE,
        "config_dir": str(config),
        "health_url": url,
        "runtimes": runtimes,
    }
    previous_manifest = None
    if (state / "manifest.json").exists():
        previous_manifest = read_manifest(state)
        manifest["selected"] = previous_manifest["selected"]
        manifest["env_owner"] = env_owner(previous_manifest)
    health_address(manifest, args)
    node = args.node_bin or str(Path(shutil.which("node") or "").parent)
    if not node or node == "." or not (Path(node) / "node").is_file():
        raise SwitchError("Supply --node-bin with the installed Node.js directory.")
    bin_dir = Path(args.bin_dir).expanduser().resolve()
    unit_dir = Path(args.unit_dir).expanduser().resolve()
    unit = unit_dir / SERVICE
    wrapper = bin_dir / "fcc-version"
    deployed = state / "fcc-version.py"
    wrapper_text = (
        "#!/bin/sh\nexec "
        + " ".join(
            shlex.quote(str(value))
            for value in (interpreter, deployed, "--state-dir", state)
        )
        + ' "$@"\n'
    )
    path = f"{node}:{Path.home() / '.local/bin'}:{Path.home() / '.cargo/bin'}:/usr/local/bin:/usr/bin:/bin"
    unit_text = (
        "[Unit]\nDescription=Free Claude Code selected runtime\n\n[Service]\n"
        "Type=exec\nWorkingDirectory=%h\nExecStart="
        + " ".join(
            systemd_quote(str(value)).replace("$", "$$")
            for value in (interpreter, deployed, "--state-dir", state, "serve")
        )
        + "\nEnvironment="
        + systemd_quote("PATH=" + path)
        + "\nRestart=on-failure\nRestartSec=5s\nTimeoutStopSec=45s\n\n"
        "[Install]\nWantedBy=default.target\n"
    )
    with tempfile.TemporaryDirectory(prefix="verify-", dir=state) as temporary:
        staged_unit = Path(temporary) / SERVICE
        staged_unit.write_text(unit_text)
        verified = subprocess.run(
            [args.systemd_analyze, "--user", "verify", str(staged_unit)],
            capture_output=True,
            timeout=30,
        )
        if verified.returncode:
            raise SwitchError(
                "Rendered systemd unit failed verification; installation is unchanged."
            )
    backup = state / "backups" / ("installation-" + uuid.uuid4().hex)
    private_dir(backup)
    previous_files = {}
    for path in (unit, wrapper, state / "manifest.json", deployed):
        if path.is_symlink():
            raise SwitchError(
                "Existing installation target is a symlink; preserve it manually first."
            )
        previous_files[path] = (
            (path.read_text(), path.stat().st_mode & 0o777) if path.exists() else None
        )
        if path.exists():
            shutil.copyfile(path, backup / path.name)
            (backup / path.name).chmod(0o600)
    previous_service = service_state(args) if not args.no_start else None
    prior_command = service_command(previous_service) if previous_service else None
    was_active = previous_service and previous_service["ActiveState"] == "active"
    if was_active and not prior_command:
        raise SwitchError(
            "Cannot record prior service identity; installation is unchanged."
        )
    env_path = Path.home() / ".fcc" / ".env"
    original_env = read_env(env_path)
    target_key, target_env = prepare_env(
        state, previous_manifest, manifest, manifest["selected"], original_env
    )
    try:
        if not args.no_start:
            stop_service(args)
            ensure_port_free(manifest, args)
            backup_config(config, state, args.timeout)
            restore_env(env_path, target_env)
            manifest["env_owner"] = target_key
        atomic_write(deployed, Path(__file__).read_text())
        atomic_write(wrapper, wrapper_text, mode=0o700)
        atomic_write(unit, unit_text)
        save_manifest(state, manifest)
        if not args.no_start:
            systemctl(args, ["daemon-reload"])
            systemctl(args, ["start", SERVICE])
            wait_healthy(manifest, args, manifest["selected"])
    except Exception as error:
        rollback_error = None
        try:
            stop_error = None
            if not args.no_start:
                try:
                    stop_service(args)
                except SwitchError as failed:
                    stop_error = failed
            for path, original in previous_files.items():
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, original[0], mode=original[1])
            if not args.no_start:
                systemctl(args, ["daemon-reload"])
                if stop_error is not None:
                    raise stop_error
                restore_env(env_path, original_env)
                if was_active:
                    systemctl(args, ["start", SERVICE])
                    wait_healthy(
                        manifest, args, "prior service", prior_command=prior_command
                    )
        except Exception as failed:
            rollback_error = failed
        if rollback_error:
            raise SwitchError(
                f"Installation failed: {error} Rollback also failed: {rollback_error}"
            ) from error
        raise SwitchError(
            f"Installation failed: {error} Rollback restored prior files and service state."
        ) from error
    print(f"Installed {wrapper}; prior installation saved privately in {backup}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state-dir", default=str(Path.home() / ".local/share/fcc-versions")
    )
    parser.add_argument(
        "--config-dir",
        help="Snapshot directory override; never changes runtime HOME/.fcc",
    )
    parser.add_argument("--systemctl", default="systemctl")
    parser.add_argument("--systemd-analyze", default="systemd-analyze")
    parser.add_argument("--health-url")
    parser.add_argument("--timeout", type=float, default=30)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    switch = commands.add_parser("use")
    switch.add_argument("runtime", choices=("upstream", "fork"))
    launch = commands.add_parser("run")
    launch.add_argument("launcher", choices=LAUNCHERS)
    launch.add_argument("native_args", nargs=argparse.REMAINDER)
    commands.add_parser("serve")
    installer = commands.add_parser("install")
    installer.add_argument("--fork-bin", required=True)
    installer.add_argument("--upstream-bin", required=True)
    installer.add_argument("--bin-dir", default=str(Path.home() / ".local/bin"))
    installer.add_argument(
        "--unit-dir", default=str(Path.home() / ".config/systemd/user")
    )
    installer.add_argument("--node-bin")
    installer.add_argument("--no-start", action="store_true")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    os.umask(0o077)
    state = Path(args.state_dir).expanduser().resolve()
    try:
        if args.command == "install":
            with switch_lock(state):
                install(args, state)
            return 0
        if args.command == "use":
            with switch_lock(state):
                use(args, state, read_manifest(state))
            return 0
        manifest = read_manifest(state)
        if args.command == "status":
            status(args, manifest)
        elif args.command == "run":
            if manifest["selected"] == "upstream" and any(
                value == "--fcc-profile" or value.startswith("--fcc-profile=")
                for value in args.native_args
            ):
                parser.error(
                    "Selected upstream runtime does not support --fcc-profile; select fork."
                )
            run(args, manifest)
        elif args.command == "serve":
            command = executable(manifest, manifest["selected"], "fcc-server")
            os.execv(command, [str(command)])
    except (SwitchError, OSError, sqlite3.Error, subprocess.TimeoutExpired) as error:
        print(f"fcc-version: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
