# Tasks

## 2026-10-13 — Merge upstream v6.16.0 and run fork

- [x] Preserve local edits and merge exact upstream v6.16.0.
- [x] Resolve profile/configuration/reasoning conflicts and run regression checks.
- [ ] Refresh fork environment, switch the service to fork and audit live state.
- [x] Update version-switching documentation with the merged baseline.

Task ID: fcc-merge-616-2026-10-13. Reuse approved Codex backend/tester roles; coordinator owns Git and production runtime.

Pre-deployment: exact v6.16.0 merge995acd9; pre-merge tracked/untracked edits preserved in stash81daed2f8bd7467d2743fec2b16f57581aa895c2. Seven conflicts resolved; profiles accept legacy strings and frozen model/effort/key-number records, separate onboarding and configurable compact retained. Complete final unit suite9249 passed,9 skipped; isolated Chromium browser suite24 passed. Ruff/format/ty, JS/Bash syntax and whitespace checks pass. Earlier failures were stale reasoning-header expectations, missing updated Chromium and a new mocked-HTTP fixture; corrected before the clean final runs. No live inference performed.

## 2026-10-13 — Restart original and review release changes

- [x] Restart selected clean upstream v6.16.0; verify process replacement, health and Admin.
- [x] Review GitHub release notes from v6.10.5 through v6.16.0.
- [x] Record user-reported real OpenAI rejection after restart, preserving the original runtime.

Result: active upstream6.16.0 PID3910639, autostart enabled. User request req_f1169913bbe34d488a000549aff42e17 failed with array_above_max_length on input[4].content; upgrade did not resolve it. No provider code or transcript modified in this follow-up.

## 2026-10-13 — Update original FCC

- [x] Investigate upstream issues/PRs for encrypted reasoning rejection.
- [x] Prepare clean pinned upstream v6.16.0, retaining v6.10.5 for rollback.
- [x] Remove obsolete upstream version hardpin from switcher and verify regression behavior.
- [x] Update live original service, audit protected state and document current versions.

Task ID: fcc-upstream-update-2026-10-13. Reuse approved backend-developer and behavioral-tester roles; coordinator owns production installation.

Pre-deployment validation: 63 switcher fixture cases passed, including per-version settings, schema1/2 round trips, failed migrated-start rollback, preserved shared OAuth refresh and no-start re-registration. Ruff/format/ty pass. Clean upstream6.16.0 temporary-home health/Admin checks pass. Synthetic reasoning probe confirms both upstream6.10.5 and6.16.0 miss bare item-ID recovery with two records; the fork handles it. Related upstream PR1717/2013 already belong to6.10.5.

Live acceptance: clean upstream6.16.0 installed and left selected/active (PID3896269), autostart enabled. Actual upstream6.16 → fork → upstream6.16 round trip passed health/Admin checks, exact previous-PID exit, single service-owned listener and per-runtime schema2/1/2 settings restoration. Fork .env and retained6105 slot match the original baseline exactly; other protected auth/profile hashes unchanged. Private environment slot permissions and zero invalid systemd environment assignments verified. Ordinary fcc-claude now dispatches matching selected launcher; original command backed up privately, native --version works in both runtimes. Real provider inference remains untested.

Recovery note: an unsupported upstream --help probe started initialization and migrated .env before failing the occupied-port check. Exact pre-action backups matched the baseline; a temporary-home migration reproduced the current file byte-for-byte, establishing no additional edits. The migrated state was privately backed up, original schema1 restored with the service stopped, and older runtime health verified before the successful installation. No account/database restoration was performed.

## 2026-10-13 — Switch between upstream and fork FCC

- [x] Install pinned, unmodified upstream v6.10.5 in a separate environment.
- [x] Implement mutually exclusive switching, version status and matching launchers.
- [x] Protect existing configuration/accounts and verify recovery after failed startup.
- [x] Test switching and document a repeatable API comparison workflow.
- [x] Install the switcher outside the workspace and verify real service switching after user approval; leave upstream running.

