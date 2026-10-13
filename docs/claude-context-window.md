# Claude Code context and auto-compaction

Open **Model Config → Claude Auto-compact Window** in the FCC web UI.

- Leave the field empty for Claude Code’s automatic, model-dependent compaction window. FCC no longer forces the previous 190,000-token threshold.
- Enter an integer from **100000 to 1000000** to set a compaction window in tokens, then choose **Apply**. For example, `500000` sets a 500K window.
- Clear the field and choose **Apply** to return to automatic mode.

The persisted FCC setting is `FCC_CLAUDE_AUTO_COMPACT_WINDOW`. It applies globally to new Claude launches, including launches using named routing profiles. It is not a provider context-size override and does not enlarge the upstream model’s actual capacity. Choose a value supported by the models and fallback routes you use.

## Apply the change to an existing session

Changing the setting cannot replace the environment of an already running Claude process. Exit Claude and resume the conversation with `fcc-claude --resume <session-id>`. Reconnect FCC-managed editor integrations to update their stored environment, then start a new Claude session there.

For an editable installation, the launcher source change takes effect on the next `fcc-claude` launch without reinstalling. Restart `fcc-server` once to load the new settings schema and refresh the web UI. Restarting the server clears named-profile session bindings; relaunch those agents afterward.

## Environment and command precedence

An explicit FCC value is passed to Claude Code as `CLAUDE_CODE_AUTO_COMPACT_WINDOW`. Claude Code gives this environment variable precedence over `/autocompact`, `--autocompact`, and saved Claude settings.

When the FCC field is empty, FCC does not add that variable. A native `CLAUDE_CODE_AUTO_COMPACT_WINDOW` explicitly supplied in your launching shell is preserved; unset it if you want Claude’s normal automatic behavior.

## Why the two context indicators can differ

The status line can display the model’s nominal full window, such as `202k/1000k`, while Claude’s low-context warning uses a smaller configured compaction window. Under the old FCC launch configuration, 202K exceeded its forced 190K threshold even though the model indicator showed only 20% of 1M.

The displayed Claude model name also does not prove the context capacity of the provider/model selected by FCC routing.

Reference: [Claude Code model configuration and auto-compaction](https://code.claude.com/docs/en/model-config#context-window-and-auto-compaction).

## Local verification (2026-10-12)

The full unit run passed 8,567 tests with eight skipped and found one outdated VS Code test helper signature. After correcting that helper, all 20 tests in its integration module passed. The isolated Chromium test verified save/reload, reset to automatic mode, and invalid-value rejection. Ruff lint/format, ty, JavaScript syntax and whitespace checks passed. Live upstream context limits were not tested.
