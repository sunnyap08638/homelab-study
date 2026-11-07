"""Read Ollama metrics and delegate user actions to Mneme's worker client."""
import json
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request
import ipaddress

from wake import HTTP, Worker, WorkerUnavailable

LAN = [ipaddress.IPv4Network(block) for block in
       ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]


def configured_worker(settings):
    url = settings.get("OLLAMA_URL", "")
    model = settings.get("OLLAMA_MODEL", "")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.username or parts.path not in ("", "/"):
        return None
    if parts.query or parts.fragment or not model or model.endswith(("-cloud", ":cloud")):
        return None
    if parts.hostname != "ollama-amd.home-study.svc.cluster.local":
        try:
            host = ipaddress.IPv4Address(parts.hostname)
        except ValueError:
            return None
        if not any(host in network for network in LAN):
            return None
    return Worker(url, model, settings.get("WOL_MAC", ""), settings.get("WOL_BROADCAST", ""))


def get(worker, path):
    try:
        with HTTP.open(Request(worker.url + path), timeout=1.2) as reply:
            return json.loads(reply.read(256_000))
    except HTTPError as error:
        error.close()
    except (OSError, ValueError):
        pass
    return None


def summary(worker):
    if not worker:
        return {"state": "Setup required", "model": None, "power_on": False, "power_off": False,
                "chat": False, "installed_bytes": None, "loaded": None, "session": None}
    tags = get(worker, "/api/tags")
    model = worker.model if ":" in worker.model else worker.model + ":latest"
    reachable = isinstance(tags, dict) and isinstance(tags.get("models"), list)
    installed = next((row for row in tags["models"]
                      if isinstance(row, dict) and row.get("name") == model), {}) if reachable else {}
    active = get(worker, "/api/ps") if reachable else None
    session = get(worker, "/mneme/session") if reachable and worker.mac else None
    session_state = session.get("state") if isinstance(session, dict) else None
    shutdown_allowed = (session.get("canShutdown", session_state in {"active", "idle"}) is True
                        if isinstance(session, dict) else False)
    loaded = None
    if isinstance(active, dict) and isinstance(active.get("models"), list):
        loaded = [{"name": row.get("name"), "vram": row.get("size_vram")} for row in active["models"]
                  if isinstance(row, dict)]
    return {"state": ("Online" if installed else "Model missing" if reachable else
                      "Busy" if worker.lock.locked() else "Unreachable"),
            "model": model, "power_on": bool(worker.mac) and not reachable and not worker.lock.locked(),
            "power_off": shutdown_allowed and not worker.lock.locked(),
            "chat": bool(installed) or (bool(worker.mac) and not reachable), "loaded": loaded,
            "installed_bytes": installed.get("size"), "session": session_state}


def power(worker, action):
    if action == "on":
        worker.wake_and_wait()
        try:
            worker.power("start")
        except WorkerUnavailable:
            # A plain Ollama endpoint can wake successfully without a session gateway.
            if not worker.ready():
                raise
            return "Ollama is ready. The PC has an unmanaged power session."
        return "Gaming PC is reachable. Ollama is ready."
    session = get(worker, "/mneme/session")
    if isinstance(session, dict) and "canShutdown" in session:
        if session["canShutdown"] is not True:
            raise WorkerUnavailable("Windows allows shutdown when nobody is signed in and gateway shutdown is enabled.")
        worker.power("shutdown")
    else:
        worker.power("end")
    return "Windows accepted the shutdown request. The next refresh checks reachability."
