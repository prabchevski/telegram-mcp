"""Account-bound outgoing drafts and durable at-most-once dispatch through TDLib 1.8.

The ledger is persisted before sendMessage. Uncertain results are never re-sent.
Text and immutable file snapshots stay local until the separate send operation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import threading
import time
from pathlib import Path
from typing import Any

from .paths import ensure_private_dir
from .policy import _assert_private_file, _atomic_private_json
from .tdjson import TdlibError

MAX_FILE_BYTES = 12 * 1024 * 1024
DRAFT_TTL = 24 * 3600
ID_PATTERN = r"^[0-9a-f]{32}$"
FINAL_STATES = {"sent", "failed"}


def valid_id(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(ID_PATTERN, value) is None:
        raise ValueError("draft_id must be a new UUID as 32 lowercase hexadecimal characters")


def validate_content(text: str, file_path: str | None) -> None:
    if not isinstance(text, str) or (file_path is None and not text.strip()):
        raise ValueError("A nonblank message or a file is required")
    limit = 1024 if file_path is not None else 4096
    try:
        length = len(text.encode("utf-16-le")) // 2
    except UnicodeError as exc:
        raise ValueError("Invalid message text") from exc
    if "\x00" in text or length > limit:
        raise ValueError(f"Text exceeds {limit} UTF-16 characters or contains NUL")
    if file_path is not None and (not isinstance(file_path, str) or not Path(file_path).is_absolute() or len(file_path) > 4096):
        raise ValueError("file_path must be an absolute local path")


def resolve_recipient(session: Any, recipient: str) -> dict[str, Any]:
    if re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{3,31}", recipient):
        chat = session.request({"@type": "searchPublicChat", "username": recipient[1:]})
    elif re.fullmatch(r"-?[1-9][0-9]{0,18}", recipient) and -(2**63) < int(recipient) < 2**63:
        chat = session.get_chat(int(recipient))
    elif recipient == "self":
        chat = session.request({"@type": "createPrivateChat", "user_id": session.user_id, "force": False})
    else:
        raise ValueError("recipient must be an exact @username, known numeric chat ID, or self")
    if chat.get("@type") != "chat" or chat.get("type", {}).get("@type") not in {"chatTypePrivate", "chatTypeBasicGroup", "chatTypeSupergroup"}:
        raise ValueError("Recipient must be an accessible non-secret cloud chat")
    return chat


class Outbox:
    def __init__(self, directory: Path, user_id: int) -> None:
        ensure_private_dir(directory)
        self.directory = directory
        self.user_id = user_id
        self.lock = threading.RLock()
        self.changed = threading.Event()
        self.client: Any = None
        self.early: dict[tuple[int, int], dict] = {}
        self.pending: dict[tuple[int, int], str] = {}
        for entry in directory.iterdir():
            if re.fullmatch(ID_PATTERN, entry.name) and (entry / "state.json").exists():
                value = self._load(entry.name)
                if value["status"] == "pending" and value.get("message_id"):
                    self.pending[(value["chat_id"], value["message_id"])] = entry.name

    def attach(self, client: Any) -> None:
        if client is not self.client:
            if self.client is not None:
                self.client.remove_update_handler(self._update)
            client.add_update_handler(self._update)
            self.client = client

    def _load(self, draft_id: str) -> dict:
        valid_id(draft_id)
        folder = self.directory / draft_id
        if folder.is_symlink() or folder.resolve() != folder:
            raise ValueError("Unsafe outgoing draft directory")
        path = folder / "state.json"
        if not path.exists():
            raise ValueError("Outgoing draft not found")
        _assert_private_file(path)
        if path.stat().st_size > 65536:
            raise ValueError("Invalid outgoing draft")
        value = json.loads(path.read_text())
        if value["user_id"] != self.user_id or value["draft_id"] != draft_id:
            raise ValueError("Outgoing draft belongs to a different Telegram account")
        return value

    def _save(self, value: dict) -> None:
        folder = self.directory / value["draft_id"]
        _atomic_private_json(folder / "state.json", value)
        # Persist the rename, not just the file contents, before an external send.
        fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def public(value: dict) -> dict:
        return {key: value.get(key) for key in (
            "draft_id", "status", "chat_id", "chat_title", "text", "file_name",
            "file_size", "file_sha256", "message_id", "detail",
        )}

    def prepare(self, session: Any, *, draft_id: str, recipient: str, text: str, file_path: str | None) -> dict:
        valid_id(draft_id)
        validate_content(text, file_path)
        spec = {"recipient": recipient, "text": text, "file_path": file_path}
        with self.lock:
            if (self.directory / draft_id).exists():
                previous = self._load(draft_id)
                if previous["spec"] != spec:
                    raise ValueError("draft_id already belongs to different content; never reuse it for another message")
                return self.public(previous)
            unfinished = 0
            for p in self.directory.iterdir():
                if not re.fullmatch(ID_PATTERN, p.name) or not (p / "state.json").exists():
                    continue
                old = self._load(p.name)
                if old["status"] == "prepared" and time.time() - old["created_at"] > DRAFT_TTL:
                    old.update(status="failed", detail="Local draft expired without being sent")
                    self._save(old)
                    if old["file_name"]:
                        (p / old["file_name"]).unlink(missing_ok=True)
                elif old["status"] not in FINAL_STATES:
                    unfinished += 1
            if unfinished >= 32:
                raise ValueError("Too many unfinished outgoing drafts; review the local outbox")
            chat = resolve_recipient(session, recipient)
            folder = self.directory / draft_id
            folder.mkdir(mode=0o700)
            try:
                value = {"draft_id": draft_id, "user_id": self.user_id, "status": "prepared", "spec": spec,
                         "chat_id": int(chat["id"]), "chat_title": str(chat.get("title", ""))[:256],
                         "text": text, "file_name": None, "file_size": None, "file_sha256": None,
                         "message_id": None, "detail": "Prepared locally; nothing sent", "created_at": time.time()}
                if file_path is not None:
                    path = Path(file_path)
                    if path.resolve() != path or path.name == "state.json" or len(path.name.encode()) > 200:
                        raise ValueError("File must not traverse symlinks and must have a safe filename")
                    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    with os.fdopen(fd, "rb") as source:
                        info = os.fstat(source.fileno())
                        if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= MAX_FILE_BYTES:
                            raise ValueError("Attachment must be a nonempty regular file up to 12 MiB")
                        digest = hashlib.sha256()
                        size = 0
                        target_fd = os.open(folder / path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        with os.fdopen(target_fd, "wb") as target:
                            while chunk := source.read(65536):
                                size += len(chunk)
                                if size > MAX_FILE_BYTES:
                                    raise ValueError("Attachment grew beyond 12 MiB")
                                digest.update(chunk)
                                target.write(chunk)
                            target.flush()
                            os.fsync(target.fileno())
                        after = os.fstat(source.fileno())
                        if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or size != info.st_size:
                            raise ValueError("Attachment changed while preparing; prepare it again")
                    value.update(file_name=path.name, file_size=size, file_sha256=digest.hexdigest())
                self._save(value)
                parent_fd = os.open(self.directory, os.O_RDONLY)
                try:
                    os.fsync(parent_fd)
                finally:
                    os.close(parent_fd)
                return self.public(value)
            except BaseException:
                shutil.rmtree(folder)
                raise

    def _finish(self, value: dict, message: dict, *, failed: bool = False) -> None:
        if message.get("chat_id") != value["chat_id"] or not message.get("is_outgoing"):
            raise ValueError("Outgoing message response did not match the recipient")
        value.update(status="failed" if failed else "sent", message_id=int(message["id"]),
                     detail="Telegram confirmed failure; no automatic retry" if failed else "Telegram confirmed sending")
        self._save(value)
        for key, draft_id in tuple(self.pending.items()):
            if draft_id == value["draft_id"]:
                self.pending.pop(key, None)
        if value["file_name"]:
            (self.directory / value["draft_id"] / value["file_name"]).unlink(missing_ok=True)

    def _update(self, event: dict) -> None:
        if event.get("@type") not in {"updateMessageSendSucceeded", "updateMessageSendFailed"}:
            return
        message = event.get("message", {})
        key = (message.get("chat_id"), event.get("old_message_id"))
        with self.lock:
            draft_id = self.pending.get(key)
            if draft_id is None:
                if len(self.early) >= 64:
                    self.early.pop(next(iter(self.early)))
                self.early[key] = event
                return
            value = self._load(draft_id)
            if value["status"] in FINAL_STATES:
                self.pending.pop(key, None)
                return
            self._finish(value, message, failed=event["@type"] == "updateMessageSendFailed")
            self.pending.pop(key, None)
            self.changed.set()

    def send(self, session: Any, *, draft_id: str, wait_seconds: float = 15.0) -> dict:
        # Backend operation serialization protects dispatch; this lock also protects
        # receiver callbacks. Never hold it while waiting for a TDLib response.
        with self.lock:
            value = self._load(draft_id)
            if value["status"] != "prepared":
                return self.public(value)
            if time.time() - value["created_at"] > DRAFT_TTL:
                raise ValueError("Draft expired; prepare a new reviewed message")
            content = {"@type": "inputMessageText", "text": {"@type": "formattedText", "text": value["text"], "entities": []},
                       "link_preview_options": {"@type": "linkPreviewOptions", "is_disabled": True}, "clear_draft": False}
            if value["file_name"]:
                path = self.directory / draft_id / value["file_name"]
                _assert_private_file(path)
                if path.stat().st_size != value["file_size"] or hashlib.sha256(path.read_bytes()).hexdigest() != value["file_sha256"]:
                    raise ValueError("Prepared attachment changed; prepare a new reviewed draft")
                content = {"@type": "inputMessageDocument", "document": {"@type": "inputFileLocal", "path": str(path)},
                           "thumbnail": None, "disable_content_type_detection": True, "caption": content["text"]}
            value.update(status="unknown", detail="Dispatch may have started; do not resend with a new draft ID")
            self._save(value)
        try:
            message = session.request({"@type": "sendMessage", "chat_id": value["chat_id"], "topic_id": None,
                                       "reply_to": None, "options": {"@type": "messageSendOptions", "disable_notification": False,
                                       "from_background": False, "scheduling_state": None}, "reply_markup": None,
                                       "input_message_content": content}, timeout=30.0)
        except TdlibError:
            with self.lock:
                value.update(status="failed", detail="Telegram rejected the send; no automatic retry")
                self._save(value)
                if value["file_name"]:
                    (self.directory / draft_id / value["file_name"]).unlink(missing_ok=True)
            return self.public(value)
        except (TimeoutError, RuntimeError):
            return self.public(value)
        with self.lock:
            if message.get("@type") != "message" or message.get("chat_id") != value["chat_id"] or not message.get("is_outgoing"):
                return self.public(value)
            state = (message.get("sending_state") or {}).get("@type")
            if state is None or state == "messageSendingStateFailed":
                self._finish(value, message, failed=state is not None)
                return self.public(value)
            value.update(status="pending", message_id=int(message["id"]), detail="Telegram is sending; query status using this draft ID")
            self._save(value)
            key = (value["chat_id"], value["message_id"])
            self.pending[key] = draft_id
            if event := self.early.pop(key, None):
                self._update(event)
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            with self.lock:
                value = self._load(draft_id)
                if value["status"] != "pending":
                    return self.public(value)
                self.changed.clear()
            self.changed.wait(min(0.25, max(0, deadline - time.monotonic())))
        return self.status(session, draft_id=draft_id)

    def status(self, session: Any, *, draft_id: str) -> dict:
        with self.lock:
            value = self._load(draft_id)
        if value["status"] == "pending":
            try:
                message = session.request({"@type": "getMessage", "chat_id": value["chat_id"], "message_id": value["message_id"]}, timeout=5.0)
                with self.lock:
                    value = self._load(draft_id)
                    if value["status"] == "pending" and message.get("@type") == "message":
                        state = (message.get("sending_state") or {}).get("@type")
                        if state is None or state == "messageSendingStateFailed":
                            self._finish(value, message, failed=state is not None)
            except (TdlibError, TimeoutError):
                # A missing temporary ID is not proof of failure or success.
                pass
        with self.lock:
            return self.public(self._load(draft_id))
