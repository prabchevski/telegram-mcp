"""Exact-message pin changes with durable, account-bound at-most-once dispatch.

Telegram has no conditional pin RPC. Readback reports the currently observed
state, rather than proving which device caused it. An uncertain result never
authorizes another dispatch, including after a service restart.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, Field

from .models import OutputModel, TrustBoundary
from .navigation import Request, nonzero
from .paths import ensure_private_dir
from .platform_support import fsync_directory
from .policy import _assert_private_file, _atomic_private_json
from .tdjson import TdlibError

ID_PATTERN = r"^[0-9a-f]{32}$"
MAX_RECORD_BYTES = 16 * 1024
ChatId = Annotated[int, Field(strict=True, ge=-(2**63), lt=2**63), AfterValidator(nonzero)]
MessageId = Annotated[int, Field(strict=True, ge=1, lt=2**63)]
OperationId = Annotated[str, Field(strict=True, pattern=ID_PATTERN)]
NotificationFlag = Annotated[bool, Field(strict=True)]


class PinStatusRequest(Request):
    operation_id: OperationId


class UnpinRequest(PinStatusRequest):
    chat_id: ChatId
    message_id: MessageId


class PinRequest(UnpinRequest):
    disable_notification: NotificationFlag = True


class PinResult(OutputModel):
    trust_boundary: TrustBoundary = Field(default_factory=TrustBoundary)
    operation_id: OperationId
    action: Literal["pin", "unpin"]
    status: Literal["pinned", "unpinned", "unchanged", "failed", "unknown"]
    chat_id: ChatId
    message_id: MessageId
    user_id: MessageId
    chat_title: str = Field(max_length=256)
    disable_notification: bool | None
    observed_is_pinned: bool | None
    observed_at: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    created_at: float = Field(ge=0, allow_inf_nan=False)
    dispatched: bool
    error: str | None = Field(default=None, max_length=100)


class MessagePins:
    def __init__(self, directory: Path, user_id: int) -> None:
        if type(user_id) is not int or not 0 < user_id < 2**63:
            raise ValueError("Invalid Telegram account ID")
        ensure_private_dir(directory)
        self.directory = directory
        self.user_id = user_id
        self.lock = threading.RLock()

    def _account(self, session: Any) -> None:
        if type(session.user_id) is not int or session.user_id != self.user_id:
            raise ValueError("Pin operation belongs to a different Telegram account")

    @staticmethod
    def public(value: dict) -> dict:
        return PinResult.model_validate({key: value[key] for key in PinResult.model_fields
                                        if key != "trust_boundary"}, strict=True).model_dump(mode="json")

    def _path(self, operation_id: str) -> Path:
        PinStatusRequest(operation_id=operation_id)
        return self.directory / (operation_id + ".json")

    def _load(self, operation_id: str) -> dict:
        path = self._path(operation_id)
        try:
            _assert_private_file(path)
        except FileNotFoundError:
            raise ValueError("Pin operation not found") from None
        if path.stat().st_size > MAX_RECORD_BYTES:
            raise ValueError("Invalid pin operation record")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            self.public(value)
            request = PinRequest if value["action"] == "pin" else UnpinRequest
            spec = request.model_validate(value["spec"]).model_dump()
            if (value["operation_id"] != operation_id or value["user_id"] != self.user_id
                    or spec["operation_id"] != operation_id
                    or spec["chat_id"] != value["chat_id"] or spec["message_id"] != value["message_id"]
                    or spec.get("disable_notification") != value["disable_notification"]
                    or value["status"] in {"pinned", "unpinned"} and not value["dispatched"]
                    or value["status"] == "pinned" and value["action"] != "pin"
                    or value["status"] == "unpinned" and value["action"] != "unpin"):
                raise ValueError("Invalid or differently owned pin operation record")
            return value
        except (KeyError, TypeError, json.JSONDecodeError):
            raise ValueError("Invalid pin operation record") from None

    def _save(self, value: dict) -> None:
        self.public(value)
        if len(json.dumps(value, ensure_ascii=True).encode()) > MAX_RECORD_BYTES:
            raise ValueError("Pin operation record is too large")
        _atomic_private_json(self._path(value["operation_id"]), value)
        fsync_directory(self.directory)

    @staticmethod
    def _message(session: Any, chat_id: int, message_id: int) -> dict:
        message = session.request({"@type": "getMessage", "chat_id": chat_id, "message_id": message_id})
        if (not isinstance(message, dict) or message.get("@type") != "message"
                or type(message.get("chat_id")) is not int or message["chat_id"] != chat_id
                or type(message.get("id")) is not int or message["id"] != message_id
                or type(message.get("is_pinned")) is not bool):
            raise ValueError("Telegram did not provide the exact message and pin state")
        if message.get("sending_state") is not None or message.get("scheduling_state") is not None:
            raise ValueError("Only already-sent unscheduled messages can be pinned or unpinned")
        if (message.get("self_destruct_type") is not None or message.get("self_destruct_in", 0) != 0
                or message.get("receiver_id") is not None or message.get("ephemeral_content") is not None):
            raise ValueError("Self-destructing or ephemeral messages cannot be pinned or unpinned")
        return message

    @staticmethod
    def _chat(session: Any, chat_id: int) -> dict:
        chat = session.get_chat(chat_id)
        if (not isinstance(chat, dict) or chat.get("@type") != "chat"
                or type(chat.get("id")) is not int or chat["id"] != chat_id
                or not isinstance(chat.get("type"), dict)
                or chat["type"].get("@type") not in {
                    "chatTypePrivate", "chatTypeBasicGroup", "chatTypeSupergroup"}):
            raise ValueError("Only accessible non-secret cloud chats can be pinned or unpinned")
        return chat

    @staticmethod
    def _permission(session: Any, chat_id: int, message_id: int) -> None:
        props = session.request({"@type": "getMessageProperties", "chat_id": chat_id,
                                 "message_id": message_id})
        # Native can_be_pinned covers both pin and unpin. Missing/unsupported
        # properties or a read failure must never bypass the permission check.
        if (not isinstance(props, dict) or props.get("@type") != "messageProperties"
                or props.get("can_be_pinned") is not True):
            raise ValueError("Telegram does not allow pinning or unpinning this message")

    def pin(self, session: Any, *, operation_id: str, chat_id: int, message_id: int,
            disable_notification: bool = True) -> dict:
        request = PinRequest(operation_id=operation_id, chat_id=chat_id, message_id=message_id,
                             disable_notification=disable_notification)
        return self._change(session, "pin", request)

    def unpin(self, session: Any, *, operation_id: str, chat_id: int, message_id: int) -> dict:
        request = UnpinRequest(operation_id=operation_id, chat_id=chat_id, message_id=message_id)
        return self._change(session, "unpin", request)

    def _change(self, session: Any, action: str, request: PinRequest | UnpinRequest) -> dict:
        self._account(session)
        spec = request.model_dump()
        with self.lock:
            path = self._path(request.operation_id)
            # lstat also catches dangling symlinks; never silently replace a
            # hostile or corrupted existing dispatch record.
            try:
                path.lstat()
            except FileNotFoundError:
                pass
            else:
                value = self._load(request.operation_id)
                if value["action"] != action or value["spec"] != spec:
                    raise ValueError("operation_id already belongs to another exact pin operation")
                return self.status(session, operation_id=request.operation_id)
            value = {"operation_id": request.operation_id, "action": action, "status": "unknown",
                     "chat_id": request.chat_id, "message_id": request.message_id, "user_id": self.user_id,
                     "chat_title": "", "disable_notification": spec.get("disable_notification"),
                     "observed_is_pinned": None, "observed_at": None, "created_at": time.time(),
                     "dispatched": False, "error": "preflight_incomplete", "spec": spec}
            self._save(value)
            try:
                chat = self._chat(session, request.chat_id)
                value["chat_title"] = str(chat.get("title", ""))[:256]
                message = self._message(session, request.chat_id, request.message_id)
                value.update(observed_is_pinned=message["is_pinned"], observed_at=time.time())
                if message["is_pinned"] == (action == "pin"):
                    value.update(status="unchanged", error=None)
                    self._save(value)
                    return self.public(value)
                self._permission(session, request.chat_id, request.message_id)
            except ValueError:
                value.update(status="failed", error="target_or_permission_unavailable")
                self._save(value)
                return self.public(value)
            except (TdlibError, TimeoutError, RuntimeError):
                value.update(status="failed", error="preflight_unavailable")
                self._save(value)
                return self.public(value)
            # This fsync completes before any native mutation. If the process
            # dies after this point the ID can only be reconciled, never retried.
            value.update(dispatched=True, status="unknown", error="native_result_unknown")
            self._save(value)
            payload = {"@type": "pinChatMessage" if action == "pin" else "unpinChatMessage",
                       "chat_id": request.chat_id, "message_id": request.message_id}
            if action == "pin":
                payload.update(disable_notification=request.disable_notification, only_for_self=False)
            try:
                response = session.request(payload)
                if not isinstance(response, dict) or response.get("@type") != "ok":
                    value.update(error="invalid_native_response")
                    self._save(value)
                    return self.public(value)
            except TdlibError as exc:
                if (exc.code <= 0 or exc.code == 408 or exc.code >= 500
                        or exc.message.strip().lower() in {"request aborted", "request_aborted"}):
                    return self.public(value)
                value.update(status="failed", error="telegram_refused_pin_change")
                self._save(value)
                return self.public(value)
            except (TimeoutError, RuntimeError):
                return self.public(value)
            return self.status(session, operation_id=request.operation_id)

    def status(self, session: Any, *, operation_id: str) -> dict:
        """Read and reconcile the selected target; never dispatch a mutation."""
        self._account(session)
        with self.lock:
            value = self._load(operation_id)
            if value["status"] == "unknown" and not value["dispatched"]:
                value.update(status="failed", error="interrupted_before_dispatch")
            try:
                self._chat(session, value["chat_id"])
                message = self._message(session, value["chat_id"], value["message_id"])
                value.update(observed_is_pinned=message["is_pinned"], observed_at=time.time())
            except (ValueError, TdlibError, TimeoutError, RuntimeError):
                value.update(observed_is_pinned=None, observed_at=None)
                if value["status"] != "failed":
                    value.update(status="unknown", error="status_readback_unavailable")
                self._save(value)
                return self.public(value)
            if value["status"] != "failed":
                if message["is_pinned"] == (value["action"] == "pin"):
                    value.update(status=("pinned" if value["action"] == "pin" else "unpinned")
                                 if value["dispatched"] else "unchanged", error=None)
                else:
                    value.update(status="unknown", error="observed_state_differs_from_requested")
            self._save(value)
            return self.public(value)
