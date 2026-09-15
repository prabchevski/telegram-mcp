"""Thin MCP/CLI client for the one-per-profile local Telegram service."""
from __future__ import annotations

import asyncio
import contextlib
import errno
import fcntl
import os
import pwd
import stat
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Sequence

from .backend import MediaError, MediaQuality, MediaTooLargeError, RawMedia, RawMessage, RawMessagePage
from .service import (
    ServicePaths, open_private_file, prepare_service_paths, require_same_user,
    service_lock_held, service_paths,
)
from .wire import (
    MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, MAX_TIMEOUT, PROTOCOL_VERSION,
    ServiceBusyError, ServiceError, ServiceProtocolError, ServiceStoppingError,
    ServiceTimeoutError, decode_result, read_frame, validate_request, write_frame,
)

START_TIMEOUT = 15.0
REQUEST_TIMEOUT = MAX_TIMEOUT
_CHILDREN: list[subprocess.Popen[bytes]] = []


class _Unavailable(ServiceError):
    pass


def _validate_socket(paths: ServicePaths) -> None:
    if not paths.directory.exists():
        raise _Unavailable("Telegram service is not running")
    prepare_service_paths(paths)
    try:
        info = paths.socket.lstat()
    except FileNotFoundError as exc:
        raise _Unavailable("Telegram service is not running") from exc
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ServiceError("Unsafe local Telegram service socket")


def _raise_remote(error: Any) -> None:
    if not isinstance(error, dict) or set(error) != {"code", "message"}:
        raise ServiceProtocolError("Invalid service error")
    code, message = error["code"], error["message"]
    if not isinstance(code, str) or not isinstance(message, str) or len(message) > 512:
        raise ServiceProtocolError("Invalid service error")
    classes = {cls.__name__: cls for cls in (
        ServiceBusyError, ServiceError, ServiceProtocolError, ServiceStoppingError,
        ServiceTimeoutError, MediaError, MediaTooLargeError, ValueError, TimeoutError,
    )}
    # These errors are also used by existing CLI/MCP consumers. Importing the
    # module does not load TDLib or create a session in this proxy process.
    from .policy import PolicyError
    from .tdlib_backend import CursorError, SessionBusyError, SetupRequiredError
    classes.update({cls.__name__: cls for cls in (PolicyError, CursorError, SessionBusyError, SetupRequiredError)})
    raise classes.get(code, ServiceError)(message)


async def _request(paths: ServicePaths, operation: str, params: dict[str, Any], *, timeout: float) -> Any:
    request = validate_request({"protocol": PROTOCOL_VERSION, "id": uuid.uuid4().hex,
                                "operation": operation, "params": params, "timeout": timeout})
    _validate_socket(paths)
    writer: asyncio.StreamWriter | None = None
    try:
        async with asyncio.timeout(timeout + 1.0):
            try:
                reader, writer = await asyncio.open_unix_connection(str(paths.socket), limit=MAX_RESPONSE_BYTES + 4)
            except OSError as exc:
                if exc.errno in (errno.ENOENT, errno.ECONNREFUSED):
                    raise _Unavailable("Telegram service is not running") from exc
                raise ServiceError("Unable to connect to the private Telegram service") from exc
            require_same_user(writer.get_extra_info("socket"))
            await write_frame(writer, request, MAX_REQUEST_BYTES)
            response = await read_frame(reader, MAX_RESPONSE_BYTES)
            if (not isinstance(response, dict) or type(response.get("protocol")) is not int
                    or response.get("protocol") != PROTOCOL_VERSION
                    or response.get("id") != request["id"]
                    or set(response) not in ({"protocol", "id", "result"}, {"protocol", "id", "error"})):
                raise ServiceProtocolError("Invalid response or service protocol mismatch; restart the service")
            if "error" in response:
                _raise_remote(response["error"])
            return decode_result(operation, response["result"])
    except TimeoutError as exc:
        raise ServiceTimeoutError("Telegram service request timed out; active native work will finish safely") from exc
    except (asyncio.IncompleteReadError, ConnectionError) as exc:
        raise ServiceError("Telegram service connection ended. For outgoing messages, check the same draft ID; never create a replacement to retry") from exc
    finally:
        if writer is not None:
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()


async def _status(paths: ServicePaths) -> dict[str, Any] | None:
    try:
        return await _request(paths, "status", {}, timeout=2.0)
    except _Unavailable:
        return None


