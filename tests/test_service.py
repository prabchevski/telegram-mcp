from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import socket
import stat
import struct
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from telegram_search_mcp.backend import MediaTooLargeError, RawMedia, RawMessage, RawMessagePage
from telegram_search_mcp.service import (
    LocalService, ServicePaths, profile_exclusive, require_same_user, service_lock_held,
)
from telegram_search_mcp.service_client import SharedTelegramBackend, _request, service_status, stop_service
from telegram_search_mcp.wire import (
    MAX_MEDIA_BYTES, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES,
    ServiceBusyError, ServiceError, ServiceProtocolError, ServiceTimeoutError,
    decode_result, encode_result, read_frame, validate_request, write_frame,
)


@pytest.fixture
def private_paths():
    base = "/private/tmp" if sys.platform == "darwin" else "/tmp"
    with tempfile.TemporaryDirectory(prefix="tgs-test-", dir=base) as directory:
        yield ServicePaths(Path(directory) / "profile")


class ThreadBackend:
    """Models uncancellable native work behind asyncio.to_thread."""
    def __init__(self, *, delay=0.01, blocked=False):
        self.delay = delay
        self.entered = threading.Event()
        self.release = threading.Event()
        if not blocked:
            self.release.set()
        self.calls = []
        self.active = 0
        self.maximum = 0
        self.close_count = 0
        self.guard = threading.Lock()

    def _read(self, chat_id, message_id):
        with self.guard:
            self.calls.append(message_id)
            self.active += 1
            self.maximum = max(self.maximum, self.active)
        self.entered.set()
        try:
            if not self.release.wait(5):
                raise RuntimeError("Test failed to release its fake native operation")
            time.sleep(self.delay)
            return RawMessage(chat_id, "local test", message_id, None,
                              datetime(2026, 1, 1, tzinfo=timezone.utc), "fake message", "messageText")
        finally:
            with self.guard:
                self.active -= 1

    async def get_message(self, *, chat_id, message_id):
        return await asyncio.to_thread(self._read, chat_id, message_id)

    async def search_messages(self, *, query, cursor, limit):
        return RawMessagePage((await self.get_message(chat_id=10, message_id=1),), None)

    async def get_context(self, *, chat_id, message_id, before, after):
        return (await self.get_message(chat_id=chat_id, message_id=message_id),)

    async def get_media(self, *, chat_id, message_id, quality, max_bytes):
        return RawMedia(chat_id, message_id, "messagePhoto", "image", "image/png", b"test-media", width=1, height=1)

    async def check_ready(self):
        return {"ready": True}

    async def close(self):
        assert self.active == 0, "backend was closed before native work finished"
        self.close_count += 1


