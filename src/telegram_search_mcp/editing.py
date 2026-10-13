"""Reviewed, account-bound edits with durable at-most-once native dispatch.

TDLib has no conditional-edit RPC. A fresh version check catches observed edits
on another device; it cannot prevent a change during the final RPC round trip.
The local dispatch marker is durable before the RPC, so uncertain edits are
only reconciled by reading the exact message and are never dispatched again.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, Field, field_validator

from .models import OutputModel, TrustBoundary
from .navigation import Request
from .paths import ensure_private_dir
from .platform_support import fsync_directory
from .policy import _assert_private_file, _atomic_private_json
from .tdjson import TdlibError

EDIT_TTL = 300
ID_PATTERN = r"^[0-9a-f]{32}$"
MAX_RECORD_BYTES = 256 * 1024
CAPTION_KINDS = {
    "messagePhoto", "messageVideo", "messageAnimation", "messageAudio",
    "messageDocument", "messageVoiceNote",
}
POSITIONED_CAPTIONS = {"messagePhoto", "messageVideo", "messageAnimation"}
AUTO_ENTITY_TYPES = {"textEntityTypeMention", "textEntityTypeHashtag", "textEntityTypeCashtag",
    "textEntityTypeBotCommand", "textEntityTypeUrl", "textEntityTypeEmailAddress",
    "textEntityTypePhoneNumber", "textEntityTypeMediaTimestamp", "textEntityTypeBankCardNumber"}


def _nonzero(value: int) -> int:
    if value == 0:
        raise ValueError("chat_id must be nonzero")
    return value


ChatId = Annotated[int, Field(strict=True, ge=-(2**63), lt=2**63), AfterValidator(_nonzero)]
MessageId = Annotated[int, Field(strict=True, ge=1, lt=2**63)]


def _valid_id(edit_id: str) -> None:
    if not isinstance(edit_id, str) or re.fullmatch(ID_PATTERN, edit_id) is None:
        raise ValueError("edit_id must be a UUID as 32 lowercase hexadecimal characters")


def _validate_text(value: str, *, caption: bool | None = None) -> None:
    if not isinstance(value, str):
        raise ValueError("text must be plain text")
    try:
        length = len(value.encode("utf-16-le")) // 2
    except UnicodeError:
        raise ValueError("Invalid message text") from None
    limit = 1024 if caption else 4096
    if "\x00" in value or length > limit:
        raise ValueError(f"Text exceeds {limit} UTF-16 characters or contains NUL")
    if caption is False and not value.strip():
        raise ValueError("A text message must not be blank")


def _validate_replacement(value: str) -> None:
    _validate_text(value)
    # Native clean_input_string/fix_formatted_text remove or rewrite these
    # characters. Reject them before preview rather than dispatch a replacement
    # whose returned text can never match the reviewed text.
    if value != value.strip(" \n"):
        raise ValueError("Replacement must not have blank boundary spaces or newlines")
    if any((ord(char) < 32 and char != "\n") or "\u2028" <= char <= "\u202e"
           or char in {"\u0333", "\u033f", "\u030a"} for char in value):
        raise ValueError("Replacement contains characters Telegram would normalize")
    if any(value[index - 1] in {"\u200e", "\u200f"} and value[index] in {"\u200e", "\u200f"}
           for index in range(1, len(value))):
        raise ValueError("Replacement contains direction marks Telegram would normalize")


class EditRequest(Request):
    edit_id: str = Field(pattern=ID_PATTERN)


class PrepareEditRequest(EditRequest):
    chat_id: ChatId
    message_id: MessageId
    text: str = Field(max_length=4096)

    @field_validator("text")
    @classmethod
    def plain_text(cls, value: str) -> str:
        _validate_replacement(value)
        return value


class EditResult(OutputModel):
    trust_boundary: TrustBoundary = Field(default_factory=TrustBoundary)
    edit_id: str = Field(pattern=ID_PATTERN)
    status: Literal["prepared", "edited", "unchanged", "conflict", "failed", "unknown"]
    kind: Literal["text", "caption"]
    chat_id: ChatId
    message_id: MessageId
    chat_title: str = Field(max_length=256)
    user_id: MessageId
    original_text: str = Field(max_length=4096)
    text: str = Field(max_length=4096)
    created_at: float = Field(ge=0, allow_inf_nan=False)
    expires_at: float = Field(ge=0, allow_inf_nan=False)
    edit_date: int = Field(strict=True, ge=0, lt=2**31)
    error: str | None = Field(default=None, max_length=100)


def _formatted(value: Any) -> dict:
    if (not isinstance(value, dict) or value.get("@type") != "formattedText"
            or not isinstance(value.get("text"), str) or not isinstance(value.get("entities"), list)):
        raise ValueError("Telegram did not provide complete formatted text")
    _validate_text(value["text"])
    return copy.deepcopy(value)


def _stable(value: Any) -> Any:
    """Ignore file transfer state, which can change without a message edit."""
    if isinstance(value, list):
        return [_stable(item) for item in value]
    if not isinstance(value, dict):
        return value
    if value.get("@type") == "file":
        result = {key: _stable(item) for key, item in value.items() if key not in {"local", "remote"}}
        unique_id = (value.get("remote") or {}).get("unique_id")
        if isinstance(unique_id, str) and unique_id:
            result["remote_unique_id"] = unique_id
        return result
    return {key: _stable(item) for key, item in value.items()}


def _content(message: dict) -> tuple[str, dict]:
    content = message.get("content")
    if not isinstance(content, dict):
        raise ValueError("Telegram did not provide message content")
    kind = content.get("@type")
    if kind == "messageAnimatedEmoji":
        # Ordinary standalone emoji are serialized as animated emoji despite
        # being native text messages. Custom/unknown stickers fail closed.
        animated = content.get("animated_emoji") or {}
        sticker = animated.get("sticker") or {}
        full_type = sticker.get("full_type") or {}
        emoji = content.get("emoji")
        if (not isinstance(emoji, str) or not emoji
                or full_type.get("@type") != "stickerFullTypeRegular"):
            raise ValueError("Only ordinary standalone emoji text can be edited")
        _validate_text(emoji)
        return "text", {"@type": "messageText", "text": {"@type": "formattedText", "text": emoji,
            "entities": []}, "link_preview": None, "link_preview_options": None}
    if kind == "messageText":
        _formatted(content.get("text"))
        # Missing differs from explicit null: null is the native default policy.
        if "link_preview_options" not in content or "link_preview" not in content:
            raise ValueError("Link preview settings are unavailable; refusing to change them")
        options = content["link_preview_options"]
        if options is not None:
            if not isinstance(options, dict) or options.get("@type") != "linkPreviewOptions":
                raise ValueError("Invalid link preview settings")
            for flag in ("is_disabled", "force_small_media", "force_large_media", "show_above_text"):
                if type(options.get(flag)) is not bool:
                    raise ValueError("Incomplete link preview settings")
            if not isinstance(options.get("url"), str):
                raise ValueError("Incomplete link preview settings")
        return "text", _stable(content)
    if kind not in CAPTION_KINDS:
        raise ValueError("Only text messages and supported media captions can be edited")
    media_field = {"messagePhoto": "photo", "messageVideo": "video", "messageAnimation": "animation",
        "messageAudio": "audio", "messageDocument": "document", "messageVoiceNote": "voice_note"}[kind]
    if not isinstance(content.get(media_field), dict):
        raise ValueError("Media identity is unavailable; refusing to change it")
    _formatted(content.get("caption"))
    if kind in POSITIONED_CAPTIONS and type(content.get("show_caption_above_media")) is not bool:
        raise ValueError("Caption position is unavailable; refusing to change it")
    if content.get("is_secret") is True:
        raise ValueError("Secret or self-destructing media cannot be edited")
    result = _stable(content)
    # A listen acknowledgement does not change a voice note's content.
    if kind == "messageVoiceNote":
        result.pop("is_listened", None)
        if isinstance(result.get("voice_note"), dict):
            result["voice_note"].pop("speech_recognition_result", None)
    return "caption", result


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _plain_matches(current: Any, desired: Any) -> bool:
    """Plain input may acquire native links/mentions, but no manual formatting."""
    if (not isinstance(current, dict) or not isinstance(desired, dict)
            or current.get("@type") != "formattedText" or current.get("text") != desired.get("text")
            or desired.get("entities") != [] or not isinstance(current.get("entities"), list)):
        return False
    boundaries = {0}
    length = 0
    for char in current["text"]:
        length += 2 if ord(char) > 0xFFFF else 1
        boundaries.add(length)
    for entity in current["entities"]:
        if not isinstance(entity, dict):
            return False
        offset, count = entity.get("offset"), entity.get("length")
        entity_type = entity.get("type")
        if (not isinstance(entity_type, dict) or entity_type.get("@type") not in AUTO_ENTITY_TYPES
                or type(offset) is not int or type(count) is not int or count <= 0
                or offset not in boundaries or offset + count not in boundaries):
            return False
        if entity_type["@type"] == "textEntityTypeMediaTimestamp" and (
                type(entity_type.get("media_timestamp")) is not int or entity_type["media_timestamp"] < 0):
            return False
    return True


def _same_preview_policy(current: Any, desired: Any, formatted: dict) -> bool:
    if current == desired:
        return True
    # TDLib drops a disabled-only preview policy when no URL remains. Explicit
    # defaults also normalize to null. No URL/size/position selection is lost.
    if current is not None or not isinstance(desired, dict) or desired.get("url") != "":
        return False
    if any(desired.get(flag) is not False for flag in ("force_small_media", "force_large_media", "show_above_text")):
        return False
    if desired.get("is_disabled") is False:
        return True
    return desired.get("is_disabled") is True and not any(
        (entity.get("type") or {}).get("@type") == "textEntityTypeUrl" for entity in formatted.get("entities", []))


class MessageEdits:
    def __init__(self, directory: Path, user_id: int) -> None:
        if type(user_id) is not int or not 0 < user_id < 2**63:
            raise ValueError("Invalid Telegram account ID")
        ensure_private_dir(directory)
        self.directory = directory
        self.user_id = user_id
        self.lock = threading.RLock()

    def _account(self, session: Any) -> None:
        if type(session.user_id) is not int or session.user_id != self.user_id:
            raise ValueError("Message edit belongs to a different Telegram account")

    @staticmethod
    def public(value: dict) -> dict:
        return EditResult(**{key: value[key] for key in (
            "edit_id", "status", "kind", "chat_id", "message_id", "chat_title", "user_id",
            "original_text", "text", "created_at", "expires_at", "edit_date", "error",
        )}).model_dump(mode="json")

    def _load(self, edit_id: str) -> dict:
        _valid_id(edit_id)
        path = self.directory / (edit_id + ".json")
        if not path.exists():
            raise ValueError("Message edit not found")
        _assert_private_file(path)
        if path.stat().st_size > MAX_RECORD_BYTES:
            raise ValueError("Invalid message edit record")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if value["user_id"] != self.user_id or value["edit_id"] != edit_id:
                raise ValueError("Message edit belongs to a different Telegram account")
            self.public(value)
            spec = PrepareEditRequest(**value["spec"]).model_dump()
            if (spec["edit_id"] != edit_id or spec["chat_id"] != value["chat_id"]
                    or spec["message_id"] != value["message_id"] or spec["text"] != value["text"]
                    or type(value["dispatched"]) is not bool
                    or _digest(value["original_snapshot"]) != value["original_version"]
                    or _digest(value["desired_content"]) != value["desired_version"]
                    or value["status"] == "prepared" and value["dispatched"]):
                raise ValueError("Invalid message edit record")
            return value
        except (KeyError, TypeError, json.JSONDecodeError):
            raise ValueError("Invalid message edit record") from None

    def _save(self, value: dict) -> None:
        if len(json.dumps(value, ensure_ascii=True).encode()) > MAX_RECORD_BYTES:
            raise ValueError("Message edit snapshot is too large")
        _atomic_private_json(self.directory / (value["edit_id"] + ".json"), value)
        fsync_directory(self.directory)

    def _message(self, session: Any, chat_id: int, message_id: int) -> dict:
        message = session.request({"@type": "getMessage", "chat_id": chat_id, "message_id": message_id})
        self._identity(message, chat_id, message_id)
        return message

    def _identity(self, message: dict, chat_id: int, message_id: int) -> None:
        if (not isinstance(message, dict) or message.get("@type") != "message"
                or type(message.get("chat_id")) is not int or message["chat_id"] != chat_id
                or type(message.get("id")) is not int or message["id"] != message_id):
            raise ValueError("Telegram message did not match the exact chat and message IDs")
        sender = message.get("sender_id") or {}
        if (not isinstance(sender, dict) or sender.get("@type") != "messageSenderUser"
                or type(sender.get("user_id")) is not int or sender["user_id"] != self.user_id):
            raise ValueError("Only messages sent by this Telegram account can be edited")
        # Sent Saved Messages use is_outgoing=false in TDLib. Ownership above
        # still excludes incoming/forwarded messages with a different sender.
        outgoing = message.get("is_outgoing")
        if type(outgoing) is not bool or (not outgoing and chat_id != self.user_id):
            raise ValueError("Incoming messages cannot be edited")
        if message.get("sending_state") is not None or message.get("scheduling_state") is not None:
            raise ValueError("Only already-sent, unscheduled messages can be edited")
        if (message.get("self_destruct_type") is not None or message.get("self_destruct_in", 0) != 0
                or message.get("receiver_id") is not None or message.get("ephemeral_content") is not None):
            raise ValueError("Self-destructing or ephemeral messages cannot be edited")
        if type(message.get("edit_date")) is not int or not 0 <= message["edit_date"] < 2**31:
            raise ValueError("Message edit version is unavailable")

    @staticmethod
    def _can_edit(session: Any, message: dict) -> None:
        try:
            props = session.request({"@type": "getMessageProperties", "chat_id": message["chat_id"],
                                     "message_id": message["id"]})
        except TdlibError as exc:
            # Only a positively identified unsupported RPC can use the legacy
            # native flag. Permission/network failures cannot bypass fresh checks.
            description = exc.message.lower()
            unsupported = (exc.code in {400, 404} and "getmessageproperties" in description
                           and any(word in description for word in ("unknown", "unsupported", "not found")))
            if unsupported and message.get("can_be_edited") is True:
                return
            raise
        if not isinstance(props, dict) or props.get("@type") != "messageProperties" or props.get("can_be_edited") is not True:
            raise ValueError("Telegram does not allow editing this message")

    def _snapshot(self, message: dict) -> dict:
        kind, content = _content(message)
        return {"user_id": self.user_id, "chat_id": message["chat_id"], "message_id": message["id"],
                "sender_id": message["sender_id"], "is_outgoing": message["is_outgoing"],
                "edit_date": message["edit_date"], "kind": kind, "content": content}

    def prepare(self, session: Any, *, edit_id: str, chat_id: int, message_id: int, text: str) -> dict:
        request = PrepareEditRequest(edit_id=edit_id, chat_id=chat_id, message_id=message_id, text=text)
        self._account(session)
        spec = request.model_dump()
        with self.lock:
            path = self.directory / (edit_id + ".json")
            if path.exists():
                value = self._load(edit_id)
                if value["spec"] != spec:
                    raise ValueError("edit_id already belongs to different content; prepare a new edit_id")
                return self.public(value)
            chat = session.get_chat(chat_id)
            if (not isinstance(chat, dict) or chat.get("@type") != "chat"
                    or type(chat.get("id")) is not int or chat["id"] != chat_id
                    or (chat.get("type") or {}).get("@type") not in {
                        "chatTypePrivate", "chatTypeBasicGroup", "chatTypeSupergroup"}):
                raise ValueError("Only accessible non-secret cloud chats can be edited")
            message = self._message(session, chat_id, message_id)
            snapshot = self._snapshot(message)
            _validate_text(text, caption=snapshot["kind"] == "caption")
            self._can_edit(session, message)
            desired = copy.deepcopy(snapshot["content"])
            key = "text" if snapshot["kind"] == "text" else "caption"
            original_text = desired[key]["text"]
            desired[key] = {"@type": "formattedText", "text": text, "entities": []}
            now = time.time()
            unfinished = 0
            for entry in self.directory.iterdir():
                if not re.fullmatch(r"[0-9a-f]{32}\.json", entry.name):
                    continue
                old = self._load(entry.stem)
                if old["status"] == "prepared" and now >= old["expires_at"]:
                    old.update(status="failed", error="prepared_edit_expired")
                    self._save(old)
                elif old["status"] in {"prepared", "unknown"}:
                    unfinished += 1
            if unfinished >= 32:
                raise ValueError("Too many unfinished message edits; review their status")
            value = {"edit_id": edit_id, "user_id": self.user_id, "spec": spec,
                "status": "prepared", "kind": snapshot["kind"], "chat_id": chat_id, "message_id": message_id,
                "chat_title": str(chat.get("title", ""))[:256], "original_text": original_text, "text": text,
                "created_at": now, "expires_at": now + EDIT_TTL, "edit_date": message["edit_date"], "error": None,
                "original_snapshot": snapshot, "original_version": _digest(snapshot),
                "desired_content": desired, "desired_version": _digest(desired), "dispatched": False}
            self._save(value)
            return self.public(value)

    @staticmethod
    def _payload(value: dict) -> dict:
        content = value["desired_content"]
        payload = {"chat_id": value["chat_id"], "message_id": value["message_id"], "reply_markup": None}
        if value["kind"] == "text":
            payload.update({"@type": "editMessageText", "input_message_content": {
                "@type": "inputMessageText", "text": content["text"],
                "link_preview_options": content["link_preview_options"], "clear_draft": False}})
        else:
            payload.update({"@type": "editMessageCaption", "caption": content["caption"],
                "show_caption_above_media": content.get("show_caption_above_media", False)})
        return payload

    @staticmethod
    def _desired(value: dict, snapshot: dict) -> bool:
        # Link previews can be regenerated for new text. Preserve and verify
        # their configuration; their fetched title/media are external metadata.
        current = copy.deepcopy(snapshot["content"])
        desired = copy.deepcopy(value["desired_content"])
        key = "text" if value["kind"] == "text" else "caption"
        if snapshot["kind"] != value["kind"] or not _plain_matches(current.get(key), desired.get(key)):
            return False
        current[key] = desired[key]
        if value["kind"] == "text":
            current.pop("link_preview", None)
            desired.pop("link_preview", None)
            if _same_preview_policy(current.get("link_preview_options"), desired.get("link_preview_options"),
                                    snapshot["content"]["text"]):
                current["link_preview_options"] = desired["link_preview_options"]
        return snapshot["kind"] == value["kind"] and current == desired

    def _expire(self, value: dict) -> bool:
        if value["status"] == "prepared" and time.time() >= value["expires_at"]:
            value.update(status="failed", error="prepared_edit_expired")
            self._save(value)
            return True
        return False

    def apply(self, session: Any, *, edit_id: str) -> dict:
        self._account(session)
        with self.lock:
            value = self._load(edit_id)
            if value["status"] != "prepared":
                # Reconcile uncertain results without ever repeating an edit RPC.
                return self.status(session, edit_id=edit_id) if value["status"] == "unknown" else self.public(value)
            if self._expire(value):
                return self.public(value)
            try:
                message = self._message(session, value["chat_id"], value["message_id"])
                snapshot = self._snapshot(message)
                if _digest(snapshot) != value["original_version"]:
                    value.update(status="conflict", error="message_changed_since_prepare")
                    self._save(value)
                    return self.public(value)
                self._can_edit(session, message)
            except ValueError:
                value.update(status="failed", error="message_no_longer_editable")
                self._save(value)
                return self.public(value)
            except (TdlibError, TimeoutError, RuntimeError):
                # No mutation has been dispatched; this preview remains usable
                # within its original lifetime after a temporary read failure.
                value.update(error="preflight_unavailable")
                self._save(value)
                return self.public(value)
            if self._desired(value, snapshot):
                value.update(status="unchanged", error=None)
                self._save(value)
                return self.public(value)
            if self._expire(value):
                return self.public(value)
            value.update(status="unknown", dispatched=True, dispatched_at=time.time(), error="native_result_unknown")
            self._save(value)
            try:
                response = session.request(self._payload(value))
                self._identity(response, value["chat_id"], value["message_id"])
                if not self._desired(value, self._snapshot(response)):
                    value.update(error="invalid_edit_response")
                    self._save(value)
                    return self.public(value)
                # The native response is server-confirmed; also read the selected
                # message back so the result describes its currently observed state.
                value["native_confirmed"] = True
                self._save(value)
            except TdlibError as exc:
                if exc.message.upper() == "MESSAGE_NOT_MODIFIED":
                    return self.status(session, edit_id=edit_id)
                if exc.code == 408 or exc.code >= 500:
                    # A server/transport failure can follow an accepted edit.
                    return self.public(value)
                value.update(status="failed", error="telegram_refused_edit")
                self._save(value)
                return self.public(value)
            except ValueError:
                value.update(error="invalid_edit_response")
                self._save(value)
                return self.public(value)
            except (TimeoutError, RuntimeError):
                # Do not include a native exception: it can contain private text.
                return self.public(value)
            return self.status(session, edit_id=edit_id)

    def status(self, session: Any, *, edit_id: str) -> dict:
        """Read and reconcile local state; never issue any Telegram mutation."""
        self._account(session)
        with self.lock:
            value = self._load(edit_id)
            if self._expire(value) or value["status"] in {"failed", "conflict"}:
                return self.public(value)
            try:
                message = self._message(session, value["chat_id"], value["message_id"])
                snapshot = self._snapshot(message)
            except (ValueError, TdlibError, TimeoutError, RuntimeError):
                # A missing/inaccessible message does not prove an in-flight edit
                # failed or that it is safe to dispatch again.
                value.update(error="status_readback_unavailable")
                self._save(value)
                return self.public(value)
            if value["dispatched"] and self._desired(value, snapshot):
                value.update(status="edited", edit_date=message["edit_date"], error=None)
            elif value["status"] in {"edited", "unchanged"} and not self._desired(value, snapshot):
                value.update(status="conflict", error="message_changed_after_edit")
            elif _digest(snapshot) != value["original_version"]:
                value.update(status="conflict", error="message_changed_since_prepare")
            elif value["status"] == "prepared":
                value.update(error=None)
            self._save(value)
            return self.public(value)
