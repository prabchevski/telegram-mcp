"""Bounded JSON protocol for the private local service (never pickle or RPC eval)."""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import math
import struct
from dataclasses import asdict
from datetime import datetime
from typing import Any

from .backend import RawMedia, RawMessage, RawMessagePage

PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 32 * 1024
MAX_MEDIA_BYTES = 12 * 1024 * 1024
MAX_RESPONSE_BYTES = 18 * 1024 * 1024
MAX_TIMEOUT = 120.0
READ_OPERATIONS = frozenset({"search_messages", "get_message", "get_context", "get_media", "list_voice_messages"})
SPEECH_OPERATIONS = frozenset({"transcribe_voice"})
MANAGEMENT_OPERATIONS = frozenset({"status", "stop", "check_ready"})
OUTGOING_OPERATIONS = frozenset({"prepare_message", "send_message", "get_send_status"})


class ServiceError(RuntimeError):
    """A failure of the private Telegram service."""


class ServiceProtocolError(ServiceError):
    pass


class ServiceBusyError(ServiceError):
    pass


class ServiceTimeoutError(ServiceError):
    pass


class ServiceStoppingError(ServiceError):
    pass


def _integer(value: Any, low: int, high: int, label: str) -> None:
    if type(value) is not int or not low <= value <= high:
        raise ServiceProtocolError(f"Invalid {label}")


def validate_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"protocol", "id", "operation", "params", "timeout"}:
        raise ServiceProtocolError("Invalid service request")
    if type(value["protocol"]) is not int or value["protocol"] != PROTOCOL_VERSION:
        raise ServiceProtocolError("Service protocol mismatch; stop the service and update all clients")
    ident = value["id"]
    if not isinstance(ident, str) or len(ident) != 32 or any(c not in "0123456789abcdef" for c in ident):
        raise ServiceProtocolError("Invalid request ID")
    timeout = value["timeout"]
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= MAX_TIMEOUT:
        raise ServiceProtocolError("Invalid request timeout")
    operation, params = value["operation"], value["params"]
    if not isinstance(operation, str) or operation not in READ_OPERATIONS | MANAGEMENT_OPERATIONS | OUTGOING_OPERATIONS | SPEECH_OPERATIONS:
        raise ServiceProtocolError("Operation is not permitted")
    if not isinstance(params, dict):
        raise ServiceProtocolError("Invalid operation parameters")
    required = {
        "search_messages": {"query", "cursor", "limit"},
        "get_message": {"chat_id", "message_id"},
        "get_context": {"chat_id", "message_id", "before", "after"},
        "get_media": {"chat_id", "message_id", "quality", "max_bytes"},
        "list_voice_messages": {"chat_id", "before_message_id", "limit"},
        "transcribe_voice": {"chat_id", "message_id", "wait_seconds", "start"},
        "status": set(), "stop": set(), "check_ready": set(),
        "prepare_message": {"draft_id", "recipient", "text", "file_path"},
        "send_message": {"draft_id"}, "get_send_status": {"draft_id"},
    }[operation]
    if set(params) != required:
        raise ServiceProtocolError("Invalid operation parameters")
    if operation in OUTGOING_OPERATIONS:
        from .outgoing import valid_id, validate_content
        try:
            valid_id(params["draft_id"])
            if operation == "prepare_message":
                if not isinstance(params["recipient"], str) or not 1 <= len(params["recipient"]) <= 64:
                    raise ValueError("Invalid recipient")
                validate_content(params["text"], params["file_path"])
        except ValueError as exc:
            raise ServiceProtocolError(str(exc)) from exc
    if "chat_id" in params:
        _integer(params["chat_id"], -(2**63), 2**63 - 1, "chat_id")
    if "message_id" in params:
        _integer(params["message_id"], 1, 2**63 - 1, "message_id")
    if operation == "transcribe_voice":
        _integer(params["wait_seconds"], 0, 60, "wait_seconds")
        if type(params["start"]) is not bool:
            raise ServiceProtocolError("Invalid start flag")
    if operation == "list_voice_messages":
        _integer(params["before_message_id"], 0, 2**63 - 1, "before_message_id")
        _integer(params["limit"], 1, 20, "limit")
    if operation == "search_messages":
        query, cursor = params["query"], params["cursor"]
        if not isinstance(query, str) or not 2 <= len(query) <= 200 or not query.strip():
            raise ServiceProtocolError("Invalid query")
        if cursor is not None and (not isinstance(cursor, str) or not 8 <= len(cursor) <= 512):
            raise ServiceProtocolError("Invalid cursor")
        _integer(params["limit"], 1, 20, "limit")
    elif operation == "get_context":
        for key in ("before", "after"):
            _integer(params[key], 0, 5, key)
    elif operation == "get_media":
        if params["quality"] not in ("preview", "full"):
            raise ServiceProtocolError("Invalid media quality")
        maximum = 2 * 1024 * 1024 if params["quality"] == "preview" else MAX_MEDIA_BYTES
        _integer(params["max_bytes"], 1, maximum, "max_bytes")
    return value


