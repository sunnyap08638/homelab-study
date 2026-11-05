"""Mirror selected local study folders to the Mneme inbox."""

import argparse
import json
import os
import subprocess
import sys
import time
from base64 import b64encode
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def paths(root, suffix):
    for directory, subdirs, files in os.walk(root):
        subdirs[:] = [name for name in subdirs if not name.startswith(".")]
        for name in files:
            path = Path(directory) / name
            if not name.startswith(".") and path.suffix.lower() == suffix and not path.is_symlink():
                yield path


def stamp(path):
    info = path.stat()
    return info.st_mtime_ns, info.st_size, info.st_ino


def read_note(source, path, pdf_reader):
    if not path.is_file():
        raise ValueError("Expected a regular file.")
    if source == "obsidian":
        if path.stat().st_size > 640_000:
            raise ValueError("Split this large note into smaller files.")
        return path.read_text(encoding="utf-8-sig").strip()
    result = subprocess.run([pdf_reader, str(path)], capture_output=True, timeout=240)
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", errors="replace").strip() or "PDF reader failed.")
    return result.stdout.decode("utf-8").strip()


def sync(roots, args, authorization):
    seen, pending, errors = {}, {}, {}
    opener = build_opener(NoRedirect())
    while True:
        for source, root, suffix in roots:
            for path in paths(root, suffix):
                key = source, path
                try:
                    before = stamp(path)
                    if seen.get(key) == before:
                        continue
                    # Wait one scan for a stable file while cloud clients finish writing it.
                    if not args.once and pending.get(key) != before:
                        pending[key] = before
                        continue
                    body = read_note(source, path, args.pdf_reader)
                    if stamp(path) != before:
                        continue
                    if len(body) > 160_000:
                        raise ValueError("Split this large note into smaller files.")
                    name = path.relative_to(root).as_posix()
                    request = Request(args.url.rstrip("/") + "/api/notes", data=json.dumps(
                        {"source": source, "name": name, "body": body}, ensure_ascii=False,
                    ).encode(), headers={
                        "Authorization": authorization, "Content-Type": "application/json",
                    })
                    with opener.open(request, timeout=15) as response:
                        if json.load(response)["changed"]:
                            print(f"Synced {source} / {name}", flush=True)
                    seen[key] = before
                    pending.pop(key, None)
                    errors.pop(key, None)
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    if isinstance(error, HTTPError):
                        error.close()
                    message = str(error)
                    if errors.get(key) != message:
                        print(f"{source} / {path.name}: {message}", file=sys.stderr, flush=True)
                        errors[key] = message
        if args.once:
            return 1 if errors else 0
        time.sleep(args.interval)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8787")
    parser.add_argument("--obsidian", type=Path)
    parser.add_argument("--goodnotes", type=Path)
    parser.add_argument("--pdf-reader", help="Executable that prints PDF text given a single PDF path.")
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    endpoint = urlsplit(args.url)
    if endpoint.scheme != "https" and not (
        endpoint.scheme == "http" and endpoint.hostname in ("127.0.0.1", "::1", "localhost")
    ):
        parser.error("Use an HTTPS app URL or a loopback SSH tunnel.")
    password = os.environ.get("STUDY_PASSWORD")
    if not password:
        parser.error("Set STUDY_PASSWORD to the app password.")
    if not args.obsidian and not args.goodnotes:
        parser.error("Choose at least one source folder.")
    if args.goodnotes and not args.pdf_reader:
        parser.error("Build the PDF reader and pass its path with --pdf-reader.")
    if args.interval < 1:
        parser.error("Choose an interval of at least one second.")
    roots = []
    for source, root, suffix in (
        ("obsidian", args.obsidian, ".md"), ("goodnotes", args.goodnotes, ".pdf"),
    ):
        if root:
            if not root.is_dir():
                parser.error(f"{root} is not an accessible folder.")
            roots.append((source, root.resolve(), suffix))
    authorization = "Basic " + b64encode(f"study:{password}".encode()).decode()
    return sync(roots, args, authorization)


if __name__ == "__main__":
    raise SystemExit(main())
