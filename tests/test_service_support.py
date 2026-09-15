"""Isolated subprocess fixture; never constructs TDLib or touches user profiles."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from telegram_search_mcp.backend import RawMessage
from telegram_search_mcp.service import LocalService, ServicePaths
from telegram_search_mcp import service_client


class ProcessBackend:
    def __init__(self):
        self.active = 0
        self.maximum = 0

    async def get_message(self, *, chat_id, message_id):
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        try:
            await asyncio.sleep(0.03)
            return RawMessage(chat_id, "fake", message_id, None, datetime.now(timezone.utc),
                              json.dumps({"pid": os.getpid(), "maximum": self.maximum}), "messageText")
        finally:
            self.active -= 1

    async def close(self):
        assert not self.active


class BlockingProcessBackend(ProcessBackend):
    def __init__(self, directory):
        super().__init__()
        self.directory = directory

    def _block(self):
        self.active += 1
        (self.directory / "started").touch()
        try:
            deadline = time.monotonic() + 10
            while not (self.directory / "release").exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("Fake native worker was not released")
                time.sleep(0.01)
            (self.directory / "completed").touch()
        finally:
            self.active -= 1

    async def get_message(self, **kwargs):
        await asyncio.to_thread(self._block)
        return await super().get_message(**kwargs)

    async def close(self):
        await super().close()
        (self.directory / "closed").touch()


def spawn_fake(profile, paths):
    counter = paths.directory.parent / "spawn-count"
    fd = os.open(counter, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, b"spawn\n")
    finally:
        os.close(fd)
    child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "daemon", str(paths.directory)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    service_client._CHILDREN.append(child)
    return child


async def main():
    mode, directory = sys.argv[1:3]
    paths = ServicePaths(Path(directory))
    if mode in ("daemon", "blocking-daemon"):
        backend = BlockingProcessBackend(paths.directory.parent) if mode == "blocking-daemon" else ProcessBackend()
        service = LocalService(backend, paths=paths, idle_timeout=5)
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, service.request_stop)
        await service.run()
    elif mode == "client":
        service_client._spawn = spawn_fake
        await service_client.ensure_service(paths=paths)
        backend = service_client.SharedTelegramBackend(paths=paths, autostart=False)
        result = await backend.get_message(chat_id=1, message_id=1)
        print(result.text, flush=True)
    else:
        raise RuntimeError("Unknown isolated helper mode")


if __name__ == "__main__":
    if sys.argv[1] == "mcp":
        from telegram_search_mcp.server import create_server
        service_client._spawn = spawn_fake
        create_server(service_client.SharedTelegramBackend(paths=ServicePaths(Path(sys.argv[2])))).run(transport="stdio")
    else:
        asyncio.run(main())
