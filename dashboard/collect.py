"""Turn host, Docker and Mneme observations into dashboard data."""
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection, HTTPException
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
from urllib.request import urlopen

HOME = Path.home()
PROJECTS = {
    "immich": ("Immich", "Photos & backup", HOME / "immich-app"),
    "jellyfin": ("Jellyfin", "Movies & television", HOME / "jellyfin"),
    "navidrome": ("Navidrome", "Music streaming", HOME / "navidrome"),
}
VOLUMES = {
    "immich": [("Photo library", HOME / "immich-app/library"),
               ("PostgreSQL", "immich-db"), ("Machine learning cache", "immich-ml")],
    "jellyfin": [("Movies", Path("/mnt/media/movies")), ("Television", Path("/mnt/media/tv")),
                 ("Configuration", HOME / "jellyfin/config"), ("Transcode cache", HOME / "jellyfin/cache")],
    "navidrome": [("Music library", Path("/mnt/media/music")), ("Database & cache", HOME / "navidrome/data")],
    "mneme": [("SQLite & saved media", "mneme-data"), ("Temporary imports", "mneme-temp")],
}
ADMIN = "/usr/local/sbin/homelab-dashboard-admin"
PI_ADDRESS = "192.168.1.166"
COMMAND_ERRORS = (OSError, subprocess.SubprocessError, ValueError)


def run(*args, timeout=6):
    return subprocess.check_output(args, text=True, timeout=timeout, stderr=subprocess.DEVNULL)


def admin(action, timeout=22):
    return json.loads(run("sudo", "-n", ADMIN, action, timeout=timeout))


def du(path):
    if not path.is_dir() or (path.is_relative_to("/mnt/media") and not os.path.ismount("/mnt/media")):
        return None
    try:
        return int(run("nice", "-n", "15", "du", "-sxB1", "--", str(path), timeout=20).split()[0])
    except COMMAND_ERRORS:
        return None


def sum_known(values):
    return sum(values) if values and None not in values else None


def scan_storage():
    try:
        protected = admin("storage", timeout=110)
    except COMMAND_ERRORS:
        protected = {}
    paths = {path for volumes in VOLUMES.values() for _, path in volumes if isinstance(path, Path)}
    with ThreadPoolExecutor(max_workers=2) as pool:
        measured = dict(zip(paths, pool.map(du, paths), strict=True))
    measured.update(protected)
    return {app: [{"label": label, "bytes": measured.get(key)} for label, key in volumes]
            for app, volumes in VOLUMES.items()}


def mem_bytes(value):
    match = re.fullmatch(r"\s*([\d.]+)\s*([KMGT]?i?B)\s*", value)
    if not match:
        return None
    number, unit = match.groups()
    scale = "BKMGT".index(unit[0])
    return round(float(number) * (1024 if "i" in unit else 1000) ** scale)


def docker_rows():
    template = '{{.ID}}\t{{.Names}}\t{{.State}}\t{{.Status}}\t{{.Label "com.docker.compose.project"}}'
    result = {}
    for line in run("docker", "ps", "-a", "--format", template).splitlines():
        ident, name, state, status, project = line.split("\t")
        if project in PROJECTS:
            health = ("unhealthy" if "(unhealthy)" in status else
                      "starting" if "(health: starting)" in status else state)
            result.setdefault(project, []).append({
                "id": ident, "name": name, "state": state, "health": health, "cpu": None, "memory": None,
            })
    return result


def docker_stats(groups):
    running = {row["id"]: row for group in groups.values() for row in group if row["state"] == "running"}
    if not running:
        return
    try:
        lines = run("docker", "stats", "--no-stream", "--format", "{{json .}}", *running).splitlines()
        for line in lines:
            stats = json.loads(line)
            row = running.get(stats["ID"])
            if row and stats["MemUsage"] != "0B / 0B":
                row["cpu"] = float(stats["CPUPerc"].removesuffix("%"))
                row["memory"] = mem_bytes(stats["MemUsage"].split("/")[0])
    except (KeyError, *COMMAND_ERRORS):
        pass


def project_state(rows):
    active = [row for row in rows if row["state"] in {"running", "restarting", "paused"}]
    if not rows:
        return "Unknown"
    if not active:
        return "Stopped"
    if len(active) != len(rows) or any(row["health"] != "running" for row in active):
        return "Degraded"
    return "Running"


def project_service(key, rows):
    name, description, directory = PROJECTS[key]
    running = [row for row in rows if row["state"] == "running"]
    return dict(id=key, name=name, description=description, runtime="Docker Compose",
                state=project_state(rows), controls=bool(rows) and (directory / "docker-compose.yml").is_file(),
                cpu=sum_known([row["cpu"] for row in running]),
                memory=sum_known([row["memory"] for row in running]), components=rows)


def disk(mount, label):
    if not os.path.ismount(mount):
        return {"mount": mount, "label": label, "total": None, "used": None, "free": None}
    info = os.statvfs(mount)
    return {"mount": mount, "label": label, "total": info.f_blocks * info.f_frsize,
            "used": (info.f_blocks - info.f_bfree) * info.f_frsize,
            "free": info.f_bavail * info.f_frsize}