Validation: 37 behavioral fixture cases passed; Ruff lint/format, ty, Bash syntax and real systemd unit verification passed. Both actual runtime environments answered /health sequentially in isolated temporary homes without cloud accounts. Upstream tag v6.10.5 is clean; fork/upstream uv.lock files and shared dependency versions match. Existing uv-tool FCC uses newer dependencies, retained unchanged. Prepared installer artifact: agent-runtime/version-switch-ready. Before deployment, production settings/auth/profile hashes and the previous unit/PID remained unchanged. User then approved installation and requested upstream remain running. Live fork → upstream → fork → upstream switching passed: health/Admin HTTP200, one listener owned by the selected service, each previous main PID gone. Final upstream PID3831063 active; autostart enabled. Settings/auth/profile hashes remained unchanged; private backups and zero invalid systemd environment assignments verified. Native Claude --version launch passed; real provider inference remains untested.

Task ID: fcc-version-switch-2026-10-13. Reuse the approved FCC backend-developer and behavioral-tester roles, runtime Codex, without model/effort changes. Coordinator owns installation and live service acceptance.

## 2026-10-12 — Encrypted reasoning item ID mismatch

- [x] Reproduce provider rejection of a generated reasoning ID paired with an old encrypted signature.
- [x] Preserve valid replay IDs and recover rejected legacy history without editing the transcript.
- [x] Verify exact error parsing, tool/message preservation and bounded request recovery.
- [x] Document the additional fix and rollout limitations.

Validation: 4096 provider/core/API recovery tests passed, three skipped; focused regression and history suites verified 259 unique cases (included in the broader run). In-memory negative control of the previous quoted-only matcher reproduced the exact bare-ID invalid_encrypted_content HTTP 400. Two legacy records recover in three attempts while preserving both readable texts; a two-attempt budget fails before publishing output. Unknown or ambiguous IDs/cipher mappings are not guessed, other native replay pairs and tool order remain intact. Ruff lint/format, full ty and git diff whitespace checks passed. Live log confirms request req_32a85e93cc634e2fbeba325612b246b8 used default openai/gpt-6.1-sol; transcript structure has two legacy signatures. Live service/settings/transcript were not changed and real provider acceptance remains unverified. Rollout is documented in docs/named-profiles.md.

## 2026-10-12 — Configurable Claude auto-compact window

- [x] Replace hardcoded 190000-token launch limit with optional persisted server setting.
- [x] Add editable control to server web UI, including automatic mode and restart guidance.
- [x] Verify launcher, validation, persistence and UI behavior in isolated tests.
- [x] Document behavior and report how existing sessions pick up the change.

Validation: full unit run 8567 passed, 8 skipped, one stale VSCode mock signature failed; corrected mock and reran its entire module (20 passed). Context-window Chromium test passed. Ruff lint/format, ty, JavaScript syntax and git diff whitespace checks passed. Installed editable launcher confirmed no forced native window by default and explicit 500000 propagation. Running user service/settings left unchanged; restart instructions are in docs/claude-context-window.md.

## 2026-10-12 — ChatGPT reasoning history request compatibility

- [x] Reproduce input[1].content array_above_max_length using a safe synthetic request.
- [x] Correct Anthropic-to-Responses history conversion at the appropriate provider boundary.
- [x] Verify text/tool/encrypted-reasoning preservation and provider-specific behavior.
- [x] Document the fix and rollout instructions.

Validation: 244 provider/history/recovery tests and 418 Responses conversion tests passed (662 total), including nine new regression cases. Bypassing the normalization in memory reproduced HTTP 400 on input[1].content in the raw encrypted-history test; no source changes were made for this negative control. Ruff lint/format, full ty and git diff whitespace checks passed. Request logs and transcript structure identify raw signed thinking as the failing replay shape; tests also cover FCC replay envelopes, auth retry, continuation and unchanged standard Responses behavior. Live service was not restarted and live provider acceptance was not exercised. Rollout instructions are in docs/named-profiles.md.