@contextlib.asynccontextmanager
async def running(paths, backend=None, **kwargs):
    backend = backend or ThreadBackend()
    service = LocalService(backend, paths=paths, **kwargs)
    task = asyncio.create_task(service.run())
    try:
        ready = asyncio.create_task(service.ready.wait())
        done, _ = await asyncio.wait({task, ready}, timeout=3, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            await task
        assert ready in done
        yield service, backend
    finally:
        if hasattr(backend, "release"):
            backend.release.set()
        service.request_stop()
        await asyncio.wait_for(task, 6)


async def eventually(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition was not reached"
        await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_roundtrip_four_reads_and_readiness(private_paths):
    async with running(private_paths) as (service, backend):
        client = SharedTelegramBackend(paths=private_paths, autostart=False)
        assert (await service_status(paths=private_paths))["running"]
        assert backend.calls == []
        message = await client.get_message(chat_id=-12, message_id=123)
        assert message.chat_id == -12 and message.message_id == 123
        assert message.sent_at == datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert (await client.search_messages(query="fake", cursor=None, limit=20)).items
        assert (await client.get_context(chat_id=1, message_id=2, before=3, after=3))[0].message_id == 2
        assert (await client.get_media(chat_id=1, message_id=2, quality="full", max_bytes=MAX_MEDIA_BYTES)).data == b"test-media"
        assert (await service_status(paths=private_paths, connect=True))["ready"]
        await client.close()
        assert service_lock_held(private_paths) and backend.close_count == 0
        assert stat.S_IMODE(private_paths.directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(private_paths.socket.stat().st_mode) == 0o600
    assert backend.close_count == 1
    assert not private_paths.socket.exists()
    assert private_paths.lock.exists(), "persistent lock inode must not be deleted"


@pytest.mark.asyncio
async def test_two_clients_dispatch_one_prepared_send_and_status(private_paths, tmp_path):
    import uuid
    from test_outgoing import FakeSession, sends
    from telegram_search_mcp.outgoing import Outbox
    session = FakeSession()
    box = Outbox(tmp_path / "outbox", session.user_id)
    box.attach(session)
    class Backend(ThreadBackend):
        async def prepare_message(self, **params):
            return await asyncio.to_thread(box.prepare, session, **params)
        async def send_message(self, **params):
            return await asyncio.to_thread(box.send, session, **params)
        async def get_send_status(self, **params):
            return await asyncio.to_thread(box.status, session, **params)
    async with running(private_paths, Backend()):
        a, b = [SharedTelegramBackend(paths=private_paths, autostart=False) for _ in range(2)]
        draft_id = uuid.uuid4().hex
        prepared = await a.prepare_message(draft_id=draft_id, recipient="@example", text="🌍" * 2000, file_path=None)
        assert prepared["status"] == "prepared" and not sends(session)
        results = await asyncio.gather(a.send_message(draft_id=draft_id), b.send_message(draft_id=draft_id))
        assert all(r["status"] == "sent" for r in results)
        assert (await b.get_send_status(draft_id=draft_id))["message_id"] == 456
        assert len(sends(session)) == 1


@pytest.mark.asyncio
async def test_many_clients_share_one_serial_native_worker(private_paths):
    async with running(private_paths, queue_limit=32) as (_, backend):
        clients = [SharedTelegramBackend(paths=private_paths, autostart=False) for _ in range(20)]
        results = await asyncio.gather(*(client.get_message(chat_id=1, message_id=i + 1) for i, client in enumerate(clients)))
        assert sorted(item.message_id for item in results) == list(range(1, 21))
        assert backend.maximum == 1
        assert len(backend.calls) == 20


@pytest.mark.asyncio
async def test_backpressure_does_not_start_extra_native_work(private_paths):
    backend = ThreadBackend(blocked=True)
    async with running(private_paths, backend, queue_limit=1) as (service, _):
        client = SharedTelegramBackend(paths=private_paths, autostart=False)
        first = asyncio.create_task(client.get_message(chat_id=1, message_id=1))
        await eventually(backend.entered.is_set)
        second = asyncio.create_task(client.get_message(chat_id=1, message_id=2))
        await eventually(lambda: service.queue.qsize() == 1)
        with pytest.raises(ServiceBusyError, match="queue is full"):
            await client.get_message(chat_id=1, message_id=3)
        assert backend.calls == [1]
        backend.release.set()
        await asyncio.gather(first, second)
        assert backend.calls == [1, 2] and backend.maximum == 1


@pytest.mark.asyncio
async def test_deadline_keeps_native_call_alive_and_drops_expired_queue(private_paths):
    backend = ThreadBackend(blocked=True)
    async with running(private_paths, backend) as (_, _):
        short = SharedTelegramBackend(paths=private_paths, timeout=0.04, autostart=False)
        with pytest.raises(ServiceTimeoutError):
            await short.get_message(chat_id=1, message_id=1)
        assert backend.active == 1 and backend.close_count == 0
        with pytest.raises(ServiceTimeoutError):
            await short.get_message(chat_id=1, message_id=2)
        assert backend.calls == [1]
        backend.release.set()
        long = SharedTelegramBackend(paths=private_paths, autostart=False)
        assert (await long.get_message(chat_id=1, message_id=3)).message_id == 3
        assert backend.calls == [1, 3] and backend.maximum == 1


@pytest.mark.asyncio
async def test_client_cancellation_does_not_cancel_or_close_native_work(private_paths):
    backend = ThreadBackend(blocked=True)
    async with running(private_paths, backend) as (service, _):
        client = SharedTelegramBackend(paths=private_paths, autostart=False)
        first = asyncio.create_task(client.get_message(chat_id=1, message_id=1))
        await eventually(backend.entered.is_set)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        await client.close()
        assert backend.active == 1 and backend.close_count == 0
        second = asyncio.create_task(client.get_message(chat_id=1, message_id=2))
        await eventually(lambda: service.queue.qsize() == 1)
        assert not second.done() and backend.calls == [1]
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        third = asyncio.create_task(client.get_message(chat_id=1, message_id=3))
        backend.release.set()
        await third
        assert backend.calls == [1, 3] and backend.maximum == 1


@pytest.mark.asyncio
async def test_stop_drains_native_operation_before_database_unlock(private_paths):
    backend = ThreadBackend(blocked=True)
    async with running(private_paths, backend) as (_, _):
        client = SharedTelegramBackend(paths=private_paths, autostart=False)
        first = asyncio.create_task(client.get_message(chat_id=1, message_id=1))
        await eventually(backend.entered.is_set)
        stop = asyncio.create_task(stop_service(paths=private_paths))
        await eventually(lambda: not private_paths.socket.exists() or stop.done() or backend.active == 1)
        await asyncio.sleep(0.05)
        assert not stop.done()
        assert backend.close_count == 0 and service_lock_held(private_paths)
        backend.release.set()
        result = await stop
        await asyncio.gather(first, return_exceptions=True)
        assert result == {"running": False, "stopped": True}
        assert not service_lock_held(private_paths) and backend.close_count == 1


@pytest.mark.asyncio
async def test_idle_stop_closes_backend_and_releases_lifetime_lock(private_paths):
    async with running(private_paths, idle_timeout=0.05) as (service, backend):
        await eventually(lambda: not service_lock_held(private_paths))
        assert backend.close_count == 1 and service.stopping.is_set()


@pytest.mark.asyncio
async def test_auth_guard_and_daemon_are_mutually_exclusive(private_paths):
    with profile_exclusive(paths=private_paths):
        service = LocalService(ThreadBackend(), paths=private_paths)
        with pytest.raises(ServiceBusyError):
            await service.run()
    async with running(private_paths):
        with pytest.raises(ServiceBusyError):
            with profile_exclusive(paths=private_paths):
                pytest.fail("authorization must not race a live service")


@pytest.mark.asyncio
async def test_regular_file_and_symlink_are_never_removed_as_stale_sockets(private_paths):
    private_paths.directory.mkdir()
    private_paths.socket.write_text("do not remove")
    with pytest.raises(ServiceError, match="unsafe"):
        await LocalService(ThreadBackend(), paths=private_paths).run()
    assert private_paths.socket.read_text() == "do not remove"
    private_paths.socket.unlink()
    target = private_paths.directory / "target"
    target.write_text("still here")
    private_paths.socket.symlink_to(target)
    with pytest.raises(ServiceError, match="unsafe"):
        await LocalService(ThreadBackend(), paths=private_paths).run()
    assert target.read_text() == "still here" and private_paths.socket.is_symlink()


@pytest.mark.asyncio
async def test_lock_symlink_is_rejected_without_modifying_target(private_paths):
    private_paths.directory.mkdir()
    target = private_paths.directory / "target"
    target.write_text("still here")
    private_paths.lock.symlink_to(target)
    with pytest.raises(OSError):
        with profile_exclusive(paths=private_paths):
            pytest.fail("symlinked lock accepted")
    assert target.read_text() == "still here"


@pytest.mark.asyncio
async def test_no_unbounded_write_or_generic_rpc_is_available(private_paths):
    async with running(private_paths) as (_, backend):
        for operation in ("sendMessage", "sendMessageAlbum", "close", "__dict__", "request", "logout"):
            with pytest.raises(ServiceProtocolError, match="not permitted"):
                await _request(private_paths, operation, {}, timeout=2)
        assert backend.calls == []


@pytest.mark.asyncio
async def test_oversized_incoming_frame_is_rejected_before_payload(private_paths):
    async with running(private_paths) as (_, backend):
        reader, writer = await asyncio.open_unix_connection(str(private_paths.socket))
        writer.write(struct.pack("!I", MAX_REQUEST_BYTES + 1))
        await writer.drain()
        response = await read_frame(reader, MAX_RESPONSE_BYTES)
        assert response["error"]["code"] == "ServiceProtocolError"
        writer.close()
        await writer.wait_closed()
        assert backend.calls == []


def test_peer_identity_rejects_other_users(monkeypatch):
    expected_uid = os.getuid() + 1
    first, second = socket.socketpair()
    try:
        # Use real OS peer credentials while changing only the expected user.
        # This exercises getpeereid on macOS and SO_PEERCRED on Linux alike.
        monkeypatch.setattr(os, "getuid", lambda: expected_uid)
        with pytest.raises(ServiceError, match="another user"):
            require_same_user(first)
    finally:
        first.close()
        second.close()


@pytest.mark.asyncio
async def test_completed_native_error_retires_daemon_before_next_read(private_paths):
    class FailingBackend(ThreadBackend):
        async def get_message(self, **kwargs):
            result = await super().get_message(**kwargs)
            if kwargs["message_id"] == 1:
                raise RuntimeError("fake broken native session")
            return result
    backend = FailingBackend()
    async with running(private_paths, backend) as (service, _):
        client = SharedTelegramBackend(paths=private_paths, autostart=False)
        with pytest.raises(ServiceError):
            await client.get_message(chat_id=1, message_id=1)
        await eventually(lambda: not service_lock_held(private_paths))
        assert service.stopping.is_set()
        assert backend.close_count == 1
        with pytest.raises(ServiceError):
            await client.get_message(chat_id=1, message_id=2)
        assert backend.calls == [1]
        assert backend.maximum == 1


@pytest.mark.asyncio
async def test_media_error_type_and_limit_are_preserved(private_paths):
    class LargeBackend(ThreadBackend):
        async def get_media(self, **kwargs):
            raise MediaTooLargeError("fake media is too large")
    async with running(private_paths, LargeBackend()) as (_, backend):
        client = SharedTelegramBackend(paths=private_paths, autostart=False)
        with pytest.raises(MediaTooLargeError, match="too large"):
            await client.get_media(chat_id=1, message_id=1, quality="full", max_bytes=MAX_MEDIA_BYTES)
        assert backend.close_count == 0
        with pytest.raises(ServiceProtocolError):
            await client.get_media(chat_id=1, message_id=1, quality="full", max_bytes=MAX_MEDIA_BYTES + 1)


def test_maximum_media_roundtrip_and_frame_fit():
    media = RawMedia(1, 1, "messageDocument", "file", "application/pdf", b"x" * MAX_MEDIA_BYTES)
    encoded = encode_result("get_media", media)
    assert len(json.dumps(encoded).encode()) < MAX_RESPONSE_BYTES
    assert decode_result("get_media", encoded) == media


@pytest.mark.parametrize("change", [
    {"operation": "sendMessage"}, {"protocol": True}, {"timeout": float("nan")},
    {"timeout": 121}, {"params": {"chat_id": 1, "message_id": True}},
    {"params": {"chat_id": 1, "message_id": 0}}, {"params": {"chat_id": 1, "message_id": 1, "extra": 2}},
])
def test_request_validation_rejects_invalid_or_ambiguous_input(change):
    request = {"protocol": 1, "id": "a" * 32, "operation": "get_message", "params": {"chat_id": 1, "message_id": 1}, "timeout": 2}
    with pytest.raises(ServiceProtocolError):
        validate_request({**request, **change})


async def process_client(paths):
    process = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).with_name("test_service_support.py")),
        "client", str(paths.directory), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await asyncio.wait_for(process.communicate(), 20)
    assert process.returncode == 0, stderr.decode()
    return json.loads(stdout)


@pytest.mark.asyncio
async def test_multiple_process_autostart_is_singleton_and_crash_recovers(private_paths):
    # Every process has its own startup code and flock handle. All use an
    # injected fake daemon, with no policy/Keychain/database/profile access.
    try:
        results = await asyncio.gather(*(process_client(private_paths) for _ in range(6)))
        pids = {item["pid"] for item in results}
        assert len(pids) == 1
        assert all(item["maximum"] == 1 for item in results)
        counter = private_paths.directory.parent / "spawn-count"
        assert counter.read_text().splitlines() == ["spawn"]
        fake_pid = pids.pop()
        assert (await service_status(paths=private_paths))["pid"] == fake_pid
        # This PID was returned by our isolated fake fixture only. Simulate a
        # crash to prove stale socket recovery without deleting lock inodes.
        lock_inode = private_paths.lock.stat().st_ino
        os.kill(fake_pid, signal.SIGKILL)
        await eventually(lambda: not service_lock_held(private_paths))
        assert private_paths.socket.exists()
        restarted = await process_client(private_paths)
        assert restarted["pid"] != fake_pid and restarted["maximum"] == 1
        assert counter.read_text().splitlines() == ["spawn", "spawn"]
        assert private_paths.lock.stat().st_ino == lock_inode
        await stop_service(paths=private_paths)
        assert not service_lock_held(private_paths)
    finally:
        await stop_service(paths=private_paths)


@pytest.mark.asyncio
async def test_sigterm_drains_real_process_fake_native_thread(private_paths):
    process = await asyncio.create_subprocess_exec(sys.executable,
        str(Path(__file__).with_name("test_service_support.py")), "blocking-daemon", str(private_paths.directory),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    request = None
    try:
        await eventually(private_paths.socket.exists)
        backend = SharedTelegramBackend(paths=private_paths, autostart=False)
        request = asyncio.create_task(backend.get_message(chat_id=1, message_id=1))
        await eventually((private_paths.directory.parent / "started").exists)
        process.send_signal(signal.SIGTERM)
        await asyncio.sleep(0.05)
        assert process.returncode is None and service_lock_held(private_paths)
        assert not (private_paths.directory.parent / "closed").exists()
        (private_paths.directory.parent / "release").touch()
        stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
        assert process.returncode == 0, stderr.decode()
        assert (private_paths.directory.parent / "completed").exists()
        assert (private_paths.directory.parent / "closed").exists()
        assert not service_lock_held(private_paths)
        assert (await request).message_id == 1
    finally:
        (private_paths.directory.parent / "release").touch()
        if process.returncode is None:
            process.send_signal(signal.SIGTERM)
            await asyncio.wait_for(process.wait(), 5)
        if request is not None:
            await asyncio.gather(request, return_exceptions=True)


def test_spawn_reaps_idle_child_without_another_request(private_paths, monkeypatch):
    import subprocess
    from telegram_search_mcp import service_client
    from telegram_search_mcp.service import prepare_service_paths

    prepare_service_paths(private_paths)
    actual_popen = subprocess.Popen
    captured = {}
    def fake_daemon(args, **kwargs):
        captured.update({"args": args, **kwargs})
        # Exercise the real reaper with an isolated short-lived process, while
        # ensuring the production daemon never opens any real user profile.
        return actual_popen([sys.executable, "-c", "import time; time.sleep(0.02)"], **kwargs)
    monkeypatch.setattr(service_client.subprocess, "Popen", fake_daemon)
    monkeypatch.setattr(service_client, "service_paths", lambda _: private_paths)
    monkeypatch.setenv("PYTHONPATH", "/untrusted-workspace")
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", "/untrusted-native-library")
    child = service_client._spawn("default", private_paths)
    deadline = time.monotonic() + 2
    while child.returncode is None:
        assert time.monotonic() < deadline
        time.sleep(0.005)
    assert child.returncode == 0
    with pytest.raises(ChildProcessError):
        os.waitpid(child.pid, os.WNOHANG)
    assert captured["args"][1] == "-I"
    assert "PYTHONPATH" not in captured["env"] and "DYLD_INSERT_LIBRARIES" not in captured["env"]
    assert captured["start_new_session"] and captured["close_fds"]
    assert stat.S_IMODE(private_paths.log.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_two_actual_mcp_stdio_processes_share_service_and_can_exit(private_paths):
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters, stdio_client

    helper = str(Path(__file__).with_name("test_service_support.py"))
    try:
        async with contextlib.AsyncExitStack() as stack:
            clients = [await stack.enter_async_context(Client(stdio_client(StdioServerParameters(
                command=sys.executable, args=[helper, "mcp", str(private_paths.directory)])))) for _ in range(2)]
            for client in clients:
                assert {tool.name for tool in (await client.list_tools()).tools} == {
                    "telegram_search_messages", "telegram_get_message", "telegram_get_context", "telegram_get_media"}
            assert not private_paths.socket.exists(), "MCP initialization must stay lazy"
            replies = await asyncio.gather(*(client.call_tool("telegram_get_message", {"chat_id": 1, "message_id": i + 1})
                for i, client in enumerate(clients * 3)))
            assert all(not reply.is_error for reply in replies)
            values = [json.loads(reply.structured_content["message"]["text"]["value"]) for reply in replies]
            daemon_pid = values[0]["pid"]
            assert all(item == {"pid": daemon_pid, "maximum": 1} for item in values)
            assert (private_paths.directory.parent / "spawn-count").read_text().splitlines() == ["spawn"]
        # Closing both MCP stdio clients leaves the shared service alive and
        # available to a later task, rather than releasing/reopening TDLib.
        assert (await service_status(paths=private_paths))["pid"] == daemon_pid
        assert (await process_client(private_paths))["pid"] == daemon_pid
    finally:
        await stop_service(paths=private_paths)


@pytest.mark.asyncio
async def test_full_12_mib_media_crosses_real_socket(private_paths):
    class FullMediaBackend(ThreadBackend):
        async def get_media(self, **kwargs):
            return RawMedia(1, 1, "messageDocument", "file", "application/pdf", b"x" * MAX_MEDIA_BYTES)
    async with running(private_paths, FullMediaBackend()):
        backend = SharedTelegramBackend(paths=private_paths, autostart=False)
        result = await backend.get_media(chat_id=1, message_id=1, quality="full", max_bytes=MAX_MEDIA_BYTES)
        assert len(result.data) == 12 * 1024 * 1024 and result.data == b"x" * MAX_MEDIA_BYTES
