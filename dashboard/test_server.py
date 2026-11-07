"""Exercise access controls without touching any live workload."""
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch

import login
from demo import Demo
from server import Handler


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(self.server.server_close)
        self.port = self.server.server_address[1]
        self.server.authorization = login.credentials("tester", "sample-only-strong-password")
        self.server.dashboard = Demo()
        self.local = f"127.0.0.1:{self.port}"
        self.server.local_hosts = {self.local}
        self.server.https_host = "example.tailnet.ts.net"
        self.thread = Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join)
        self.addCleanup(self.server.shutdown)

    def request(self, path, *, auth=True, host=None, origin=None, forwarded=False, funnel=False, body=None):
        headers = {"Host": host or self.local}
        if auth:
            headers["Authorization"] = self.server.authorization.decode()
        if forwarded:
            headers["X-Forwarded-Proto"] = "https"
        if funnel:
            headers["Tailscale-Funnel-Request"] = "?1"
        if origin:
            headers["Origin"] = origin
        if body is not None:
            headers["Content-Type"] = "application/json"
        conn = HTTPConnection("127.0.0.1", self.port, timeout=2)
        try:
            conn.request("POST" if body is not None else "GET", path, body, headers)
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def test_app_metadata_is_available_before_login_but_snapshot_is_not(self):
        host = self.server.https_host
        status, body = self.request("/manifest.webmanifest", auth=False, host=host, forwarded=True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["display"], "standalone")
        self.assertEqual(self.request("/api/snapshot", auth=False, host=host, forwarded=True)[0], 401)
        self.assertEqual(self.request("/api/snapshot", host=host, forwarded=True, funnel=True)[0], 400)
        self.assertEqual(self.request("/api/snapshot", host=host)[0], 400)
        self.assertEqual(self.request("/api/snapshot", host="wrong.tailnet.ts.net", forwarded=True)[0], 400)

    def test_actions_require_login_and_exact_origin_for_each_route(self):
        host = self.server.https_host
        body = json.dumps(dict(id="jellyfin", action="stop", expected="Running"))
        self.assertEqual(self.request("/api/action", body=body, host=host, forwarded=True,
                                      auth=False, origin=f"https://{host}")[0], 401)
        self.assertEqual(self.request("/api/action", body=body, host=host, forwarded=True,
                                      origin="https://unrelated.example")[0], 403)
        self.assertEqual(self.request("/api/action", body=body, host=host, forwarded=True,
                                      origin=f"http://{host}")[0], 403)
        self.assertEqual(self.request("/api/action", body=body, origin=f"http://{self.local}")[0], 200)
        body = json.dumps(dict(id="jellyfin", action="start", expected="Stopped"))
        self.assertEqual(self.request("/api/action", body=body, host=host, forwarded=True,
                                      origin=f"https://{host}")[0], 200)


class AccountTests(unittest.TestCase):
    def test_rotating_account_replaces_legacy_login_and_keeps_file_private(self):
        with TemporaryDirectory() as path, patch.object(login, "CONFIG", Path(path)):
            previous = "old-test-only-password"
            current = "new-test-only-password"
            legacy = Path(path) / "password"
            legacy.write_text(previous + "\n")
            self.assertEqual(login.authorization(), login.credentials("homelab", previous))
            with patch("builtins.input", return_value="athena"), patch.object(login, "getpass", return_value=current):
                login.main()
            self.assertFalse(legacy.exists())
            self.assertEqual((Path(path) / "account.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(login.authorization(), login.credentials("athena", current))
