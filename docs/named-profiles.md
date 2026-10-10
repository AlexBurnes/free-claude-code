# Named routing profiles

Install this fork using [the fork installation guide](fork-installation.md). For automatic Linux startup, see [the systemd user-service guide](systemd-user-service.md).

Named profiles save separate model, tier, reasoning and fallback choices. Provider credentials and OAuth connections stay shared in Providers. Existing Model Config is the implicit default; launching without a profile keeps the normal FCC behavior.

## Save a profile

Open Profile Config in the Admin UI. Choose Create profile, enter a lowercase name such as `deepseek`, then select the base/tier models, reasoning settings and fallback order. New profiles copy saved default routing. Choose Save profile; profile changes are separate from the main Apply button.

Empty tier choices use the profile's base model. Duplicate profile copies the selected saved profile. Discard changes restores saved values, Reload profiles rereads storage, and Delete profile removes a saved entry. Unsaved edits prompt before they are discarded. Edit default in Model Config opens the existing default editor.

Names start with a lowercase letter, contain lowercase ASCII letters, digits, underscores or hyphens, and are at most 64 characters. `default` is reserved. Profiles persist in `~/.fcc/profiles.json`; credentials, endpoints and OAuth tokens are not profile fields.

## Launch agents

```sh
fcc-claude --fcc-profile deepseek
fcc-claude --fcc-profile nvidia
fcc-codex --fcc-profile openai --profile my-native-codex-profile
```

FCC consumes only `--fcc-profile`; native harness `--profile` remains untouched. Arguments after `--` pass through. An explicit `--fcc-profile default` snapshots the current default; omitting the selector retains the ordinary unbound default behavior.

Each explicit launch creates an independent session binding. Different launches can use different profiles concurrently. Editing or deleting a saved profile affects new launches; existing launches keep their routing settings and model catalog. A named profile rejects direct model choices outside its configured base, tiers and fallbacks.

## Generate convenient commands

```sh
fcc-profiles launchers --output-dir ./fcc-launchers --harness claude deepseek nvidia
./fcc-launchers/fcc-claude-deepseek
./fcc-launchers/fcc-claude-nvidia
```

Omit names to generate wrappers for all saved named profiles. The output directory is explicit, files are not overwritten, and nothing is installed automatically. These are POSIX shell wrappers; the corresponding `fcc-<harness>` command must be available on PATH.

## Lifecycle and diagnostics

Session routes follow FCC's existing proxy authentication policy. The launcher closes its binding after the child process exits. Abandoned sessions expire after 24 hours without requests; server restarts remove all session bindings. Restart affected agents after a server restart. Saved profiles persist.

Routing candidate logs identify profile and provider/model. Request outcome logs identify the final provider/model, including fallback outcomes. Diagnose unexpected provider selection from the completed request route, because a configured fallback can use a different provider.

Terminal launchers support profile session URLs. Native Desktop integrations that currently require a root server URL are not expanded by this feature.

### ChatGPT catalog failure in editable builds

If OpenAI / ChatGPT reports "Could not load models" while the account remains connected, check the server log for a catalog GET returning HTTP 400. Development package versions with VCS metadata can be rejected as `client_version`; catalog requests must send only the numeric release version, such as `6.10.5`. The fix retains the original package version in diagnostic headers.

Editable installations pick up the source fix after restarting `fcc-server`; no reinstall or account reconnect is required. Refresh the Admin page and its model catalog afterward. Restart agents launched with named profiles because restarting the server clears their session bindings.

## Local validation

Run `uv sync --frozen --group dev` and `./scripts/ci.sh` from the checkout to execute the repository checks. The deterministic tests use temporary configuration, mocked providers and an isolated Admin fixture server. They do not establish compatibility with live provider accounts or native Desktop profile routing.

Verified locally on 2026-10-10: 8,538 unit tests passed (eight skipped), all 246 browser tests passed, and suppression/Ruff/ty/JavaScript syntax checks passed. Initial independent contract review completed; final independent subagent review remains pending after a Codex usage limit.