def _spawn(profile: str, paths: ServicePaths) -> subprocess.Popen[bytes]:
    if paths != service_paths(profile):
        raise ServiceError("Custom test service paths require an explicitly injected starter")
    # Avoid inherited workspace variables affecting the interpreter or native
    # library loader. -I also ignores PYTHONPATH and user site packages.
    account = pwd.getpwuid(os.getuid())
    from .launchers import service_python
    environment = {"HOME": account.pw_dir, "USER": account.pw_name, "LOGNAME": account.pw_name,
                   "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                   "LANG": "en_US.UTF-8"}
    log_fd = open_private_file(paths.log)
    try:
        if os.fstat(log_fd).st_size > 64 * 1024:
            os.ftruncate(log_fd, 0)
        os.lseek(log_fd, 0, os.SEEK_END)
        child = subprocess.Popen(
            [service_python(), "-I", "-m", "telegram_search_mcp.service", "--profile", profile],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log_fd,
            cwd=account.pw_dir, env=environment, start_new_session=True, close_fds=True,
        )
    finally:
        os.close(log_fd)
    _CHILDREN[:] = [child for child in _CHILDREN if child.poll() is None]
    _CHILDREN.append(child)
    # MCP processes may outlive an idle service for hours. Reap our own child
    # promptly even when no further client request arrives. Popen serializes
    # wait/poll internally; no global SIGCHLD handler affects other children.
    threading.Thread(target=child.wait, name="telegram-service-reaper", daemon=True).start()
    return child


async def ensure_service(profile: str = "default", *, paths: ServicePaths | None = None) -> dict[str, Any]:
    paths = paths or service_paths(profile)
    existing = await _status(paths)
    if existing is not None:
        if existing.get("stopping"):
            raise ServiceStoppingError("Telegram service is stopping; retry after it exits")
        return existing
    prepare_service_paths(paths)
    startup_fd = open_private_file(paths.startup_lock)
    deadline = time.monotonic() + START_TIMEOUT
    try:
        while True:
            try:
                fcntl.flock(startup_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ServiceTimeoutError("Telegram service startup is busy; retry later")
                await asyncio.sleep(0.05)
        existing = await _status(paths)
        if existing is not None:
            if existing.get("stopping"):
                raise ServiceStoppingError("Telegram service is stopping; retry after it exits")
            return existing
        if service_lock_held(paths):
            raise ServiceBusyError("Telegram profile is authorizing or its service is draining; wait and retry")
        child = _spawn(profile, paths)
        while time.monotonic() < deadline:
            existing = await _status(paths)
            if existing is not None:
                return existing
            if child.poll() is not None:
                raise ServiceError("Telegram service could not start. Run `tgsearch doctor` locally.")
            await asyncio.sleep(0.05)
        raise ServiceTimeoutError("Telegram service startup timed out; run `tgsearch service status`")
    finally:
        os.close(startup_fd)


async def service_status(profile: str = "default", *, connect: bool = False,
                         paths: ServicePaths | None = None) -> dict[str, Any]:
    paths = paths or service_paths(profile)
    if connect:
        status = await ensure_service(profile, paths=paths)
        status.update(await _request(paths, "check_ready", {}, timeout=REQUEST_TIMEOUT))
        return status
    status = await _status(paths)
    return status if status is not None else {"running": False, "profile_busy": service_lock_held(paths)}


async def stop_service(profile: str = "default", *, wait: bool = True,
                       paths: ServicePaths | None = None) -> dict[str, Any]:
    paths = paths or service_paths(profile)
    try:
        status = await _request(paths, "stop", {}, timeout=5.0)
    except _Unavailable:
        return {"running": False, "stopped": not service_lock_held(paths), "profile_busy": service_lock_held(paths)}
    if not wait:
        return status
    deadline = time.monotonic() + MAX_TIMEOUT + 15
    while service_lock_held(paths):
        if time.monotonic() >= deadline:
            raise ServiceTimeoutError("Telegram service is still finishing a native operation. No process was killed; check status later.")
        await asyncio.sleep(0.05)
    for child in _CHILDREN:
        child.poll()
    return {"running": False, "stopped": True}


class SharedTelegramBackend:
    """A disposable client. Closing MCP never closes another client's session."""

    def __init__(self, profile: str = "default", *, paths: ServicePaths | None = None,
                 timeout: float = REQUEST_TIMEOUT, autostart: bool = True) -> None:
        if not 0 < timeout <= MAX_TIMEOUT:
            raise ValueError("Invalid Telegram service timeout")
        self.profile = profile
        self.paths = paths or service_paths(profile)
        self.timeout = timeout
        self.autostart = autostart

    async def _call(self, operation: str, params: dict[str, Any]) -> Any:
        if self.autostart:
            await ensure_service(self.profile, paths=self.paths)
        try:
            return await _request(self.paths, operation, params, timeout=self.timeout)
        except _Unavailable:
            if not self.autostart:
                raise
            # Recovery is safe only when connect failed before dispatch. Never
            # silently re-send a request whose response was lost.
            await ensure_service(self.profile, paths=self.paths)
            return await _request(self.paths, operation, params, timeout=self.timeout)

    async def search_messages(self, *, query: str, cursor: str | None, limit: int) -> RawMessagePage:
        return await self._call("search_messages", {"query": query, "cursor": cursor, "limit": limit})

    async def get_message(self, *, chat_id: int, message_id: int) -> RawMessage | None:
        return await self._call("get_message", {"chat_id": chat_id, "message_id": message_id})

    async def get_context(self, *, chat_id: int, message_id: int, before: int, after: int) -> Sequence[RawMessage]:
        return await self._call("get_context", {"chat_id": chat_id, "message_id": message_id, "before": before, "after": after})

    async def get_media(self, *, chat_id: int, message_id: int, quality: MediaQuality, max_bytes: int) -> RawMedia | None:
        return await self._call("get_media", {"chat_id": chat_id, "message_id": message_id, "quality": quality, "max_bytes": max_bytes})

    async def close(self) -> None:
        # Connections are one-shot and closed in _request, including cancellation.
        # The daemon closes on explicit management stop or ten minutes idle.
        pass

    async def prepare_message(self, *, draft_id: str, recipient: str, text: str, file_path: str | None) -> dict:
        return await self._call("prepare_message", {"draft_id": draft_id, "recipient": recipient, "text": text, "file_path": file_path})

    async def send_message(self, *, draft_id: str) -> dict:
        return await self._call("send_message", {"draft_id": draft_id})

    async def get_send_status(self, *, draft_id: str) -> dict:
        return await self._call("get_send_status", {"draft_id": draft_id})