class Collector:
    def __init__(self):
        self.previous_cpu = None
        self.previous_mneme = None
        self.pi_sample = None
        self.pi_checked_at = float("-inf")
        self.cores = os.cpu_count()
        cpuinfo = Path("/proc/cpuinfo").read_text()
        match = re.search(r"^model name\s*:\s*(.+)$", cpuinfo, re.M)
        self.processor = match[1] if match else "Linux processor"

    def raspberry_pi(self):
        now = time.monotonic()
        if now - self.pi_checked_at < 60:
            return self.pi_sample

        dns_ms = None
        try:
            result = run("dig", "-r", "@" + PI_ADDRESS, "example.com", "A",
                         "+time=1", "+tries=1", "+noall", "+answer", "+stats", timeout=2)
            address = re.search(r"^example\.com\.\s+\d+\s+IN\s+A\s+(\S+)", result, re.M)
            timing = re.search(r"^;; Query time: (\d+) msec$", result, re.M)
            if address and address[1] != "0.0.0.0" and timing:
                dns_ms = int(timing[1])
        except COMMAND_ERRORS:
            pass

        web_online = False
        connection = HTTPConnection(PI_ADDRESS, timeout=1.5)
        try:
            connection.request("HEAD", "/admin/")
            with connection.getresponse() as response:
                web_online = 200 <= response.status < 400
        except (OSError, HTTPException):
            pass
        finally:
            connection.close()

        self.pi_sample = {"name": "pi5homeserver", "address": PI_ADDRESS,
                          "dns_ms": dns_ms, "web_online": web_online,
                          "checked_at": time.time()}
        self.pi_checked_at = now
        return self.pi_sample

    def host(self):
        with open("/proc/stat") as handle:
            ticks = list(map(int, handle.readline().split()[1:9]))
        sample = (sum(ticks), ticks[3] + ticks[4])
        cpu = None
        if self.previous_cpu:
            elapsed = sample[0] - self.previous_cpu[0]
            if elapsed > 0:
                cpu = max(0, min(100, (1 - (sample[1] - self.previous_cpu[1]) / elapsed) * 100))
        self.previous_cpu = sample
        mem = {key: int(value.split()[0]) * 1024 for key, value in
               (line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())}
        return dict(name=socket.gethostname(), processor=self.processor, cores=self.cores, cpu=cpu,
                    memory=mem["MemTotal"] - mem["MemAvailable"], memory_total=mem["MemTotal"],
                    uptime=int(float(Path("/proc/uptime").read_text().split()[0])),
                    disks=[disk("/", "System SSD"), disk("/mnt/media", "Media drive")])

    def mneme_cgroup(self):
        try:
            pids = run("pgrep", "-u", "10001", "-f", r"^python -B /app/app\.py$").split()
            pid, = pids
            path = Path(f"/proc/{pid}/cgroup").read_text().strip().removeprefix("0::/")
            if not re.fullmatch(r"kubepods\.slice/[\w./-]+/cri-containerd-[a-f0-9]{64}\.scope", path):
                return None, None
            group = Path("/sys/fs/cgroup") / path
            counters = dict(line.split() for line in (group / "cpu.stat").read_text().splitlines())
            memory_stats = dict(line.split() for line in (group / "memory.stat").read_text().splitlines())
            memory = max(0, int((group / "memory.current").read_text()) - int(memory_stats["inactive_file"]))
            sample = (path, time.monotonic(), int(counters["usage_usec"]))
            cpu = None
            if self.previous_mneme and path == self.previous_mneme[0]:
                elapsed = sample[1] - self.previous_mneme[1]
                cpu = max(0, (sample[2] - self.previous_mneme[2]) / elapsed / 10_000)
            self.previous_mneme = sample
            return cpu, memory
        except (KeyError, *COMMAND_ERRORS):
            self.previous_mneme = None
            return None, None

    def mneme(self, config):
        try:
            with urlopen("http://127.0.0.1:8787/healthz", timeout=1) as response:
                healthy = json.load(response).get("ok") is True
        except COMMAND_ERRORS:
            healthy = False
        replicas = config.get("replicas")
        if replicas == 0:
            state = "Stopping" if healthy else "Stopped"
        else:
            state = "Running" if healthy else "Starting" if replicas == 1 else "Unknown"
        cpu, memory = self.mneme_cgroup()
        components = [{"name": "home-study / app", "health": state.lower(), "cpu": cpu, "memory": memory}]
        return dict(id="mneme", name="Mneme", description="Flashcards & study tools", runtime="K3s",
                    state=state, controls=replicas in (0, 1) and state != "Stopping", cpu=cpu, memory=memory,
                    components=components)


def processes():
    try:
        lines = run("ps", "-e", "-o", "pid=,comm=,pcpu=,rss=", "--sort=-pcpu").splitlines()[:12]
        result = []
        for line in lines:
            pid, rest = line.strip().split(None, 1)
            name, cpu, rss = rest.rsplit(None, 2)
            result.append(dict(pid=int(pid), name=name, cpu=float(cpu), memory=int(rss) * 1024))
        return result
    except COMMAND_ERRORS:
        return []


def compose_action(key, action, expected):
    rows = docker_rows().get(key, [])
    if project_state(rows) != expected or not rows:
        raise ValueError("The service changed since the last update. Refresh and try again.")
    _, _, directory = PROJECTS[key]
    run("docker", "compose", "--project-directory", str(directory), "-f",
        str(directory / "docker-compose.yml"), "-p", key, action, timeout=75)
