#!/usr/bin/env bash
# Run on the Mac. SSH and sudo may ask for passwords in the terminal.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
if [[ $# != 1 || $1 == -* ]]; then
    printf 'Usage: bash scripts/deploy_homeserver.sh user@ubuntu-server\n' >&2
    exit 2
fi
target=$1
control_dir=$(mktemp -d /tmp/home-study-ssh.XXXXXX)
socket="$control_dir/ctl"
remote_dir=''
cleanup() {
    if [[ -n $remote_dir ]]; then
        ssh -S "$socket" -o BatchMode=yes -- "$target" "rm -rf -- '$remote_dir'" >/dev/null 2>&1 || true
    fi
    ssh -S "$socket" -O exit -- "$target" >/dev/null 2>&1 || true
    rmdir -- "$control_dir" 2>/dev/null || true
}
trap cleanup EXIT

ssh -M -S "$socket" -o ControlPersist=20m -fN -- "$target"
dir=$(ssh -S "$socket" -o BatchMode=yes -- "$target" 'mktemp -d /tmp/home-study.XXXXXX')
if [[ ! $dir =~ ^/tmp/home-study\.[a-zA-Z0-9]+$ ]]; then
    printf 'Unexpected temporary directory from %s.\n' "$target" >&2
    exit 1
fi
remote_dir=$dir
COPYFILE_DISABLE=1 tar -cf - app.py anki_import.py uploads.py pdf_source.py requirements.txt wake.py index.html study.js study.css \
    kustomization.yaml k8s/app.yaml scripts/bootstrap_homeserver.sh |
    ssh -S "$socket" -o BatchMode=yes -- "$target" "tar -xf - -C '$remote_dir'"
ssh -t -S "$socket" -o BatchMode=yes -- "$target" "cd '$remote_dir' && sudo -- bash scripts/bootstrap_homeserver.sh"

printf '\nOpen a new terminal for the app tunnel.\n'
printf 'ssh -N -L 8787:127.0.0.1:8787 %s\n' "$target"
printf 'Then open http://127.0.0.1:8787 in your browser.\n'
