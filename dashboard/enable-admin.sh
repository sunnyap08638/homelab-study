#!/usr/bin/env bash
# Run from the dashboard source on the Ubuntu server.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
account=${SUDO_USER:-}
if [[ $EUID != 0 || ! $account =~ ^[a-z_][a-z0-9_-]*$ || $account == root ]]; then
    printf 'Run sudo bash enable-admin.sh from the dashboard operator account.\n' >&2
    exit 1
fi
if [[ ! -x /usr/local/bin/k3s || ! -r /etc/rancher/k3s/k3s.yaml ]]; then
    printf 'The local K3s installation was not found.\n' >&2
    exit 1
fi
policy=$(mktemp)
trap 'rm -f -- "$policy"' EXIT
helper=/usr/local/sbin/homelab-dashboard-admin
printf '%s ALL=(root) NOPASSWD: %s status, %s storage, %s start, %s stop\n' \
    "$account" "$helper" "$helper" "$helper" "$helper" > "$policy"
/usr/sbin/visudo -cf "$policy"
install -o root -g root -m 0755 admin.py "$helper"
install -o root -g root -m 0440 "$policy" /etc/sudoers.d/homelab-dashboard
loginctl enable-linger "$account"
printf 'Enabled the scoped HomeLab bridge and dashboard startup at boot.\n'
