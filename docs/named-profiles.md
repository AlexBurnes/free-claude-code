# Named routing profiles

Install this fork using [the fork installation guide](fork-installation.md). For automatic Linux startup, see [the systemd user-service guide](systemd-user-service.md).

For model cards, benchmark comparisons and repeatable agent evaluations, see [Choosing models for FCC agents](model-selection.md).

Named profiles save separate model, tier, reasoning and fallback choices. Provider credentials and OAuth connections stay shared in Providers. Existing Model Config is the implicit default; launching without a profile keeps the normal FCC behavior.

## Save a profile

Open Profile Config in the Admin UI. Choose Create profile, enter a lowercase name such as `deepseek`, then select the base/tier models, reasoning settings and fallback order. New profiles copy saved default routing. Choose Save profile; profile changes are separate from the main Apply button.

Empty tier choices use the profile's base model. Duplicate profile copies the selected saved profile. Discard changes restores saved values, Reload profiles rereads storage, and Delete profile removes a saved entry. Unsaved edits prompt before they are discarded. Edit default in Model Config opens the existing default editor.

Names start with a lowercase letter, contain lowercase ASCII letters, digits, underscores or hyphens, and are at most 64 characters. `default` is reserved. Profiles persist in `~/.fcc/profiles.json`; credentials, endpoints and OAuth tokens are not profile fields.

Claude launches share the optional [auto-compact window setting](claude-context-window.md) in Model Config. It applies to new launches and does not change an already running session.

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

### ChatGPT rejects reasoning history with HTTP 400

If a conversation fails with `Invalid 'input[1].content': array too long` and `array_above_max_length`, the ChatGPT/Codex endpoint has rejected a reasoning history item's nonempty `content`. The array index can differ between requests. This is a request-format error, separate from Claude's context-window or auto-compaction warnings.

FCC can encounter this when replaying Claude thinking with an encrypted signature, including older conversations and restored FCC replay records. The ChatGPT route now omits the reasoning item's plaintext `content` after restoring replay history, preserving its encrypted state, ID and summary. Visible messages, tool calls and tool results retain their order. Other Responses providers keep their existing behavior.

Removing fallback models can expose an error on the selected route, but this error alone does not establish that the fallback configuration caused it. Re-adding fallbacks is not required for this fix.

For an editable installation, restart the server to load the change:

```sh
systemctl --user restart fcc-server.service
```

Retry the failed request in the existing conversation. Relaunch agents using explicit named profiles after the restart because their server session bindings are cleared; use `--resume` to retain the conversation. For a packaged installation, update FCC from the fixed checkout first, following the [fork installation guide](fork-installation.md).

The public OpenAI Responses schema still permits reasoning `content`, so this compatibility rule applies specifically to the ChatGPT/Codex route. See the [OpenAI SDK reasoning input schema](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/response_reasoning_item_param.py) and [reasoning history documentation](https://developers.openai.com/api/docs/guides/reasoning). Verified locally on 2026-10-12: 662 provider, history, recovery and Responses conversion tests passed, including nine new regression cases. Synthetic transport tests verify the request shape; live upstream acceptance requires retrying the conversation against the provider.

### ChatGPT rejects an encrypted reasoning item ID

The error `invalid_encrypted_content` with `Encrypted content item_id did not match the target item id` means the provider cannot verify the encrypted state against the supplied reasoning ID. Older Claude history can contain a raw thinking signature without the original Responses item ID; converting it produces a new ID. Removing plaintext `content` fixes the request schema but cannot restore that lost ID.

For the ChatGPT/Codex route, FCC now handles raw signed Claude thinking before sending a request. Such a block has no original Responses ID, so replaying its ciphertext with a newly generated ID is invalid. Readable thinking is retained as ordinary assistant history in the same position. Raw redacted thinking without readable text or an original ID is omitted from the outgoing copy. The saved Claude transcript is not edited. Other Responses endpoints retain their existing legacy handling, and direct native Responses input keeps its supplied ID/cipher pair.

Fresh Responses reasoning sent to Claude is now stored in an FCC replay envelope containing the original native item and its provider/model/connection scope. A matching next request restores the original ID and ciphertext. When provider/protocol/endpoint/connection scope differs, only readable context is projected; the encrypted state is not reused across incompatible routes. The existing FCC compatibility policy delegates model compatibility within the same connection to the upstream provider. Valid existing FCC envelopes are preserved.

Precise recovery still recognizes explicitly named rejected items, including unquoted IDs, and uses the existing bounded attempt budget (five attempts by default). It does not guess unknown or ambiguous items. The previous item-by-item repair could consume all five attempts on a history with five incompatible blocks before reaching a successful request. The preflight handling avoids those predictable failures without increasing the retry budget.

This error does not by itself mean an API key or OAuth credential is invalid. The reported reason identifies an encrypted reasoning item binding failure. Changing account credentials or adding fallbacks does not recover a lost reasoning ID.

Apply the source update by restarting `fcc-server` as described above, then retry the conversation. Relaunch agents with explicit named profiles using `--resume` after the server restart. Provider acceptance must be checked in the resumed conversation; synthetic tests do not exercise the real encrypted state.

On 2026-10-13, the reported request `req_fdfa35bda265480da6407da37d2e3bb2` used the default ChatGPT route with `gpt-6.1-sol` and received five HTTP 400 responses. Structural inspection found four raw signed readable thinking blocks and one raw redacted block, without FCC replay envelopes. No transcript text, encrypted content or account secrets were copied into diagnostics.

Live acceptance: after deploying commit `e9a9fb1`, the user confirmed on 2026-10-13 that the fix works. This is user-reported provider acceptance in addition to the local regression checks; the coordinator did not issue additional cloud inference requests.

Verified locally on 2026-10-13: 9,261 unit tests passed, nine skipped; Ruff, formatting and type checks passed. Regression cases cover five-block preflight, fresh readable and opaque reasoning roundtrips, credential/endpoint scope, rotating adapter IDs, unchanged request inputs and bounded native recovery.

## Local validation

Run `uv sync --frozen --group dev` and `./scripts/ci.sh` from the checkout to execute the repository checks. The deterministic tests use temporary configuration, mocked providers and an isolated Admin fixture server. They do not establish compatibility with live provider accounts or native Desktop profile routing.

Verified locally on 2026-10-10: 8,538 unit tests passed (eight skipped), all 246 browser tests passed, and suppression/Ruff/ty/JavaScript syntax checks passed. Initial independent contract review completed; final independent subagent review remains pending after a Codex usage limit.
