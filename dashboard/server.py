"""Serve Mount Olympus on loopback for SSH and private Tailscale access."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import re
import shutil
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from collect import (COMMAND_ERRORS, PROJECTS, Collector, admin, compose_action,
                     docker_rows, docker_stats, processes, project_service, scan_storage)
from demo import Demo
from login import CONFIG, authorization
from wake import WorkerUnavailable
from worker import configured_worker, power, summary

ROOT = Path(__file__).resolve().parent
POLL_SECONDS = 8
ASSETS = {
    "/": ("index.html", "text/html"),
    "/dashboard.css": ("dashboard.css", "text/css"),
    "/dashboard.js": ("dashboard.js", "text/javascript"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
}
ALLOWED_ACTIONS = {**dict.fromkeys([*PROJECTS, "mneme"], {"start", "stop"}), "worker": {"on", "off"}}
APP_METADATA = {"/manifest.webmanifest", "/icon.svg", "/apple-touch-icon.png"}


class Live:
    def __init__(self):
        self.collector = Collector()
        self.latest = None
        self.storage = (None, {})
        self.worker = None
        self.worker_config = None
        try:
            path = Path.home() / ".config/homelab-dashboard/worker.json"
            self.local_worker_config = json.loads(path.read_text())
            if not isinstance(self.local_worker_config, dict):
                raise ValueError("Worker configuration must be a JSON object.")
        except FileNotFoundError:
            self.local_worker_config = {}
        except (OSError, ValueError):
            self.local_worker_config = {}
            logging.warning("Could not read the local dashboard worker configuration")
        self.refresh = threading.Event()
        self.refresh_storage = threading.Event()
        self.service_lock = threading.Lock()
        self.worker_lock = threading.Lock()
        threading.Thread(target=self.collect, name="metrics", daemon=True).start()
        threading.Thread(target=self.collect_storage, name="disk-usage", daemon=True).start()

    def collect_storage(self):
        while True:
            self.refresh_storage.clear()
            try:
                self.storage = (time.time(), scan_storage())
            except Exception:
                logging.exception("Storage collection failed")
            self.refresh_storage.wait(300)

    def collect(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            while True:
                self.refresh.clear()
                started = time.monotonic()
                try:
                    host_task = pool.submit(self.collector.host)
                    docker_task = pool.submit(self.docker)
                    process_task = pool.submit(processes)
                    pi_task = pool.submit(self.collector.raspberry_pi)
                    try:
                        config = admin("status")
                        enabled = True
                    except COMMAND_ERRORS:
                        config, enabled = {}, False
                    if enabled and self.latest and not self.latest["admin"]:
                        self.refresh_storage.set()
                    settings = config.get("worker", {})
                    if not settings.get("OLLAMA_URL"):
                        settings = self.local_worker_config
                    if settings != self.worker_config:
                        try:
                            self.worker = configured_worker(settings)
                        except (TypeError, ValueError):
                            self.worker = None
                            logging.warning("Invalid Mneme worker configuration")
                        self.worker_config = settings
                    worker_task = pool.submit(summary, self.worker)
                    services = docker_task.result() + [self.collector.mneme(config)]
                    self.latest = dict(mode="live", timestamp=time.time(), host=host_task.result(),
                                       services=services, worker=worker_task.result(), admin=enabled,
                                       processes=process_task.result(), raspberry_pi=pi_task.result())
                except Exception:
                    logging.exception("Metrics collection failed")
                self.refresh.wait(max(0, POLL_SECONDS - (time.monotonic() - started)))

    @staticmethod
    def docker():
        try:
            groups = docker_rows()
        except COMMAND_ERRORS:
            groups = {}
        docker_stats(groups)
        return [project_service(key, groups.get(key, [])) for key in PROJECTS]

    def snapshot(self):
        snap = self.latest
        if snap is None:
            return None
        timestamp, sizes = self.storage
        return {**snap, "storage_timestamp": timestamp,
                "services": [{**row, "volumes": sizes.get(row["id"], [])} for row in snap["services"]]}

    def action(self, body):
        key, action = body["id"], body["action"]
        if key == "worker":
            worker = self.worker
            if not worker:
                raise WorkerUnavailable("Enable the dashboard bridge and connect the gaming PC to Mneme.")
            with self.worker_request():
                try:
                    return {"message": power(worker, action)}
                finally:
                    self.refresh.set()
        if not self.service_lock.acquire(blocking=False):
            raise ValueError("A workload change is already in progress.")
        try:
            expected = body.get("expected")
            if action == "start" and expected not in {"Stopped", "Degraded"}:
                raise ValueError("The service needs to be stopped or degraded before starting.")
            if action == "stop" and expected not in {"Running", "Degraded", "Starting"}:
                raise ValueError("The service needs to be active before stopping.")
            if key == "mneme":
                admin(action)
            else:
                compose_action(key, action, expected)
            return {"message": f"{action.capitalize()} requested. Health updates on the next refresh."}
        finally:
            self.refresh.set()
            self.service_lock.release()

    def chat(self, messages):
        worker = self.worker
        if not worker:
            raise WorkerUnavailable("Enable the dashboard bridge and connect the gaming PC to Mneme.")
        with self.worker_request():
            try:
                return {"text": worker.complete([
                    {"role": "system", "content": "You are a local assistant. Explain clearly. "
                     "Put code in fenced code blocks with a language label. "
                     "You have no file, command, or network tools."}, *messages
                ]), "model": worker.model}
            finally:
                self.refresh.set()

    @contextmanager
    def worker_request(self):
        if not self.worker_lock.acquire(blocking=False):
            raise WorkerUnavailable("Wait for the active worker request to finish.")
        try:
            yield
        finally:
            self.worker_lock.release()


def checked_messages(body):
    messages = body.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 9 or len(messages) % 2 == 0:
        raise ValueError("Send a prompt and up to four recent conversation turns.")
    total = 0
    for index, message in enumerate(messages):
        role = "user" if index % 2 == 0 else "assistant"
        if (not isinstance(message, dict) or set(message) != {"role", "content"}
                or message["role"] != role or not isinstance(message["content"], str)
                or not message["content"].strip()):
            raise ValueError("Each conversation turn needs non-empty text.")
        total += len(message["content"])
    if len(messages[-1]["content"]) > 4000 or total > 12000:
        raise ValueError("Keep the prompt under 4,000 characters and the supplied history under 12,000.")
    return messages


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(12)
        super().setup()

    def send(self, status, body, mime, **headers):
        self.send_response(status)
        self.send_header("Content-Type", mime if mime.startswith("image/") else mime + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        page = mime == "text/html" and self.path == "/"
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin" if page else "no-referrer")
        frames = "; frame-src https://www.youtube.com" if page else ""
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'" + frames)
        for name, value in headers.items():
            self.send_header(name.replace("_", "-"), value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def json(self, status, data, **headers):
        self.send(status, json.dumps(data).encode(), "application/json", **headers)

    def expected_origin(self):
        host = self.headers.get("Host")
        if self.headers.get("Tailscale-Funnel-Request"):
            return None
        if (host and host == self.server.https_host
                and self.headers.get("X-Forwarded-Proto") == "https"):
            return f"https://{host}"
        if host in self.server.local_hosts:
            return f"http://{host}"
        return None

    def authorized(self):
        if self.expected_origin() is None:
            self.json(400, {"error": "Use the configured Mount Olympus address."})
            return False
        expected = self.server.authorization
        if expected is None or compare_digest(self.headers.get("Authorization", "").encode("latin-1"), expected):
            return True
        self.json(401, {"error": "Sign in to Mount Olympus."}, WWW_Authenticate='Basic realm="HomeLab"')
        return False

    def clio_request(self, path, body=None):
        if isinstance(self.server.dashboard, Demo):
            self.json(503, {"error": "Clio is available on the live dashboard."})
            return
        request = Request("http://127.0.0.1:8765" + path,
                          data=json.dumps(body).encode() if body is not None else None,
                          headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=5) as response:
                self.json(response.status, json.load(response))
        except HTTPError as error:
            with error:
                try:
                    result = json.load(error)
                except ValueError:
                    result = {"error": "Clio could not complete the request."}
            self.json(error.code, result)
        except (OSError, URLError, ValueError):
            self.json(503, {"error": "Clio is unavailable on the homeserver."})

    def clio_clip(self, job_id, name):
        if isinstance(self.server.dashboard, Demo):
            self.json(503, {"error": "Clio is available on the live dashboard."})
            return
        headers = {}
        if self.headers.get("Range"):
            headers["Range"] = self.headers["Range"]
        request = Request(f"http://127.0.0.1:8765/jobs/{job_id}/{name}", headers=headers)
        try:
            response = urlopen(request, timeout=10)
        except HTTPError as error:
            with error:
                content_range = error.headers.get("Content-Range")
                range_header = {"Content_Range": content_range} if content_range else {}
                self.json(error.code, {"error": "This clip is unavailable."}, **range_header)
            return
        except (OSError, URLError):
            self.json(503, {"error": "Clio is unavailable on the homeserver."})
            return
        with response:
            self.send_response(response.status)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for name in ("Content-Length", "Content-Range", "Accept-Ranges"):
                if name in response.headers:
                    self.send_header(name, response.headers[name])
            self.end_headers()
            try:
                shutil.copyfileobj(response, self.wfile)
            except (OSError, ConnectionResetError):
                pass

    def do_GET(self):
        # Safari can fetch app artwork without forwarding the dashboard login.
        if self.path in APP_METADATA and self.expected_origin() is not None:
            filename, mime = ASSETS[self.path]
            self.send(200, (ROOT / filename).read_bytes(), mime)
            return
        if not self.authorized():
            return
        if self.path in ASSETS:
            filename, mime = ASSETS[self.path]
            self.send(200, (ROOT / filename).read_bytes(), mime)
        elif match := re.fullmatch(r"/api/clio/([0-9a-f]{32})/(clip-0[1-3]\.mp4)", self.path):
            self.clio_clip(*match.groups())
        elif re.fullmatch(r"/api/clio/[0-9a-f]{32}", self.path):
            self.clio_request("/api/jobs/" + self.path.rsplit("/", 1)[1])
        elif self.path == "/api/snapshot":
            data = self.server.dashboard.snapshot()
            self.json(200, data) if data else self.json(503, {"error": "Collecting the first host sample."})
        else:
            self.json(404, {"error": "Page not found."})

    def do_POST(self):
        if not self.authorized():
            return
        if (self.headers.get("Origin") != self.expected_origin()
                or self.headers.get_content_type() != "application/json"):
            self.json(403, {"error": "Use the dashboard page to submit this request."})
            return
        try:
            length = self.headers.get("Content-Length", "")
            if not re.fullmatch(r"[0-9]{1,5}", length) or not 0 < int(length) <= 32_768:
                raise ValueError("The request is too large or empty.")
            body = json.loads(self.rfile.read(int(length)))
            if not isinstance(body, dict):
                raise ValueError("Send a JSON object.")
            if self.path == "/api/clio":
                self.clio_request("/api/jobs", {"topic": body.get("topic"), "mode": body.get("mode", "review")})
                return
            if match := re.fullmatch(r"/api/clio/([0-9a-f]{32})/(clips|review)", self.path):
                job_id, action = match.groups()
                self.clio_request(f"/api/jobs/{job_id}/{action}", body)
                return
            if self.path == "/api/chat":
                result = self.server.dashboard.chat(checked_messages(body))
            elif self.path == "/api/action":
                key, action = body.get("id"), body.get("action")
                expected = body.get("expected", "")
                if (not isinstance(key, str) or not isinstance(action, str) or not isinstance(expected, str)
                        or action not in ALLOWED_ACTIONS.get(key, ())):
                    raise ValueError("Unknown workload or action.")
                result = self.server.dashboard.action(body)
                logging.info("HomeLab action accepted: %s %s", action, key)
            else:
                self.json(404, {"error": "Page not found."})
                return
            self.json(200, result)
        except (ValueError, UnicodeError) as error:
            self.json(400, {"error": str(error)})
        except WorkerUnavailable as error:
            self.json(503, {"error": str(error)})
        except COMMAND_ERRORS:
            logging.exception("HomeLab command failed")
            self.json(503, {"error": "The command could not be confirmed. Refresh and check the current state."})

    def log_message(self, format, *args):
        if args[1] != "200":
            super().log_message(format, *args)


def configured_https_host():
    try:
        host = (CONFIG / "https-host").read_text().strip().lower()
    except FileNotFoundError:
        return None
    if not re.fullmatch(r"[a-z0-9-]+\.[a-z0-9-]+\.ts\.net", host):
        raise ValueError("Set https-host to the dashboard's Tailscale DNS name without a URL or port.")
    return host


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    try:
        expected = None if args.demo else authorization()
        https_host = None if args.demo else configured_https_host()
    except (OSError, ValueError, KeyError, TypeError):
        raise SystemExit("Check the account and HTTPS hostname in ~/.config/homelab-dashboard.")
    logging.basicConfig(level=logging.INFO)
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        port = server.server_address[1]
        server.authorization = expected
        server.local_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        server.https_host = https_host
        server.dashboard = Demo() if args.demo else Live()
        print(f"Mount Olympus {'example' if args.demo else 'live'} at http://127.0.0.1:{port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
