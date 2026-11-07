#!/usr/bin/env bash
# Run as the Docker-enabled operator on the Ubuntu server.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
source_dir=$PWD
app="$HOME/.local/share/homelab-dashboard"
config="$HOME/.config/homelab-dashboard"
units="$HOME/.config/systemd/user"
umask 077
mkdir -p -- "$app" "$config" "$units"
if [[ ! -e "$config/account.json" && ! -s "$config/password" ]]; then
    python3 -c 'import secrets; print(secrets.token_urlsafe(24))' > "$config/password"
fi
if [[ $source_dir != "$app" ]]; then
    install -m 0600 server.py login.py collect.py worker.py demo.py index.html dashboard.css dashboard.js \
        manifest.webmanifest icon.svg apple-touch-icon.png admin.py enable-admin.sh "$app"
    install -m 0600 ../wake.py "$app/wake.py"
fi
cat > "$units/homelab-dashboard.service" <<'UNIT'
[Unit]
Description=Mount Olympus homelab control room

[Service]
Type=simple
ExecStart=/usr/bin/python3 -B %h/.local/share/homelab-dashboard/server.py
Restart=on-failure
RestartSec=3s
UMask=0077

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable homelab-dashboard.service
systemctl --user restart homelab-dashboard.service
printf 'Mount Olympus listens on Ubuntu loopback port 8790.\n'
