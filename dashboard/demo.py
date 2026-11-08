"""Example dashboard data and simulated actions for the local learning preview."""
import time

GIB = 1024 ** 3
MIB = 1024 ** 2


class Demo:
    def __init__(self):
        self.stopped = set()
        self.online = False

    def snapshot(self):
        disk = [
            dict(label="System SSD", mount="/", used=24 * GIB, total=442 * GIB, free=397 * GIB),
            dict(label="Media drive", mount="/mnt/media", used=127 * GIB, total=458 * GIB, free=308 * GIB),
        ]
        services = []
        for key, name, description, runtime, cpu, memory, paths, components in (
            ("immich", "Immich", "Photos & backup", "Docker Compose", 5.2, 612,
             [("Photo library", 8.3), ("PostgreSQL", 0.54), ("Machine learning cache", 0.39)],
             ["immich_server", "immich_postgres", "immich_machine_learning", "immich_redis"]),
            ("jellyfin", "Jellyfin", "Movies & television", "Docker Compose", 2.8, 284,
             [("Movies", 83.2), ("Television", 31.2), ("Configuration", 0.42), ("Transcode cache", 0.18)],
             ["jellyfin"]),
            ("navidrome", "Navidrome", "Music streaming", "Docker Compose", 0.6, 92,
             [("Music library", 12.6), ("Database & cache", 0.19)], ["navidrome"]),
            ("project-clio", "Project Clio", "YouTube highlights & clips", "Docker Compose", 0.3, 224,
             [], ["project-clio-clio-1"]),
            ("mneme", "Mneme", "Flashcards & study tools", "K3s", 1.1, 48,
             [("SQLite & saved media", 2.34), ("Temporary imports", 0)], ["home-study / app"]),
        ):
            state = "Stopped" if key in self.stopped else "Running"
            services.append(dict(id=key, name=name, description=description, runtime=runtime,
                                 cpu=cpu if state == "Running" else None, memory=memory * MIB if state == "Running" else None,
                                 state=state, controls=True,
                                 volumes=[{"label": label, "bytes": int(size * GIB)} for label, size in paths],
                                 components=[dict(name=c, health=state.lower(), cpu=None, memory=None)
                                             for c in components]))
        worker = dict(state="Online" if self.online else "Unreachable", model="qwen3.8:27b",
                      power_on=not self.online, power_off=self.online, chat=True,
                      installed_bytes=18 * 1000 ** 3 if self.online else None,
                      loaded=[] if self.online else None, session="active" if self.online else None)
        return dict(mode="demo", timestamp=time.time(), storage_timestamp=time.time(),
                    host=dict(name="homeserver", processor="Example x86-64 CPU", cores=8, cpu=13.7,
                              memory=4.1 * GIB, memory_total=16 * GIB, uptime=1544222, disks=disk),
                    services=services, worker=worker, admin=True,
                    raspberry_pi=dict(name="pi5homeserver", address="192.168.1.166",
                                      dns_ms=3, web_online=True, checked_at=time.time()),
                    processes=[dict(pid=pid, name=name, cpu=cpu, memory=memory * MIB) for pid, name, cpu, memory in
                               [(1460, "k3s-server", 2.4, 420), (932, "dockerd", 1.2, 116),
                                (1468, "containerd", 0.8, 87), (4212, "jellyfin", 0.4, 284),
                                (1613, "tailscaled", 0.2, 39), (4112, "python", 0.1, 48)]])

    def action(self, body):
        key, action = body["id"], body["action"]
        if key == "worker":
            self.online = action == "on"
            return {"message": "Example power state updated."}
        if action == "stop":
            self.stopped.add(key)
        else:
            self.stopped.discard(key)
        return {"message": "Example workload updated."}

    def chat(self, messages):
        self.online = True
        return {"text": 'Example reply. A live response comes from the model on your gaming PC.\n\n'
                        '```python\nfrom pathlib import Path\n\n'
                        'def uptime_seconds():\n    stats = Path("/proc/uptime").read_text()\n'
                        '    return float(stats.split()[0])\n```'}
