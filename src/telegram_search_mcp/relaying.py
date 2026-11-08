"""Bounded inline selections and attributed forwards using the existing outbox.

HTML/widget contents are never downloaded or executed here. Native TDLib must
support a media type before it can appear as an inline result or be forwarded.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path

from pydantic import Field

from .models import OutputModel, TrustBoundary, UntrustedText
from .navigation import ChatId, Id, Request, cloud_chat, text
from .outgoing import ID_PATTERN, resolve_recipient
from .paths import ensure_private_dir
from .platform_support import fsync_directory
from .policy import _assert_private_file, _atomic_private_json

INLINE_TTL = 300
MAX_SNAPSHOT_BYTES = 256 * 1024


class GetInlineResultsRequest(Request):
    recipient: str = Field(min_length=1, max_length=64)
    bot_username: str = Field(pattern=r"^@[A-Za-z][A-Za-z0-9_]{3,31}$")
    query: str = Field(default="", max_length=256)
    offset: str = Field(default="", max_length=1024)


class PrepareForwardRequest(Request):
    draft_id: str = Field(pattern=ID_PATTERN)
    recipient: str = Field(min_length=1, max_length=64)
    from_chat_id: ChatId
    message_id: Id


class PrepareInlineRequest(Request):
    draft_id: str = Field(pattern=ID_PATTERN)
    result_set_id: str = Field(pattern=ID_PATTERN)
    result_id: str = Field(min_length=1, max_length=1024)


class InlineResultItem(OutputModel):
    result_id: str = Field(min_length=1, max_length=1024)
    content_type: str = Field(max_length=80)
    title: UntrustedText
    description: UntrustedText


class InlineResultsResult(OutputModel):
    trust_boundary: TrustBoundary = Field(default_factory=TrustBoundary)
    result_set_id: str = Field(pattern=ID_PATTERN)
    chat_id: ChatId
    chat_title: UntrustedText
    bot_user_id: Id
    bot_username: str = Field(max_length=33)
    query: UntrustedText
    expires_at: int
    items: list[InlineResultItem] = Field(max_length=20)
    next_offset: str = Field(max_length=1024)
    coverage_limited: bool


def _snapshot(directory: Path, result_set_id: str, user_id: int) -> dict:
    PrepareInlineRequest(draft_id=result_set_id, result_set_id=result_set_id, result_id="check")
    ensure_private_dir(directory)
    path = directory / (result_set_id + ".json")
    _assert_private_file(path)
    if path.stat().st_size > MAX_SNAPSHOT_BYTES:
        raise ValueError("Invalid inline result snapshot")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("user_id") != user_id:
        raise ValueError("Inline results belong to a different Telegram account")
    if time.time() >= value["expires_at"]:
        raise ValueError("Inline results expired; query the bot again before preparing a new message")
    return value


def get_inline_results(session, *, directory: Path, **params) -> dict:
    request = GetInlineResultsRequest.model_validate(params)
    if "\x00" in request.query or "\x00" in request.offset:
        raise ValueError("Inline query and offset must not contain NUL")
    chat = resolve_recipient(session, request.recipient)
    bot_chat = resolve_recipient(session, request.bot_username)
    bot_id = (bot_chat.get("type") or {}).get("user_id")
    if type(bot_id) is not int or bot_id <= 0:
        raise ValueError("The exact username must resolve to an inline bot")
    user = session.request({"@type": "getUser", "user_id": bot_id})
    bot_type = user.get("type") or {}
    if (user.get("@type") != "user" or user.get("id") != bot_id
            or bot_type.get("@type") != "userTypeBot" or bot_type.get("is_inline") is not True):
        raise ValueError("The exact username must resolve to an inline bot")
    ensure_private_dir(directory)
    now = time.time()
    active = 0
    for path in directory.glob("*.json"):
        _assert_private_file(path)
        if now - path.stat().st_mtime >= INLINE_TTL:
            path.unlink()
        else:
            active += 1
    if active >= 32:
        raise ValueError("Too many active inline selections; wait for their expiry")
    response = session.request({"@type": "getInlineQueryResults", "bot_user_id": bot_id,
        "chat_id": chat["id"], "user_location": None, "query": request.query, "offset": request.offset})
    query_id = response.get("inline_query_id")
    if not (type(query_id) is int or isinstance(query_id, str)
            and re.fullmatch(r"-?(?:0|[1-9][0-9]{0,18})", query_id)):
        raise ValueError("Telegram returned invalid inline query identity")
    numeric_query_id = int(query_id)
    if (not -(2**63) <= numeric_query_id < 2**63
            or response.get("@type") != "inlineQueryResults" or not isinstance(response.get("results"), list)
            or not isinstance(response.get("next_offset"), str) or len(response["next_offset"]) > 1024):
        raise ValueError("Telegram returned invalid inline results")
    items = []
    for result in response["results"][:20]:
        if not isinstance(result, dict):
            raise ValueError("Telegram returned an invalid inline result")
        identifier, kind = result.get("id"), result.get("@type")
        if (not isinstance(identifier, str) or not 1 <= len(identifier) <= 1024 or "\x00" in identifier
                or not isinstance(kind, str) or not 1 <= len(kind) <= 80):
            raise ValueError("Telegram returned an invalid inline result identity")
        items.append(InlineResultItem(result_id=identifier, content_type=kind,
            title=text(str(result.get("title", "")), 256),
            description=text(str(result.get("description", "")), 512)))
    if len({item.result_id for item in items}) != len(items):
        raise ValueError("Telegram returned duplicate inline result identities")
    token = uuid.uuid4().hex
    preview = InlineResultsResult(result_set_id=token, chat_id=chat["id"],
        chat_title=text(str(chat.get("title", "")), 256), bot_user_id=bot_id,
        bot_username=request.bot_username, query=text(request.query, 256),
        expires_at=int(now) + INLINE_TTL, items=items, next_offset=response["next_offset"],
        coverage_limited=len(response["results"]) > 20).model_dump(mode="json")
    _atomic_private_json(directory / (token + ".json"), {"user_id": session.user_id,
        "query_id": str(numeric_query_id), "recipient": request.recipient, **preview})
    fsync_directory(directory)
    return preview


def _forward_source(session, chat_id: int, message_id: int) -> tuple[dict, str]:
    chat = cloud_chat(session, chat_id)
    if chat.get("id") != chat_id or chat.get("has_protected_content") is True:
        raise ValueError("Source chat is protected or does not match")
    message = session.request({"@type": "getMessage", "chat_id": chat_id, "message_id": message_id})
    if (message.get("@type") != "message" or message.get("chat_id") != chat_id
            or message.get("id") != message_id or message.get("sending_state")
            or message.get("scheduling_state") or message.get("has_protected_content") is True):
        raise ValueError("Source must be the exact sent, unprotected message")
    props = session.request({"@type": "getMessageProperties", "chat_id": chat_id, "message_id": message_id})
    if props.get("@type") != "messageProperties" or props.get("can_be_forwarded") is not True:
        raise ValueError("Telegram does not allow forwarding this message")
    def stable(value):
        if isinstance(value, dict):
            # File download/upload progress is not message content.
            ignored = {"local", "remote"} if value.get("@type") == "file" else set()
            return {key: stable(item) for key, item in value.items() if key not in ignored}
        return [stable(item) for item in value] if isinstance(value, list) else value
    payload = {"content": stable(message.get("content")), "edit_date": message.get("edit_date", 0)}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return message, fingerprint


def _existing(outbox, draft_id, spec):
    with outbox.lock:
        if (outbox.directory / draft_id).exists():
            previous = outbox._load(draft_id)
            if previous["spec"] != spec:
                raise ValueError("draft_id already belongs to different content")
            return outbox.public(previous)


def prepare_forward(outbox, session, **params) -> dict:
    if outbox.user_id != session.user_id:
        raise ValueError("Outbox belongs to a different Telegram account")
    request = PrepareForwardRequest.model_validate(params)
    spec = {"relay": "forward", **request.model_dump(exclude={"draft_id"})}
    if previous := _existing(outbox, request.draft_id, spec):
        return previous
    message, fingerprint = _forward_source(session, request.from_chat_id, request.message_id)
    content = message.get("content") or {}
    caption = (content.get("text") or content.get("caption") or {}).get("text", "")
    preview = (f"Forward from chat {request.from_chat_id}, message {request.message_id}; "
               f"type {content.get('@type', 'unknown')}; attribution preserved\n{str(caption)[:1500]}")
    return outbox.prepare(session, draft_id=request.draft_id, recipient=request.recipient,
        text=preview, file_path=None, _spec=spec, _relay={"kind": "forward",
        "from_chat_id": request.from_chat_id, "message_id": request.message_id, "fingerprint": fingerprint})


def prepare_inline(outbox, session, *, directory: Path, **params) -> dict:
    if outbox.user_id != session.user_id:
        raise ValueError("Outbox belongs to a different Telegram account")
    request = PrepareInlineRequest.model_validate(params)
    spec = {"relay": "inline", **request.model_dump(exclude={"draft_id"})}
    if previous := _existing(outbox, request.draft_id, spec):
        return previous
    snapshot = _snapshot(directory, request.result_set_id, session.user_id)
    selected = next((item for item in snapshot["items"] if item["result_id"] == request.result_id), None)
    if selected is None:
        raise ValueError("Result was not returned in this exact inline selection")
    chat = cloud_chat(session, snapshot["chat_id"])
    if chat.get("id") != snapshot["chat_id"]:
        raise ValueError("Inline recipient identity changed")
    if chat.get("draft_message") is not None:
        raise ValueError("Inline sending clears the native draft; preserve or send that draft first")
    preview = (f"Inline bot {snapshot['bot_username']} ({snapshot['bot_user_id']}), result {request.result_id}; "
               f"type {selected['content_type']}; via-bot attribution preserved\n"
               f"{selected['title']['value']}\n{selected['description']['value']}")[:4096]
    return outbox.prepare(session, draft_id=request.draft_id, recipient=str(snapshot["chat_id"]),
        text=preview, file_path=None, _spec=spec, _relay={"kind": "inline",
        "query_id": snapshot["query_id"], "result_id": request.result_id, "expires_at": snapshot["expires_at"]})


def dispatch_payload(session, value: dict) -> dict:
    if value["user_id"] != session.user_id:
        raise ValueError("Outbox belongs to a different Telegram account")
    relay = value["relay"]
    options = {"@type": "messageSendOptions", "paid_message_star_count": 0, "allow_paid_broadcast": False,
               "disable_notification": False, "from_background": False}
    common = {"chat_id": value["chat_id"], "topic_id": None, "options": options}
    if relay["kind"] == "forward":
        _, fingerprint = _forward_source(session, relay["from_chat_id"], relay["message_id"])
        if fingerprint != relay["fingerprint"]:
            raise ValueError("Source message changed; review and prepare a new forward")
        return {"@type": "forwardMessages", **common, "from_chat_id": relay["from_chat_id"],
                "message_ids": [relay["message_id"]], "send_copy": False, "remove_caption": False}
    if relay["kind"] != "inline" or time.time() >= relay["expires_at"]:
        raise ValueError("Inline results expired; query and review a new result")
    if cloud_chat(session, value["chat_id"]).get("draft_message") is not None:
        raise ValueError("Inline sending would clear an existing native draft")
    return {"@type": "sendInlineQueryResultMessage", **common, "reply_to": None,
            "query_id": relay["query_id"], "result_id": relay["result_id"], "hide_via_bot": False}
