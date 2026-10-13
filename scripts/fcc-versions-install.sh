#!/usr/bin/env bash
# Install the one-service runtime switcher; never install or update FCC packages.
set -euo pipefail
umask 077

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
global_args=()
install_args=()
fork_bin=""
while (($#)); do
  case "$1" in
    --state-dir|--config-dir|--systemctl|--systemd-analyze|--health-url|--timeout)
      (($# >= 2)) || { printf 'Missing value for %s\n' "$1" >&2; exit 2; }
      global_args+=("$1" "$2")
      shift 2
      ;;
    --fork-bin)
      (($# >= 2)) || { printf 'Missing value for --fork-bin\n' >&2; exit 2; }
      fork_bin="$2"
      install_args+=("$1" "$2")
      shift 2
      ;;
    *) install_args+=("$1"); shift ;;
  esac
done
if [[ -z "$fork_bin" || ! -x "$fork_bin/python" ]]; then
  printf 'Supply --fork-bin with an installed FCC bin directory containing Python.\n' >&2
  exit 2
fi
exec "$fork_bin/python" "$script_dir/fcc-version.py" "${global_args[@]}" install "${install_args[@]}"
