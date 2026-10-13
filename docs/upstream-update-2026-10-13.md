# Upstream FCC update — 2026-10-13

The installed comparison baseline was upstream v6.10.5, commit `530eacac36897da543aad02962216212c1daf4c1`. The new clean installation is [upstream v6.16.0](https://github.com/Alishahryar1/free-claude-code/releases/tag/v6.16.0), commit `4e99ddaa5cf453d4ef22fae7c17f7f3aacc3c52b`, published on 2026-10-12. Its source checkout is `../free-claude-code-upstream-6.16.0`; the old checkout remains available for rollback.

## Reported encrypted reasoning error

The user reproduced an OpenAI HTTP400 while the original server was active:

```text
invalid_encrypted_content
Encrypted content item_id did not match the target item id.
Request ID: req_018b18c8feff477497cfbfbbf55f2172
```

This establishes that the failure occurs with the original server too. It does not distinguish a current upstream conversion defect from incompatible reasoning records already stored in an existing conversation.

GitHub issue searches in the upstream repository for `encrypted` and `item_id` found no issue containing those terms on 2026-10-13. Two relevant merged pull requests exist:

- [PR1717: Preserve reasoning history when switching providers](https://github.com/Alishahryar1/free-claude-code/pull/1717) describes invalid encrypted content and invalid reasoning IDs after provider switching.
- [PR2013: Preserve native reasoning and finish metadata before answers](https://github.com/Alishahryar1/free-claude-code/pull/2013) changes native reasoning preservation and source item tracking. Its linked [issue2006](https://github.com/Alishahryar1/free-claude-code/issues/2006) concerns successful but empty answers, a different symptom.

GitHub commit ancestry checks confirm both PRs were already included in v6.10.5, as well as v6.16.0. They are not new fixes obtained by this update. The latest release notes describe fallback API-key selection, not a fix for the reported item-ID mismatch. No issue/comment was posted on the user's behalf.

Source inspection and an isolated synthetic two-record probe confirm v6.10.5 and v6.16.0 rejection recovery still searches for single-quoted item IDs. The same bare-ID probe is corrected by the fork; both upstream versions return no correction, while their quoted-ID case succeeds. No provider requests or working accounts were used. With a bare `rs_...` ID, `param=null` and multiple opaque reasoning records, the rejected record is not identified. Therefore updating alone must not be presented as a verified fix. The fork contains a separate more precise matcher; its live-provider acceptance remains unverified.

Start a fresh short conversation with the same provider/model, then test the existing conversation separately using `--resume`. Preserve transcripts and record UTC time, actual route and request ID. Do not publish ciphertext or credentials. Local readiness checks do not prove provider acceptance.

## Compatibility and validation

The new official frozen lock updates eight shared dependencies, including OpenAI SDK3.22.0→3.25.0 and SQLAlchemy2.1.1→2.1.3. Comparison now includes those dependency changes.

v6.16.0 migrates managed configuration from schema1 to schema2 and converts provider credentials to key lists. The older fork/original cannot read schema2. SQLite migration definitions are unchanged between the two upstream tags. Preserve independent managed configuration for each runtime; never downgrade credential lists blindly. Account files, databases and native conversation history should remain at their existing locations.

A clean isolated v6.16.0 server with a temporary home and no working cloud accounts passed `/health` and `/admin` checks. Live installation and switcher validation are tracked in `TASKS.md` under `fcc-upstream-update-2026-10-13`.

## Installed result

Original v6.16.0 is active with autostart enabled. Actual switching to the fork and back passed health/Admin checks; each previous process exited and only the active service owned port8082. Managed configurations restore exactly, original v6.10.5 remains available with its schema1 configuration, and protected account/profile hashes are unchanged. The switcher passed63 fixture cases, including failed migration recovery. Plain `fcc-claude` now invokes the selected runtime's launcher. See [the Russian guide](version-switching-ru.md) for commands. The reported provider error is not established as fixed by the upstream update.

## User verification after restart

The coordinator restarted the selected original v6.16.0 service on 2026-10-13. New PID3910639, previous PID3896269 exited, health healthy, Admin HTTP200, autostart enabled. The user then reported OpenAI HTTP400 `array_above_max_length` on `input[4].content`: maximum0 elements, received1. Request ID `req_f1169913bbe34d488a000549aff42e17`. Thus the original update did not resolve this observed request compatibility failure. This is distinct from the earlier encrypted item-ID rejection. The exact failing request body was not inspected in this follow-up; its correspondence to the fork's previously tested encrypted-reasoning content normalization is the diagnostic hypothesis. Original runtime remains selected and unmodified.