async def read_frame(reader: asyncio.StreamReader, maximum: int) -> Any:
    header = await reader.readexactly(4)
    length = struct.unpack("!I", header)[0]
    if not 1 <= length <= maximum:
        raise ServiceProtocolError("Service frame exceeds its size limit")
    data = await reader.readexactly(length)
    try:
        return json.loads(data, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ServiceProtocolError("Invalid service JSON") from exc


async def write_frame(writer: asyncio.StreamWriter, value: Any, maximum: int) -> None:
    try:
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ServiceProtocolError("Invalid service response") from exc
    if not 1 <= len(payload) <= maximum:
        raise ServiceProtocolError("Service frame exceeds its size limit")
    writer.write(struct.pack("!I", len(payload)) + payload)
    await writer.drain()


def _encode_message(message: RawMessage) -> dict[str, Any]:
    value = asdict(message)
    value["sent_at"] = message.sent_at.isoformat()
    return value


def encode_result(operation: str, result: Any) -> Any:
    if operation in SPEECH_OPERATIONS:
        from .models import TranscriptionResult
        return TranscriptionResult.model_validate(result).model_dump(exclude={"trust_boundary"})
    if operation in OUTGOING_OPERATIONS:
        return _outgoing_result(result)
    if operation in MANAGEMENT_OPERATIONS:
        return result
    if operation in {"search_messages", "list_voice_messages"}:
        if len(result.items) > 20:
            raise ServiceProtocolError("Too many messages in service result")
        return {"items": [_encode_message(item) for item in result.items], "next_cursor": result.next_cursor}
    if operation == "get_message":
        return None if result is None else _encode_message(result)
    if operation == "get_context":
        if len(result) > 11:
            raise ServiceProtocolError("Too many messages in service result")
        return [_encode_message(item) for item in result]
    if operation == "get_media":
        if result is None:
            return None
        if not 1 <= len(result.data) <= MAX_MEDIA_BYTES:
            raise ServiceProtocolError("Media exceeds service transfer limit")
        value = asdict(result)
        value["data"] = base64.b64encode(result.data).decode("ascii")
        return value
    raise ServiceProtocolError("Operation is not permitted")


def _decode_message(value: Any) -> RawMessage:
    if not isinstance(value, dict):
        raise ServiceProtocolError("Invalid message result")
    fields = {"chat_id", "chat_title", "message_id", "sender_id", "sent_at", "text", "content_type"}
    if set(value) != fields:
        raise ServiceProtocolError("Invalid message result")
    _integer(value["chat_id"], -(2**63), 2**63 - 1, "chat_id")
    _integer(value["message_id"], 1, 2**63 - 1, "message_id")
    if value["sender_id"] is not None:
        _integer(value["sender_id"], -(2**63), 2**63 - 1, "sender_id")
    if any(not isinstance(value[key], str) for key in ("chat_title", "sent_at", "text", "content_type")):
        raise ServiceProtocolError("Invalid message text")
    try:
        return RawMessage(**{**value, "sent_at": datetime.fromisoformat(value["sent_at"])})
    except (ValueError, TypeError) as exc:
        raise ServiceProtocolError("Invalid message timestamp") from exc


def decode_result(operation: str, value: Any) -> Any:
    if operation in SPEECH_OPERATIONS:
        from .models import TranscriptionResult
        return TranscriptionResult.model_validate(value).model_dump(exclude={"trust_boundary"})
    if operation in OUTGOING_OPERATIONS:
        return _outgoing_result(value)
    if operation in MANAGEMENT_OPERATIONS:
        if not isinstance(value, dict):
            raise ServiceProtocolError("Invalid service status")
        return value
    if operation == "get_message":
        return None if value is None else _decode_message(value)
    if operation == "get_context":
        if not isinstance(value, list) or len(value) > 11:
            raise ServiceProtocolError("Invalid context result")
        return tuple(_decode_message(item) for item in value)
    if operation in {"search_messages", "list_voice_messages"}:
        if not isinstance(value, dict) or set(value) != {"items", "next_cursor"}:
            raise ServiceProtocolError("Invalid search result")
        items, cursor = value["items"], value["next_cursor"]
        if not isinstance(items, list) or len(items) > 20:
            raise ServiceProtocolError("Invalid search result")
        if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 512):
            raise ServiceProtocolError("Invalid search cursor")
        return RawMessagePage(tuple(_decode_message(item) for item in items), cursor)
    if operation == "get_media":
        if value is None:
            return None
        fields = {"chat_id", "message_id", "content_type", "media_kind", "mime_type", "data", "file_name", "width", "height", "is_preview"}
        if not isinstance(value, dict) or set(value) != fields:
            raise ServiceProtocolError("Invalid media result")
        _integer(value["chat_id"], -(2**63), 2**63 - 1, "chat_id")
        _integer(value["message_id"], 1, 2**63 - 1, "message_id")
        if value["media_kind"] not in ("image", "audio", "file") or type(value["is_preview"]) is not bool:
            raise ServiceProtocolError("Invalid media kind")
        for key in ("content_type", "mime_type", "data"):
            if not isinstance(value[key], str):
                raise ServiceProtocolError("Invalid media metadata")
        if value["file_name"] is not None and not isinstance(value["file_name"], str):
            raise ServiceProtocolError("Invalid media filename")
        for key in ("width", "height"):
            if value[key] is not None:
                _integer(value[key], 1, 16_384, key)
        try:
            data = base64.b64decode(value["data"], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ServiceProtocolError("Invalid media encoding") from exc
        if not 1 <= len(data) <= MAX_MEDIA_BYTES:
            raise ServiceProtocolError("Media exceeds service transfer limit")
        return RawMedia(**{**value, "data": data})
    raise ServiceProtocolError("Operation is not permitted")


def _outgoing_result(value: Any) -> dict:
    from .models import OutgoingState
    from pydantic import ValidationError
    try:
        return OutgoingState.model_validate(value, strict=True).model_dump()
    except ValidationError as exc:
        raise ServiceProtocolError("Invalid outgoing result") from exc
