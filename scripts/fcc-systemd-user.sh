#!/usr/bin/env bash
# Install and enable FCC for the current user's systemd manager.
set -euo pipefail
umask 077

fcc_executable="$HOME/.local/bin/fcc-server"
if [[ ! -x "$fcc_executable" ]]; then
  printf 'FCC is not installed at %s\n' "$fcc_executable" >&2
  exit 1
fi

node_executable="$(command -v node)"
node_bin_dir="$(dirname "$node_executable")"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
unit_path="$unit_dir/fcc-server.service"
mkdir -p "$unit_dir"

cat > "$unit_path" <<EOF
[Unit]
Description=Free Claude Code local proxy

[Service]
Type=exec
WorkingDirectory=%h
ExecStart=%h/.local/bin/fcc-server
Environment="PATH=$node_bin_dir:%h/.local/bin:%h/.cargo/bin:/usr/local/bin:/usr/bin:/bin"
Restart=on-failure
RestartSec=5s
TimeoutStopSec=45s

[Install]
WantedBy=default.target
EOF

systemd-analyze --user verify "$unit_path"
systemctl --user daemon-reload
systemctl --user enable --now fcc-server.service
systemctl --user show fcc-server.service \
  -p ActiveState -p SubState -p UnitFileState -p MainPID
