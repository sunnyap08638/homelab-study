"""Wake an allowlisted Ollama worker only in response to an inference request."""

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

    def ready(self):
        try:
            with HTTP.open(self.url + "/api/tags", timeout=2) as response:
                models = json.loads(response.read(256_000))["models"]
        except HTTPError as error:
            error.close()
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
            if self.ready():
                self.retry_after = 0
                return
        self.retry_after = time.monotonic() + 60
        raise WorkerUnavailable("The worker did not become ready after Wake-on-LAN.")

    def chat(self, messages):
        if not self.lock.acquire(blocking=False):
            raise WorkerUnavailable("Another request is starting the worker or generating cards.")
        try:
            self._wait_until_ready()
            request = Request(
                self.url + "/api/chat",
                data=json.dumps({
                    "model": self.model, "messages": messages, "format": "json",
                    "stream": False, "think": False, "keep_alive": 0,
                    "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 1200},
                }).encode(),
                headers={"Content-Type": "application/json"},
            )
            with HTTP.open(request, timeout=180) as response:
                payload = json.loads(response.read(256_000))
            return json.loads(payload["message"]["content"])
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
