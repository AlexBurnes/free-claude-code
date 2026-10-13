# Compare the FCC fork with unmodified upstream

Keep two separate Python environments and switch a single `fcc-server.service` between them. Both versions use the same `~/.fcc` OAuth account files and databases, with independent managed `.env` settings. Match default Model Config explicitly when comparing them. The switcher does not run two managed servers concurrently.

## Runtime installations

The current reference installation is unmodified upstream tag **v6.16.0**, commit `4e99ddaa5cf453d4ef22fae7c17f7f3aacc3c52b`. The prior v6.10.5 checkout remains available for rollback. Both are pinned releases rather than a moving branch. A successful local health check does not establish that upstream fixes a reported provider API error.

From the fork checkout:

```bash
uv sync --frozen

git clone --branch v6.16.0 --depth 1 \
  https://github.com/Alishahryar1/free-claude-code.git ../free-claude-code-upstream-6.16.0
uv sync --project ../free-claude-code-upstream-6.16.0 --frozen --no-dev
```

Use the fork checkout's `.venv/bin` and the upstream checkout's `.venv/bin`. After merging v6.16.0 into the fork, both active comparison environments use the upstream v6.16.0 lock. The retained v6.10.5 environment uses its older lock. The older `uv tool` installation has newer dependency versions, including OpenAI SDK, FastAPI and Pydantic, and is therefore a separate variable in debugging. It remains installed; the comparison dispatcher uses the two checkout environments.

The fork is editable and includes local changes. Updating its source changes the fork runtime after a service restart. Do not run `fcc-update` during the comparison: it targets upstream and can invalidate the pinned baseline. Do not delete or move either checkout while its environment is registered.

## Install the switcher

Install once from the fork checkout, as the regular user without sudo. Supply absolute runtime paths:

```bash
bash scripts/fcc-versions-install.sh \
  --fork-bin "$PWD/.venv/bin" \
  --upstream-bin "$(realpath ../free-claude-code-upstream-6.16.0/.venv/bin)"
```

The installer provides `fcc-version` and configures the existing `fcc-server.service`. Selection persists for later restarts. The installer preserves the service's existing enabled/disabled autostart setting. Use `systemctl --user enable fcc-server.service` if you want login startup and it is not already enabled. It keeps a private backup of the preceding unit. No provider secrets are placed in the unit. Once installed, use this switcher rather than rerunning the older `fcc-systemd-user.sh` installer, which would replace its service command. The default health address is `http://127.0.0.1:8082/health`; pass `--health-url` if FCC uses a different port, and update the registration if that port changes. The installed dispatcher is pinned to the fork environment's Python interpreter.

## Switch and launch

```bash
fcc-version status
fcc-version use upstream
fcc-version run claude

# Return to the fork.
fcc-version use fork
fcc-version run claude --fcc-profile openai
```

Use `fcc-version run claude`, `codex`, `pi`, or `opencode` when comparing versions. It dispatches the selected version's launcher and preserves native arguments. On this machine, `fcc-claude` dispatches through `fcc-version run claude`, selecting the matching launcher. Other existing commands and generated profile wrappers still belong to their previously installed package; the generic switcher installer does not replace those commands. Upstream does not support the fork's `--fcc-profile` option. For upstream comparisons, choose matching models and reasoning in each runtime's default Model Config.

Both upstream v6.10.5 and v6.16.0 force Claude's auto-compact window to 190000 tokens in its launcher. This fork has the editable Claude Auto-compact Window setting. A long conversation can therefore compact earlier under upstream; record compaction when comparing it, and use fresh short conversations for the initial check.

Switch between completed requests. Stop or pause agent work first; switching interrupts in-flight requests. Relaunch named-profile agents after returning to the fork because a server restart clears their session bindings. Claude's native conversation history stays in its normal location, so native `--resume` can reopen a conversation; the history may itself contain the replay data responsible for an API rejection.

The switcher stops the service fully before starting the other version, serializes switching, checks readiness, and attempts to return to the previous selection/service after a failed startup. It refuses to take over an occupied port and does not kill an unrelated process. `status` distinguishes the stored selection from the process actually running. Inspect status after any manual service operation. The short Admin UI version label is not a unique build identifier; use the registered source/environment paths and running process identity for comparison.

## Shared state and backups

The versions share `~/.fcc` OAuth account files, profiles, databases and application logs. Their managed `.env` files are preserved separately under the private switcher `envs/` directory and restored only after stopping the server. API keys, default routing and settings edited in Admin UI remain with that runtime. Upstream v6.16.0 and the merged fork migrate `.env` to schema2; the retained older original v6.10.5 requires schema1. Missing slots may be seeded only from settings compatible with the registered target version. Initial slots copy the previous settings, then evolve independently. Match settings explicitly during an A/B test. Named profile storage remains useful only in the fork.

