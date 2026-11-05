#!/usr/bin/env bash
# Run on the Ubuntu server from the project directory.
set -euo pipefail

die() {
    printf 'Home Study: %s\n' "$*" >&2
    exit 1
}

[[ $(uname -s) == Linux && -r /etc/os-release ]] || die "Run this on the Ubuntu server."
. /etc/os-release
[[ ${ID:-} == ubuntu ]] || die "Expected Ubuntu."
[[ $EUID == 0 ]] || die "Run as root with sudo."
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
for program in curl ip openssl python3 ss systemctl; do
    command -v "$program" >/dev/null || die "Required program missing: $program"
done

node=$(hostname -s)
namespace=home-study
kube() {
    k3s kubectl --kubeconfig /etc/rancher/k3s/k3s.yaml "$@"
}

if command -v k3s >/dev/null; then
    systemctl is-active --quiet k3s || die "K3s exists but its server service is not running."
    kube get node "$node" >/dev/null || die "The existing cluster has no node named $node."
    if [[ -n $(ss -H -ltn 'sport = :8787') ]] &&
        [[ -z $(kube -n "$namespace" get deployment home-study --ignore-not-found -o name) ]]; then
        die "TCP 8787 is already in use on this server."
    fi
    printf 'Using the existing K3s server.\n'
else
    [[ ! -e /etc/rancher/k3s && ! -e /var/lib/rancher/k3s ]] ||
        die "Existing K3s configuration or data needs inspection before installing."
    [[ -z $(ss -H -ltn '( sport = :6443 or sport = :8787 )') ]] ||
        die "TCP 6443 or 8787 is already in use."
    lan=$(python3 - <<'PY'
import ipaddress
import json
import subprocess

def ip(*args):
    return json.loads(subprocess.check_output(["ip", "-j", "-4", *args]))

routes = ip("route", "show", "table", "main")
for route in routes:
    destination = route.get("dst", "default")
    if destination != "default" and any(ipaddress.ip_network(destination, strict=False).overlaps(network)
        for network in map(ipaddress.ip_network, ("10.42.0.0/16", "10.43.0.0/16"))):
        raise SystemExit(f"Existing route {destination} overlaps a default K3s network.")

defaults = sorted((r for r in routes if r.get("dst") == "default"), key=lambda r: r.get("metric", 0))
if not defaults or not defaults[0].get("dev"):
    raise SystemExit("Could not identify the server LAN interface from its IPv4 default route.")
interface = defaults[0]["dev"]
addresses = [a["local"] for item in ip("addr", "show", "dev", interface)
             for a in item["addr_info"] if a["scope"] == "global"]
private_lan = [ipaddress.ip_network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
if len(addresses) != 1 or not any(ipaddress.ip_address(addresses[0]) in network for network in private_lan):
    raise SystemExit(f"Expected one private IPv4 address on {interface}. Inspect the LAN settings.")
print(interface, addresses[0])
PY
    )
    read -r lan_interface lan_ip <<< "$lan"
    installer=$(mktemp)
    trap 'rm -f -- "$installer"' EXIT
    printf 'Installing K3s on %s with LAN IP %s.\n' "$node" "$lan_ip"
    curl -fsSL --max-time 60 https://get.k3s.io -o "$installer"
    INSTALL_K3S_CHANNEL=stable INSTALL_K3S_SYMLINK=skip sh "$installer" server \
        --node-ip "$lan_ip" --tls-san "$lan_ip" --flannel-iface "$lan_interface" \
        --disable=traefik --disable=servicelb
fi

kube wait --for=condition=Ready "node/$node" --timeout=180s
kube rollout status deployment/local-path-provisioner -n kube-system --timeout=180s
kube label "node/$node" homelab-role=always-on
kube create namespace "$namespace" --dry-run=client -o yaml | kube apply -f -
if [[ -z $(kube -n "$namespace" get secret home-study-auth --ignore-not-found -o name) ]]; then
    study_password=$(openssl rand -hex 24)
    printf '%s' "$study_password" |
        kube -n "$namespace" create secret generic home-study-auth --from-file=password=/dev/stdin
    unset study_password
fi
kube apply -k .
if ! kube -n "$namespace" rollout status deployment/home-study --timeout=240s; then
    kube -n "$namespace" get pods,pvc -o wide
    kube -n "$namespace" logs deployment/home-study --tail=30 || true
    die "The application did not become ready."
fi
kube -n "$namespace" wait --for=jsonpath='{.status.phase}'=Bound pvc/home-study-data --timeout=30s
printf 'Home Study health: '
curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8787/healthz
printf '\n'
kube get node "$node" -o wide
kube -n "$namespace" get deployment/home-study pvc/home-study-data
printf 'App username: study. Retrieve the password directly on the server using the README.\n'
if command -v ufw >/dev/null && ufw status | grep '^Status: active' >/dev/null; then
    printf 'UFW is active. Check LAN-only K3s access before joining the GPU worker.\n'
fi
