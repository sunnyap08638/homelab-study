"""Bounded disk staging for the single Mneme instance."""

import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from secrets import token_hex

MAX_BYTES = int(os.environ.get("STUDY_UPLOAD_MAX_BYTES", "10000000000"))
GB = 1_000_000_000
CHUNK_BYTES = 8 * 1024 * 1024
IDLE_SECONDS = 60 * 60
DISK_RESERVE = 2 * 1024 ** 3


class UploadError(ValueError):
    pass


@dataclass
class Upload:
    folder: Path
    size: int
    offset: int = 0
    updated: float = field(default_factory=time.monotonic)
    state: str = "uploading"
    result: dict | None = None
    error: str | None = None
    lock: object = field(default_factory=threading.Lock)

    @property
    def path(self):
        return self.folder / "source"


class Uploads:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.jobs = {}
        self.lock = threading.Lock()
        # A restarted app has lost the previous process's upload sessions.
        for folder in self.root.glob("upload-*"):
            if folder.is_dir() and not folder.is_symlink():
                shutil.rmtree(folder)
        self.closed = threading.Event()
        threading.Thread(target=self._reap, daemon=True).start()

    def _reap(self):
        while not self.closed.wait(60):
            self.expire()

    def expire(self):
        now = time.monotonic()
        with self.lock:
            for key, upload in list(self.jobs.items()):
                if not upload.lock.acquire(blocking=False):
                    continue
                try:
                    if upload.state != "working" and now - upload.updated >= IDLE_SECONDS:
                        try:
                            shutil.rmtree(upload.folder, ignore_errors=not upload.folder.exists())
                        except OSError:
                            continue
                        upload.state = "expired"
                        del self.jobs[key]
                finally:
                    upload.lock.release()

    def start(self, size):
        if type(size) is not int or not 0 < size <= MAX_BYTES:
            raise UploadError(f"Choose a file of up to {MAX_BYTES / GB:g} GB.")
        self.expire()
        with self.lock:
            active = [job for job in self.jobs.values() if job.state in ("uploading", "working")]
            if len(active) >= 2:
                raise UploadError("Finish or cancel another upload first.")
            reserved = sum(max(0, job.size - job.offset)
                           for job in active if job.state == "uploading")
            if shutil.disk_usage(self.root).free < reserved + size + DISK_RESERVE:
                raise UploadError("The server does not have enough free disk space for this upload.")
            key = token_hex(16)
            folder = Path(tempfile.mkdtemp(prefix="upload-", dir=self.root))
            upload = Upload(folder, size)
            upload.path.touch(mode=0o600)
            self.jobs[key] = upload
            return {"id": key, "offset": 0}

    def get(self, key):
        with self.lock:
            upload = self.jobs.get(key)
        if upload is None:
            raise UploadError("This upload expired or was interrupted. Start the upload again.")
        return upload

    def status(self, key):
        upload = self.get(key)
        with upload.lock:
            if upload.state == "uploading":
                return {"state": upload.state, "offset": upload.offset}
            return {"state": upload.state, "result": upload.result, "error": upload.error}

    def append(self, key, offset, size, stream):
        upload = self.get(key)
        with upload.lock:
            if upload.state != "uploading":
                raise UploadError("This upload has already finished.")
            current = upload.offset
            if offset != current:
                return {"offset": current}
            if not 0 < size <= CHUNK_BYTES or current + size > upload.size:
                raise UploadError("The upload chunk is outside the file range.")
            try:
                with upload.path.open("r+b") as destination:
                    destination.seek(current)
                    remaining = size
                    try:
                        while remaining:
                            chunk = stream.read(min(remaining, 128 * 1024))
                            if not chunk:
                                raise UploadError("The connection ended during an upload chunk.")
                            destination.write(chunk)
                            remaining -= len(chunk)
                    except BaseException:
                        destination.truncate(current)
                        raise
            except OSError as error:
                raise UploadError("The upload chunk could not be saved. Retry the upload.") from error
            finally:
                upload.updated = time.monotonic()
            upload.offset = current + size
            return {"offset": upload.offset}

    def claim(self, key):
        upload = self.get(key)
        with upload.lock:
            if upload.state != "uploading" or upload.offset != upload.size:
                raise UploadError("The upload is incomplete or already being processed.")
            upload.state = "working"
        return upload

    def finish(self, upload, result=None, error=None):
        with upload.lock:
            try:
                shutil.rmtree(upload.folder)
            except OSError:
                error = "Temporary files could not be removed. Cleanup will retry automatically."
                upload.updated = time.monotonic() - IDLE_SECONDS
            else:
                upload.updated = time.monotonic()
            upload.result = result
            upload.error = error
            upload.state = "failed" if error else "done"
            return upload.state == "done"

    def cancel(self, key):
        upload = self.get(key)
        with upload.lock:
            if upload.state == "working":
                raise UploadError("This upload is being processed.")
            if upload.state != "uploading":
                return {}
            try:
                shutil.rmtree(upload.folder)
            except OSError as error:
                raise UploadError("The temporary upload could not be removed yet.") from error
            upload.state = "failed"
            upload.error = "Upload canceled."
            upload.updated = time.monotonic()
        return {}

    def close(self):
        self.closed.set()
