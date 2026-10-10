# FCC as a systemd user service

On Linux, run FCC automatically using the current user's systemd manager. The service runs `~/.local/bin/fcc-server` from your home directory, uses FCC's existing saved settings and provider accounts, and restarts the server after a crash.

## Install

First install this fork using [the installation guide](fork-installation.md). Install the desired agent CLIs separately. Node.js must be available in the shell used for service installation.

Stop any FCC server running manually, then run the installer from this repository as your regular user, without sudo:

```bash
bash scripts/fcc-systemd-user.sh
```

The installer creates `~/.config/systemd/user/fcc-server.service`, validates the unit, reloads the user manager, and enables/starts the service. It uses the standard uv tool executable location and records the current Node.js directory in PATH for agent commands. No provider credentials are copied into the unit.

It starts at login. To start it at boot and keep it running after logout, enable user lingering according to your machine's administrative policy:

```bash
loginctl enable-linger "$USER"
```

The installer does not change lingering. It may require administrator authorization on your machine.

## Manage

```bash
systemctl --user status fcc-server.service
systemctl --user restart fcc-server.service
systemctl --user stop fcc-server.service
journalctl --user -u fcc-server.service -f
```

Stopping leaves automatic startup enabled. To stop FCC and disable automatic startup:

```bash
systemctl --user disable --now fcc-server.service
```

Do not launch a second manual server while the service owns FCC's configured port. FCC also writes its application log to `~/.fcc/logs/server.log`.

After changing your Node.js installation, rerun the installer and restart the service to refresh PATH. After updating FCC, restart the service and refresh Admin. Named-profile agents must be relaunched because server restarts clear their session bindings. Saved profiles and provider connections persist.
