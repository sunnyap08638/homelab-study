#!/usr/bin/env bash
# Run on the Ubuntu server after the worker has pulled the model.
set -euo pipefail

if [[ $# -lt 1 || $# -gt 4 || $# -eq 3 ]]; then
    printf 'Usage: sudo bash %s PC_LAN_IP [MODEL [ETHERNET_MAC LAN_BROADCAST]]\n' "$0" >&2
    exit 2
fi
if [[ $EUID -ne 0 ]]; then
    printf 'Run this script with sudo on the Ubuntu K3s server.\n' >&2
    exit 2
fi

pc_ip=$1
model=${2:-qwen3.8:27b}
mac=${3:-}
broadcast=${4:-}
if [[ $model != *:* ]]; then
    model+=:latest
fi
if [[ ! $model =~ ^[[:alnum:]_.-]+(/[[:alnum:]_.-]+)?:[[:alnum:]_.-]+$ || $model == *-cloud || $model == *:cloud ]]; then
    printf 'Choose an installed local Ollama model and tag.\n' >&2
    exit 2
fi
python3 - "$pc_ip" "$mac" "$broadcast" <<'PY'
import ipaddress
import re
import sys

host, mac, broadcast = sys.argv[1:]
networks = [ipaddress.IPv4Network(prefix) for prefix in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
try:
    if not any(ipaddress.IPv4Address(host) in network for network in networks):
        raise ValueError
    if broadcast and not any(ipaddress.IPv4Address(broadcast) in network for network in networks):
        raise ValueError
except ValueError:
    sys.exit("Use the gaming PC's private LAN IPv4 address and the LAN broadcast IPv4 address.")
if bool(mac) != bool(broadcast) or (mac and not re.fullmatch(r"(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}", mac)):
    sys.exit("Wake-on-LAN requires both a six-pair Ethernet MAC and the LAN broadcast address.")
PY

url="http://$pc_ip:11434"
if ! tags=$(curl --noproxy '*' -fsS --max-time 5 "$url/api/tags") ||
    ! python3 -c 'import json, sys; sys.exit(not any(m.get("name") == sys.argv[1] for m in json.load(sys.stdin)["models"]))' "$model" <<< "$tags"; then
    printf 'Ubuntu could not verify %s at %s. Check the Windows listener, firewall, and model.\n' "$model" "$url" >&2
    exit 1
fi

kube() {
    k3s kubectl -n home-study "$@"
}
kube get deployment home-study -o name >/dev/null
kube create configmap home-study-worker \
    --from-literal="OLLAMA_URL=$url" --from-literal="OLLAMA_MODEL=$model" \
    --from-literal="WOL_MAC=$mac" --from-literal="WOL_BROADCAST=$broadcast" \
    --dry-run=client -o yaml | kube apply -f -
kube rollout restart deployment/home-study
kube rollout status deployment/home-study --timeout=240s

python3 - <<'PY'
import base64
import json
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

password = subprocess.check_output([
    "k3s", "kubectl", "-n", "home-study", "get", "secret", "home-study-auth",
    "-o", "jsonpath={.data.password}",
])
auth = base64.b64encode(b"study:" + base64.b64decode(password)).decode()
data = json.dumps({
    "directions": "Create one short flashcard about the boiling point of water.",
    "source": "Pure water boils at 100 degrees Celsius at standard atmospheric pressure.",
}).encode()
request = Request("http://127.0.0.1:8787/api/chat/drafts", data=data, headers={
    "Authorization": "Basic " + auth,
    "Content-Type": "application/json",
})
try:
    with build_opener(ProxyHandler({})).open(request, timeout=200) as response:
        result = json.load(response)
except HTTPError as error:
    with error:
        try:
            detail = json.load(error).get("error", f"HTTP {error.code}")
        except (ValueError, AttributeError):
            detail = f"HTTP {error.code}"
    sys.exit(f"Mneme generation failed: {detail}")
except (URLError, TimeoutError, ValueError) as error:
    sys.exit(f"Mneme generation check failed: {error}")
if not result.get("cards"):
    sys.exit("Mneme reached the worker but received no usable draft cards.")
print(f"Mneme generated {len(result['cards'])} test draft card(s). No cards were saved.")
PY
