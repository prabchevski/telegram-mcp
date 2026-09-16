"""One private Unix-socket service owns each profile's TDLib session.

Caller timeouts never cancel native work. The queue worker awaits the actual
backend call before starting another operation or closing the database.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import ctypes
import fcntl
import hashlib
import os
import signal
import socket
import stat
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from . import __version__
from .backend import MediaError, TelegramBackend
from .paths import ensure_private_dir, profile_root
from .wire import (
    MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, PROTOCOL_VERSION,
    ServiceBusyError, ServiceError, ServiceProtocolError, ServiceStoppingError,
    ServiceTimeoutError, encode_result, read_frame, validate_request, write_frame,
)

IDLE_TIMEOUT = 600.0
QUEUE_LIMIT = 16
CONNECTION_LIMIT = 40
FRAME_TIMEOUT = 5.0
WRITE_TIMEOUT = 10.0


@dataclass(frozen=True)
class ServicePaths:
    directory: Path

    @property
    def socket(self) -> Path:
        return self.directory / "service.sock"

    @property
    def lock(self) -> Path:
        return self.directory / "service.lock"

    @property
    def startup_lock(self) -> Path:
        return self.directory / "startup.lock"

    @property
    def log(self) -> Path:
        return self.directory / "service.log"


def service_paths(profile: str = "default") -> ServicePaths:
    # macOS Unix sockets have a 104-byte path limit. A fixed short base avoids
    # both that limit and redirection through inherited TMPDIR/workspace .env.
    identity = str(profile_root(profile))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    base = Path("/private/tmp" if sys.platform == "darwin" else "/tmp")
    return ServicePaths(base / f"tgsearch-{os.getuid()}" / digest)


def prepare_service_paths(paths: ServicePaths) -> None:
    ensure_private_dir(paths.directory.parent)
    ensure_private_dir(paths.directory)
    if len(os.fsencode(paths.socket)) > 103:
        raise ServiceError("Local service socket path is too long")


def open_private_file(path: Path) -> int:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ServiceError("Unsafe local service file")
        os.fchmod(fd, 0o600)
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextlib.contextmanager
def profile_exclusive(profile: str = "default", *, paths: ServicePaths | None = None) -> Iterator[None]:
    """Lifetime/auth guard, deliberately separate from the TDLib database lock.

    Hold this around the *entire* auth/config mutation, before changing secrets.
    Lock files are persistent inodes and must never be deleted to unlock them.
    """
    paths = paths or service_paths(profile)
    prepare_service_paths(paths)
    fd = open_private_file(paths.lock)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ServiceBusyError(
                "Telegram profile has an active service or authorization. "
                "Run `tgsearch service stop` and wait for it to finish before authorizing."
            ) from exc
        yield
    finally:
        os.close(fd)


def service_lock_held(paths: ServicePaths) -> bool:
    """Check the actual lock, never infer liveness from a recorded PID."""
    if not paths.directory.exists():
        return False
    prepare_service_paths(paths)
    fd = open_private_file(paths.lock)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False
    finally:
        os.close(fd)


def require_same_user(sock: Any) -> None:
    if hasattr(socket, "SO_PEERCRED"):
        creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _, uid, _ = struct.unpack("3i", creds)
    elif hasattr(sock, "getpeereid"):
        uid, _ = sock.getpeereid()
    elif sys.platform in ("darwin", "freebsd", "openbsd", "netbsd"):
        libc = ctypes.CDLL(None, use_errno=True)
        getpeereid = libc.getpeereid
        getpeereid.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
        getpeereid.restype = ctypes.c_int
        uid_value, gid_value = ctypes.c_uint(), ctypes.c_uint()
        if getpeereid(sock.fileno(), ctypes.byref(uid_value), ctypes.byref(gid_value)):
            raise ServiceError("Unable to verify local service peer")
        uid = uid_value.value
    else:
        raise ServiceError("This platform cannot verify local service peers")
    if uid != os.getuid():
        raise ServiceError("Local service peer belongs to another user")


def _safe_unlink_socket(path: Path, expected_inode: int | None = None) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise ServiceError("Refusing to remove an unsafe local service socket")
    if expected_inode is not None and info.st_ino != expected_inode:
        return
    path.unlink()


def error_result(exc: Exception) -> dict[str, str]:
    known = {
        "ServiceError", "ServiceBusyError", "ServiceTimeoutError", "ServiceStoppingError",
        "ServiceProtocolError", "SetupRequiredError", "SessionBusyError", "CursorError",
        "PolicyError", "MediaError", "MediaTooLargeError", "ValueError", "TimeoutError",
    }
    name = type(exc).__name__
    if name in known:
        return {"code": name, "message": str(exc)[:512]}
    return {"code": "ServiceError", "message": "Telegram operation failed. For an outgoing message, check its existing draft ID before taking any further action."}


@dataclass
class Job:
    request: dict[str, Any]
    deadline: float
    result: asyncio.Future[dict[str, Any]]


class LocalService:
    """Injectable service runner; tests supply temporary paths and a fake backend."""

    def __init__(self, backend: TelegramBackend, *, profile: str = "default", paths: ServicePaths | None = None,
                 idle_timeout: float = IDLE_TIMEOUT, queue_limit: int = QUEUE_LIMIT) -> None:
        if idle_timeout <= 0 or queue_limit < 1:
            raise ValueError("Invalid service limits")
        self.backend = backend
        self.profile = profile
        self.paths = paths or service_paths(profile)
        self.idle_timeout = idle_timeout
        self.queue: asyncio.Queue[Job | None] = asyncio.Queue(maxsize=queue_limit)
        self.stopping = asyncio.Event()
        self.ready = asyncio.Event()
        self.active = False
        self.last_activity = time.monotonic()
        self.connections: set[asyncio.Task[Any]] = set()
        self.writers: set[asyncio.StreamWriter] = set()
        self._socket_inode: int | None = None

    def status(self) -> dict[str, Any]:
        return {"running": True, "stopping": self.stopping.is_set(), "busy": self.active,
                "queued": self.queue.qsize(), "pid": os.getpid(), "version": __version__,
                "protocol": PROTOCOL_VERSION}

    def request_stop(self) -> None:
        self.stopping.set()

    async def run(self) -> None:
        with profile_exclusive(self.profile, paths=self.paths):
            _safe_unlink_socket(self.paths.socket)
            previous_umask = os.umask(0o077)
            try:
                server = await asyncio.start_unix_server(self._handle, path=str(self.paths.socket), limit=MAX_REQUEST_BYTES + 4)
            finally:
                os.umask(previous_umask)
            self.paths.socket.chmod(0o600)
            self._socket_inode = self.paths.socket.stat().st_ino
            worker = asyncio.create_task(self._worker(), name="telegram-service-worker")
            idle = asyncio.create_task(self._idle(), name="telegram-service-idle")
            self.ready.set()
            try:
                await self.stopping.wait()
            finally:
                self.stopping.set()
                server.close()
                await server.wait_closed()
                idle.cancel()
                await asyncio.gather(idle, return_exceptions=True)
                # Reject pending reads. Finish the active native call *without*
                # cancellation before invoking backend.close and releasing locks.
                while not self.queue.empty():
                    pending = self.queue.get_nowait()
                    if pending is not None and not pending.result.done():
                        pending.result.set_result({"error": error_result(ServiceStoppingError("Telegram service is stopping; retry after it exits"))})
                    self.queue.task_done()
                await self.queue.put(None)
                await worker
                try:
                    await self.backend.close()
                finally:
                    # Completed calls get a short chance to deliver their
                    # results before closing their one-shot connections.
                    if self.connections:
                        await asyncio.wait(tuple(self.connections), timeout=1.0)
                    for writer in tuple(self.writers):
                        writer.close()
                    if self.connections:
                        _, pending_tasks = await asyncio.wait(tuple(self.connections), timeout=1.0)
                        for task in pending_tasks:
                            task.cancel()
                        await asyncio.gather(*pending_tasks, return_exceptions=True)
                    _safe_unlink_socket(self.paths.socket, self._socket_inode)

    async def _idle(self) -> None:
        while not self.stopping.is_set():
            await asyncio.sleep(min(1.0, self.idle_timeout / 2))
            if not self.active and self.queue.empty() and time.monotonic() - self.last_activity >= self.idle_timeout:
                self.request_stop()

    async def _worker(self) -> None:
        while True:
            job = await self.queue.get()
            if job is None:
                self.queue.task_done()
                return
            try:
                if job.result.done():
                    continue
                if self.stopping.is_set():
                    job.result.set_result({"error": error_result(ServiceStoppingError("Telegram service is stopping; retry after it exits"))})
                    continue
                if time.monotonic() >= job.deadline:
                    job.result.set_result({"error": error_result(ServiceTimeoutError("Telegram request expired while queued; retry later"))})
                    continue
                self.active = True
                operation = job.request["operation"]
                try:
                    # Validated against a closed allowlist; no generic TDLib RPCs.
                    params = dict(job.request["params"])
                    # Old proxies validate an exact outgoing result shape. Negotiate
                    # new reply/schedule metadata without breaking their pending sends.
                    include_details = params.pop("include_details", False)
                    result = await getattr(self.backend, operation)(**params)
                    response = {"result": encode_result(operation, result, include_details=include_details)}
                except Exception as exc:
                    response = {"error": error_result(exc)}
                    # A completed native failure can leave native asynchronous
                    # work or trailing global TDLib updates. Retire this daemon
                    # instead of opening a second native client in its process.
                    if isinstance(exc, RuntimeError) and not isinstance(exc, (MediaError, ServiceError)):
                        self.request_stop()
                if not job.result.done():
                    if time.monotonic() >= job.deadline:
                        response = {"error": error_result(ServiceTimeoutError("Telegram request deadline exceeded; retry later"))}
                    job.result.set_result(response)
            finally:
                self.active = False
                self.last_activity = time.monotonic()
                self.queue.task_done()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        current = asyncio.current_task()
        assert current is not None
        if len(self.connections) >= CONNECTION_LIMIT:
            writer.close()
            await writer.wait_closed()
            return
        self.connections.add(current)
        self.writers.add(writer)
        job: Job | None = None
        disconnected: asyncio.Task[Any] | None = None
        request_id: str | None = None
        should_stop = False
        try:
            require_same_user(writer.get_extra_info("socket"))
            async with asyncio.timeout(FRAME_TIMEOUT):
                request = validate_request(await read_frame(reader, MAX_REQUEST_BYTES))
            request_id = request["id"]
            operation = request["operation"]
            if operation == "status":
                response = {"result": self.status()}
            elif operation == "stop":
                response = {"result": {**self.status(), "stopping": True}}
                should_stop = True
            elif self.stopping.is_set():
                raise ServiceStoppingError("Telegram service is stopping; retry after it exits")
            else:
                result: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
                job = Job(request, time.monotonic() + request["timeout"], result)
                try:
                    self.queue.put_nowait(job)
                except asyncio.QueueFull as exc:
                    raise ServiceBusyError("Telegram request queue is full; retry later") from exc
                self.last_activity = time.monotonic()
                # One request per connection. EOF/cancellation abandons queued
                # work but never cancels the worker's active native call.
                disconnected = asyncio.create_task(reader.read(1))
                done, _ = await asyncio.wait({result, disconnected}, timeout=request["timeout"], return_when=asyncio.FIRST_COMPLETED)
                if result in done:
                    response = result.result()
                elif disconnected in done:
                    return
                else:
                    raise ServiceTimeoutError("Telegram request deadline exceeded; native work will finish safely")
            await self._respond(writer, request_id, response)
        except (ConnectionError, asyncio.IncompleteReadError, BrokenPipeError):
            pass
        except Exception as exc:
            with contextlib.suppress(ConnectionError, OSError, TimeoutError, ServiceError):
                await self._respond(writer, request_id, {"error": error_result(exc)})
        finally:
            if should_stop:
                self.request_stop()
            if job is not None and not job.result.done():
                job.result.cancel()
            if disconnected is not None:
                disconnected.cancel()
                await asyncio.gather(disconnected, return_exceptions=True)
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()
            self.writers.discard(writer)
            self.connections.discard(current)

    async def _respond(self, writer: asyncio.StreamWriter, request_id: str | None, response: dict[str, Any]) -> None:
        async with asyncio.timeout(WRITE_TIMEOUT):
            await write_frame(writer, {"protocol": PROTOCOL_VERSION, "id": request_id, **response}, MAX_RESPONSE_BYTES)


async def _run(profile: str) -> None:
    from .tdlib_backend import TDLibBackend

    service = LocalService(TDLibBackend(profile), profile=profile)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, service.request_stop)
    await service.run()


def main() -> None:
    parser = argparse.ArgumentParser(description="Private local Telegram read service")
    parser.add_argument("--profile", default="default")
    args = parser.parse_args()
    try:
        asyncio.run(_run(args.profile))
    except ServiceBusyError:
        # Another process owns service.lock; never interfere with its lifetime.
        raise SystemExit(2)
    except Exception:
        # Never log Telegram content, credentials, native requests, or tracebacks.
        print("Telegram local service could not start or close cleanly; run tgsearch doctor.", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