Before switching, the tool creates a private backup of FCC state under `~/.local/share/fcc-versions/backups/`. SQLite databases are copied through the SQLite backup API so the snapshot is consistent even when another client writes. Symbolic links and special files in FCC storage are refused rather than followed. Backups contain credentials: do not commit them, send them to colleagues, or paste their contents into an issue. Failed startup rollback restores the preceding runtime and its exact pre-action managed `.env`. OAuth credentials and databases are not overwritten by rollback, preserving account refreshes. Restoring other state is a separate deliberate operation with the server stopped.

## API comparison procedure

1. Record `fcc-version status`, the default model, tier overrides, reasoning policy and fallback order. Use the same provider/account and route in both versions. Disable fallbacks only if testing that specific route; record that choice.
2. Run the fork with a fresh Claude conversation and a small reproducible prompt. Record the UTC time, provider/model, HTTP error category and request ID. Do not publish credentials or encrypted reasoning contents.
3. Switch to upstream and run the same prompt in a fresh conversation using `fcc-version run claude`. Verify the same route in server diagnostics/logs.
4. If fresh conversations succeed but the existing conversation fails, compare the problematic history separately using native `--resume`. Preserve the transcript; do not edit or delete opaque reasoning records as a first diagnostic step.
5. Return to the fork, relaunch profile-bound agents, and verify readiness with `fcc-version status`.

The shared application log remains `~/.fcc/logs/server.log`. Capture timestamps around each switch and correlate them with the selected runtime and request IDs. Use `journalctl --user -u fcc-server.service` for startup/shutdown evidence. A switch and `/health` success only confirm local startup; real provider acceptance must be verified separately.

## Local validation

37 fixture tests pass, including concurrent-switch exclusion, failed-switch and failed-install rollback, manual port ownership, exact launcher arguments, private credentials/settings snapshots and concurrent SQLite backup. Ruff lint/format, ty, Bash syntax and generated systemd unit verification pass. Validation results and deployment status are tracked under the `fcc-version-switch-2026-10-13` task in `TASKS.md`. Upstream and fork startup checks use temporary homes, loopback ports and no cloud accounts. Switcher tests use fixture services and do not send inference requests.

On 2026-10-13 the user approved live installation and requested that upstream remain running. `~/.local/bin/fcc-version` and the user-service unit are installed. Real switching completed successfully: fork → upstream → fork → upstream. Each runtime passed `/health` and Admin HTTP checks, only the current service owned port 8082, and the preceding main process exited. After that initial deployment, selection and active runtime were `upstream` (v6.10.5); automatic startup remains enabled. Settings, authentication and profile file hashes remained unchanged; backup permissions and the systemd environment-assignment audit passed. `fcc-version run claude --version` succeeded. No real inference request was sent, so provider API acceptance remains a separate comparison step. See [the Russian switching guide](version-switching-ru.md).

## Update and rollback

On 2026-10-13 a separate clean v6.16.0 checkout was prepared; see [upstream update findings](upstream-update-2026-10-13.md). Re-register the desired original release by rerunning the switcher installer with its absolute `--upstream-bin`. This preserves the selection and uses a version-specific upstream configuration slot. The old checkout remains `../free-claude-code-upstream/.venv/bin`. Do not replace its source in place.

```bash
# Re-register the retained original v6.10.5 for rollback.
bash scripts/fcc-versions-install.sh \
  --fork-bin "$PWD/.venv/bin" \
  --upstream-bin "$(realpath ../free-claude-code-upstream/.venv/bin)"
fcc-version use upstream
```

Use the v6.16.0 path from the installation example to return to the updated release. Switching/re-registering restarts the server and interrupts current requests. Configuration snapshots contain credentials; keep them private.

The update is installed and verified: upstream v6.16.0 → fork → upstream v6.16.0, with schema2/1/2 settings restoration. Final active runtime is upstream v6.16.0; autostart is enabled. 63 fixture cases and independent serial verification passed. Protected account/profile hashes remained unchanged; the old original's schema1 slot matches its pre-update baseline. The local `fcc-claude` dispatcher works in both versions. Reinstalling the old uv-tool package can replace that dispatcher; use `fcc-version run claude` as the canonical matching launch command. No real provider inference was sent.

## Fork merged with v6.16.0 (2026-10-13)

Official v6.16.0 is merged into `feature/named-routing-profiles`. Named profiles accept existing string fallback lists and new structured entries with independent reasoning effort and API-key number. The profile editor uses the upstream fallback controls. Configurable Claude auto-compact and Codex reasoning/replay recovery fixes remain enabled in the fork. The pre-merge working tree is retained in a named Git stash; no changes were pushed. See `TASKS.md` for final validation and active runtime.
