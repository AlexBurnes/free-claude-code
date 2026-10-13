# Install the AlexBurnes FCC fork

This fork adds named routing profiles, launcher selection and per-session routing isolation, a Profile Config editor, a ChatGPT catalog compatibility fix for development builds, and a shorter Admin version label.

The implementation branch is `feature/named-routing-profiles`. Use that branch for these features. Requirements: Git and uv 0.12.13 or newer. The project requires Python 3.14.7; uv can obtain that interpreter automatically. Install the desired agent CLIs separately.

## Clone and install

Cloning provides the documentation and Linux service installer alongside FCC:

```bash
git clone --branch feature/named-routing-profiles https://github.com/AlexBurnes/free-claude-code.git
cd free-claude-code
uv tool install --force --python 3.14.7 .
```

On Linux, configure automatic startup using [the systemd user-service guide](systemd-user-service.md):

```bash
bash scripts/fcc-systemd-user.sh
```

Otherwise, start the server manually:

```bash
fcc-server
```

Open the Admin URL shown in the server log, connect providers on this computer, and create profiles in Profile Config. Provider credentials and account connections are not part of the Git repository.

```bash
fcc-claude --fcc-profile deepseek
```

See [named profiles](named-profiles.md) for model selection, generated launcher wrappers and session lifecycle.

## Install directly from Git

If you only need FCC's commands, uv can install directly from the implementation branch:

```bash
uv tool install --force --python 3.14.7 \
  "git+https://github.com/AlexBurnes/free-claude-code.git@feature/named-routing-profiles"
```

The repository's service installer and documentation are available through the clone method above. For reproducible installations, replace the branch ref with the desired commit SHA.

## Update

For a Git-based installation, reinstall from your chosen ref with refreshed source metadata:

```bash
uv tool install --force --refresh --python 3.14.7 \
  "git+https://github.com/AlexBurnes/free-claude-code.git@feature/named-routing-profiles"
```

Stop a running FCC service before replacing its installation, and start it afterward. Relaunch agents using named-profile session bindings after restarting the server. An installation pinned to a commit remains at that commit until you select a newer SHA.

The upstream quick-start installer and `fcc-update` target the original upstream project. Use this guide's fork-specific commands to retain these changes.

## Compare with upstream

To keep an unmodified upstream installation alongside this fork and switch a single service between them, see [the version comparison guide](version-comparison.md). Use its matching launcher dispatcher when testing API errors.
