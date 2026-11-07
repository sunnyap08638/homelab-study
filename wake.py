"""Run inference and manage generation sessions on a private Ollama worker."""

import argparse
import ipaddress
import json
import re
import socket
import threading
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class WorkerUnavailable(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Ollama requests go directly to the configured private backend.
HTTP = build_opener(ProxyHandler({}), NoRedirect())


def magic_packet(mac):
    mac = mac.replace("-", ":")
    if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", mac):
        raise ValueError("Use six colon- or hyphen-separated hex pairs for the Ethernet MAC.")
    return b"\xff" * 6 + bytes.fromhex(mac.replace(":", "")) * 16


def wake(mac, broadcast):
    packet = magic_packet(mac)
    address = ipaddress.IPv4Address(broadcast)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (str(address), 9))


class Worker:
    def __init__(self, url, model, mac="", broadcast="", wake_timeout=90):
        self.url = url.rstrip("/")
        self.model = model
        self.mac = mac
        self.broadcast = broadcast
        self.wake_timeout = wake_timeout
        self.lock = threading.Lock()
        self.retry_after = 0
        if bool(mac) != bool(broadcast):
            raise ValueError("Configure both the MAC and broadcast address to enable waking.")
        if mac:
            magic_packet(mac)
            ipaddress.IPv4Address(broadcast)

    def ready(self, after_wake=False):
        headers = {"X-Mneme-Wake": "1"} if after_wake else {}
        request = Request(self.url + "/api/tags", headers=headers)
        try:
            with HTTP.open(request, timeout=2) as response:
                models = json.loads(response.read(256_000))["models"]
        except HTTPError as error:
            error.close()
            if error.code in (502, 503, 504):
                return False
            raise WorkerUnavailable(f"Ollama health check returned HTTP {error.code}.") from error
        except OSError:
            return False
        except (ValueError, KeyError, TypeError) as error:
            raise WorkerUnavailable("The Ollama host returned an invalid model list.") from error
        expected = self.model if ":" in self.model else self.model + ":latest"
        if not isinstance(models, list):
            raise WorkerUnavailable("The Ollama host returned an invalid model list.")
        if not any(isinstance(m, dict) and m.get("name") == expected for m in models):
            raise WorkerUnavailable(f"Ollama is online. Install {expected} on the worker first.")
        return True

    def _wait_until_ready(self):
        if self.ready():
            self.retry_after = 0
            return
        if not self.mac:
            raise WorkerUnavailable("Ollama is unreachable. Start the worker and check its URL.")
        now = time.monotonic()
        if now < self.retry_after:
            raise WorkerUnavailable("The last wake attempt timed out. Try again in a minute.")
        try:
            wake(self.mac, self.broadcast)
        except OSError as error:
            raise WorkerUnavailable("Could not send Wake-on-LAN from this host.") from error
        deadline = now + self.wake_timeout
        while time.monotonic() < deadline:
            time.sleep(min(2, max(0, deadline - time.monotonic())))
            if self.ready(after_wake=True):
                self.retry_after = 0
                return
        self.retry_after = time.monotonic() + 60
        raise WorkerUnavailable("The worker did not become ready after Wake-on-LAN.")

    def wake_and_wait(self):
        if not self.lock.acquire(blocking=False):
            raise WorkerUnavailable("Wait for the current worker request to finish.")
        try:
            self._wait_until_ready()
        finally:
            self.lock.release()

    def power(self, action=None):
        if not self.mac:
            if action:
                raise WorkerUnavailable("Wake-on-LAN is required for generation sessions.")
            return {"state": "unavailable"}
        if not self.lock.acquire(blocking=False):
            if action:
                raise WorkerUnavailable("Wait for the current generation or power request to finish.")
            return {"state": "busy"}
        try:
            if action == "start":
                self._wait_until_ready()
            url = self.url + "/mneme/session"
            if action:
                url += "/" + action
            request = Request(url, data=b"" if action else None)
            with HTTP.open(request, timeout=4) as response:
                return json.loads(response.read(4096))
        except HTTPError as error:
            error.close()
            if error.code == 404:
                if not action:
                    return {"state": "unavailable"}
                raise WorkerUnavailable("The Windows gateway needs an update for generation sessions.") from error
            if error.code == 409:
                if action == "shutdown":
                    raise WorkerUnavailable("Windows declined shutdown. Sign out of the gaming PC and check that gateway shutdown is enabled.") from error
                raise WorkerUnavailable("Mneme can manage power only when it woke the PC and nobody is signed in.") from error
            raise WorkerUnavailable("The worker could not change its generation session.") from error
        except OSError as error:
            if not action:
                return {"state": "offline"}
            raise WorkerUnavailable("Could not confirm the worker’s power state. Check the session status before retrying.") from error
        except (ValueError, TypeError) as error:
            raise WorkerUnavailable("The worker returned an invalid session status.") from error
        finally:
            self.lock.release()

    def chat(self, messages):
        return self.complete(messages, structured=True)

    def complete(self, messages, *, structured=False):
        if not self.lock.acquire(blocking=False):
            raise WorkerUnavailable("Another request is starting the worker or generating a response.")
        try:
            self._wait_until_ready()
            options = {"temperature": 0, "num_ctx": 4096, "num_predict": 1200}
            body = {"model": self.model, "messages": messages, "stream": False,
                    "think": False, "keep_alive": 0, "options": options}
            if structured:
                body["format"] = "json"
            else:
                options.update(temperature=0.2, num_predict=1800)
            request = Request(
                self.url + "/api/chat",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
            )
            with HTTP.open(request, timeout=180) as response:
                payload = json.loads(response.read(256_000))
            content = payload["message"]["content"]
            if not isinstance(content, str) or (not structured and not content.strip()):
                raise ValueError("Empty or invalid model response")
            return json.loads(content) if structured else content
        except HTTPError as error:
            error.close()
            raise WorkerUnavailable(f"Ollama returned HTTP {error.code} during generation.") from error
        except OSError as error:
            raise WorkerUnavailable("The connection to Ollama failed during generation.") from error
        except (ValueError, KeyError, TypeError) as error:
            raise WorkerUnavailable("Ollama returned an incomplete or invalid response.") from error
        finally:
            self.lock.release()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send one Wake-on-LAN packet from the Ubuntu LAN.")
    parser.add_argument("mac")
    parser.add_argument("broadcast")
    args = parser.parse_args()
    wake(args.mac, args.broadcast)
    print(f"Sent Wake-on-LAN to {args.mac} via {args.broadcast}:9")
