#!/usr/bin/python3 -I
"""Root-owned bridge for fixed K3s operations and protected storage measurements."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

NAMESPACE = "home-study"
WORKLOAD = "home-study"
CLAIM = "home-study-data"
STORAGE_ROOT = Path("/var/lib/rancher/k3s/storage")
PROTECTED = {
    "immich-db": Path("/home/sunny/immich-app/postgres"),
    "immich-ml": Path("/var/lib/docker/volumes/immich_model-cache/_data"),
}


def kube(*args):
    return subprocess.check_output(
        ["/usr/local/bin/k3s", "kubectl", "--kubeconfig=/etc/rancher/k3s/k3s.yaml",
         "--request-timeout=5s", "-n", NAMESPACE, *args], text=True, timeout=12,
    )


def get(*args):
    return json.loads(kube("get", *args, "-o", "json"))


def status():
    deployment = get("deployment", WORKLOAD)
    worker = kube("get", "configmap", "home-study-worker", "--ignore-not-found", "-o", "json")
    config = json.loads(worker)["data"] if worker else {}
    return {
        "replicas": deployment["spec"].get("replicas", 1),
        "ready": deployment.get("status", {}).get("readyReplicas", 0),
        "worker": {key: config.get(key, "") for key in
                   ("OLLAMA_URL", "OLLAMA_MODEL", "WOL_MAC", "WOL_BROADCAST")},
    }


def du(path):
    # Resolve each directory relative to an open parent so root never follows symlinks.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open("/", flags)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        result = subprocess.check_output(
            ["/usr/bin/nice", "-n", "15", "/usr/bin/du", "-sxB1", "--", f"/proc/self/fd/{descriptor}/."],
            text=True, timeout=20, stderr=subprocess.DEVNULL, pass_fds=(descriptor,),
        )
        return int(result.split()[0])
    except (subprocess.SubprocessError, ValueError, OSError):
        return None
    finally:
        os.close(descriptor)


def storage():
    sizes = {key: du(path) for key, path in PROTECTED.items()}
    claim = get("pvc", CLAIM)
    volume = get("pv", claim["spec"]["volumeName"])["spec"]
    if (volume["claimRef"]["namespace"], volume["claimRef"]["name"]) != (NAMESPACE, CLAIM):
        raise ValueError("The persistent volume does not match Mneme's claim.")
    raw_path = volume.get("local", volume.get("hostPath", {})).get("path", "")
    path = Path(raw_path)
    if path.parent == STORAGE_ROOT and path.name.startswith("pvc-"):
        sizes["mneme-data"] = du(path)
    temporary = []
    for pod in get("pods", "-l", "app=home-study")["items"]:
        uid = str(uuid.UUID(pod["metadata"]["uid"]))
        path = Path("/var/lib/kubelet/pods") / uid / "volumes/kubernetes.io~empty-dir/imports"
        if path.exists():
            temporary.append(du(path))
    sizes["mneme-temp"] = None if None in temporary else sum(temporary)
    return sizes


def main(action):
    if action == "status":
        return status()
    if action == "storage":
        return storage()
    if action in ("start", "stop"):
        current, desired = (0, 1) if action == "start" else (1, 0)
        kube("scale", "deployment/" + WORKLOAD, f"--current-replicas={current}", f"--replicas={desired}")
        return {"ok": True}
    raise SystemExit(2)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(2)
    try:
        print(json.dumps(main(sys.argv[1])))
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        print(f"HomeLab admin: {error}", file=sys.stderr)
        raise SystemExit(1)
