"""Bounded cloud-chat navigation. No viewMessages/read acknowledgements are sent."""
from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from .models import OutputModel, TrustBoundary, UntrustedText, MessageRecord
from .tdjson import TdlibError

def nonzero(value: int) -> int:
    if value == 0:
        raise ValueError("A nonzero chat or sender ID is required")
    return value


Id = Annotated[int, Field(strict=True, ge=1, lt=2**53)]
TopicId = Annotated[int, Field(strict=True, ge=1, lt=2**31)]
ChatId = Annotated[int, Field(strict=True, gt=-(2**53), lt=2**53), AfterValidator(nonzero)]
Limit = Annotated[int, Field(strict=True, ge=1, le=20)]
FILTERS = {"all": None, "document": "Document", "photo": "Photo", "video": "Video",
           "audio": "Audio", "voice": "VoiceAndVideoNote", "link": "Url", "mention": "UnreadMention", "pinned": "Pinned"}


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ChatListRequest(Request):
    query: str = Field(default="", max_length=200)
    chat_list: Literal["main", "archive"] = "main"
    unread_only: bool = False
    cursor: str | None = Field(default=None, max_length=512)
    limit: Limit = 20


def timestamp(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError()
        result = int(date.timestamp())
        if not 0 < result < 2**31:
            raise ValueError()
        return result
    except (ValueError, OverflowError):
        raise ValueError("Dates must be ISO 8601 with a timezone, for example 2026-09-16T10:00:00+01:00") from None


class HistoryRequest(Request):
    chat_id: ChatId
    date_from: str | None = Field(default=None, max_length=40)
    date_to: str | None = Field(default=None, max_length=40)
    unread_only: bool = False
    cursor: str | None = Field(default=None, max_length=512)
    limit: Limit = 20

    @model_validator(mode="after")
    def dates(self):
        low, high = timestamp(self.date_from), timestamp(self.date_to)
        if low is not None and high is not None and low >= high:
            raise ValueError("date_from must precede date_to (exclusive)")
        return self


class SearchRequest(HistoryRequest):
    query: str = Field(default="", max_length=200)
    sender_id: ChatId | None = None
    media_type: Literal["all", "document", "photo", "video", "audio", "voice", "link", "mention", "pinned"] = "all"
    topic_id: TopicId | None = None


class ThreadRequest(Request):
    chat_id: ChatId
    message_id: Id
    cursor: str | None = Field(default=None, max_length=512)
    limit: Limit = 20


class ScheduledRequest(Request):
    chat_id: ChatId
    cursor: str | None = Field(default=None, max_length=512)
    limit: Limit = 20


class ChatRecord(OutputModel):
    chat_id: int
    title: UntrustedText
    chat_type: str
    unread_count: int
    unread_mention_count: int
    is_marked_as_unread: bool
    last_read_inbox_message_id: int
    last_message_id: int | None


class ChatPage(OutputModel):
    trust_boundary: TrustBoundary = Field(default_factory=TrustBoundary)
    items: list[ChatRecord] = Field(max_length=20)
    next_cursor: str | None
    scope: str
    coverage_limited: bool


class HistoryMessage(MessageRecord):
    is_outgoing: bool
    is_unread: bool
    reply_to_message_id: int | None = None
    topic_id: int | None = None
    file_name: UntrustedText | None = None
    file_size: int | None = None
    scheduled_at: int | None = None


class HistoryPage(OutputModel):
    trust_boundary: TrustBoundary = Field(default_factory=TrustBoundary)
    items: list[HistoryMessage] = Field(max_length=20)
    next_cursor: str | None
    scanned_count: int


def text(value: str, maximum: int = 4000) -> UntrustedText:
    return UntrustedText(value=value[:maximum], truncated=len(value) > maximum, original_character_count=len(value))


def cloud_chat(session, chat_id: int) -> dict:
    chat = session.get_chat(chat_id)
    if chat.get("type", {}).get("@type") not in {"chatTypePrivate", "chatTypeBasicGroup", "chatTypeSupergroup"}:
        raise ValueError("Only accessible non-secret cloud chats are supported")
    return chat


def topic(value: int | None) -> dict | None:
    return {"@type": "messageTopicForum", "forum_topic_id": value} if value else None


def message_record(message: dict, chat: dict) -> HistoryMessage:
    content = message.get("content") or {}
    kind = content.get("@type", "messageUnsupported")
    formatted = content.get("text") if kind == "messageText" else content.get("caption")
    value = (formatted or {}).get("text", "")
    file_name, file_size = None, None
    for key in ("document", "audio", "video", "voice_note", "video_note", "animation"):
        media = content.get(key)
        if isinstance(media, dict):
            if media.get("file_name"):
                file_name = text(str(media["file_name"]), 256)
            file = media.get(key.replace("_note", "")) or {}
            file_size = file.get("size") or file.get("expected_size") or None
            recognition = media.get("speech_recognition_result") or {}
            if not value and recognition.get("@type") == "speechRecognitionResultText":
                value = recognition.get("text", "")
    sender = message.get("sender_id") or {}
    reply = message.get("reply_to") or {}
    schedule = message.get("scheduling_state") or {}
    outgoing = bool(message.get("is_outgoing"))
    topic_info = message.get("topic_id") or {}
    return HistoryMessage(chat_id=int(message["chat_id"]), chat_title=text(str(chat.get("title", "")), 256),
        message_id=int(message["id"]), sender_id=sender.get("user_id") or sender.get("chat_id"),
        sent_at=datetime.fromtimestamp(message.get("date", 0), timezone.utc), text=text(str(value)), content_type=kind[:80],
        is_outgoing=outgoing, is_unread=not outgoing and message["id"] > chat.get("last_read_inbox_message_id", 0),
        reply_to_message_id=reply.get("message_id"), topic_id=topic_info.get("forum_topic_id"),
        file_name=file_name, file_size=file_size, scheduled_at=schedule.get("send_date"))


def fingerprint(params: dict, user_id: int) -> str:
    value = {k: v for k, v in params.items() if k not in {"cursor", "limit"}}
    return hashlib.sha256(json.dumps([user_id, value], sort_keys=True).encode()).hexdigest()[:24]


def cursor_at(position: int, fingerprint: str) -> str:
    return base64.urlsafe_b64encode(json.dumps([position, fingerprint]).encode()).decode()


def cursor_position(cursor: str | None, fingerprint: str) -> int:
    if cursor is None:
        return 0
    try:
        position, digest = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if type(position) is not int or not 0 < position < 2**53 or digest != fingerprint:
            raise ValueError()
        return position
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("Invalid cursor or changed filters; start a new search") from None


class Navigation:
    def __init__(self):
        self.snapshots: dict[str, tuple] = {}

    def list_chats(self, session, request: ChatListRequest) -> dict:
        digest = fingerprint(request.model_dump(), session.user_id)
        now = time.monotonic()
        deadline = now + 40
        self.snapshots = {k: v for k, v in self.snapshots.items() if v[0] > now}
        if request.cursor:
            try:
                token, offset = request.cursor.split(":")
                expires, expected, ids, limited = self.snapshots[token]
                offset = int(offset)
                if expected != digest or not 0 <= offset <= len(ids):
                    raise ValueError()
            except (KeyError, ValueError, TypeError):
                raise ValueError("Chat cursor expired or filters changed; start a new listing") from None
        else:
            query = request.query.strip()
            if query.startswith("@"):
                chat = session.request({"@type": "searchPublicChat", "username": query[1:]})
                ids = [chat["id"]]
            else:
                payload = ({"@type": "searchChats", "query": query, "type_filter": None, "limit": 500} if query else
                           {"@type": "getChats", "chat_list": {"@type": "chatListArchive" if request.chat_list == "archive" else "chatListMain"}, "limit": 500})
                ids = list(dict.fromkeys(session.request(payload).get("chat_ids", [])))[:500]
            limited = len(ids) >= 500
            token, offset = uuid.uuid4().hex, 0
            if len(self.snapshots) >= 16:
                self.snapshots.pop(next(iter(self.snapshots)))
            self.snapshots[token] = (now + 600, digest, ids, limited)
        items, scanned = [], 0
        while offset < len(ids) and len(items) < request.limit and scanned < 60 and time.monotonic() < deadline:
            chat = session.get_chat(ids[offset], timeout=max(1, min(5, deadline - time.monotonic())))
            offset += 1
            scanned += 1
            if chat.get("type", {}).get("@type") == "chatTypeSecret":
                continue
            if request.unread_only and not (chat.get("unread_count") or chat.get("is_marked_as_unread") or chat.get("unread_mention_count")):
                continue
            items.append(ChatRecord(chat_id=chat["id"], title=text(str(chat.get("title", "")), 256),
                chat_type=chat.get("type", {}).get("@type", "unknown"), unread_count=chat.get("unread_count", 0),
                unread_mention_count=chat.get("unread_mention_count", 0), is_marked_as_unread=bool(chat.get("is_marked_as_unread")),
                last_read_inbox_message_id=chat.get("last_read_inbox_message_id", 0), last_message_id=(chat.get("last_message") or {}).get("id")))
        return ChatPage(items=items, next_cursor=f"{token}:{offset}" if offset < len(ids) else None,
                        scope="known_chats" if request.query else request.chat_list, coverage_limited=limited).model_dump(mode="json")

    def messages(self, session, request: HistoryRequest | SearchRequest | ThreadRequest | ScheduledRequest) -> dict:
        chat = cloud_chat(session, request.chat_id)
        digest = fingerprint({**request.model_dump(), "kind": type(request).__name__}, session.user_id)
        before = cursor_position(request.cursor, digest)
        low = timestamp(getattr(request, "date_from", None))
        high = timestamp(getattr(request, "date_to", None))
        unread = getattr(request, "unread_only", False)
        read_id = chat.get("last_read_inbox_message_id", 0)
        start_id = before
        if not before and high:
            try:
                anchor = session.request({"@type": "getChatMessageByDate", "chat_id": request.chat_id, "date": high - 1})
                start_id = int(anchor["id"]) + 1
            except TdlibError as exc:
                if exc.code == 404:
                    return HistoryPage(items=[], next_cursor=None, scanned_count=0).model_dump(mode="json")
                raise
        items, scanned, seen = [], 0, set()
        next_id = None
        deadline = time.monotonic() + 45
        for _ in range(5):
            if time.monotonic() >= deadline:
                break
            payload = {"chat_id": request.chat_id, "from_message_id": max(0, start_id - 1), "offset": 0, "limit": 50}
            if isinstance(request, ScheduledRequest):
                payload = {"@type": "getChatScheduledMessages", "chat_id": request.chat_id}
            elif isinstance(request, ThreadRequest):
                payload.update({"@type": "getMessageThreadHistory", "message_id": request.message_id})
            elif isinstance(request, SearchRequest):
                sender = None if request.sender_id is None else ({"@type": "messageSenderUser", "user_id": request.sender_id} if request.sender_id > 0 else {"@type": "messageSenderChat", "chat_id": request.sender_id})
                filter_name = FILTERS[request.media_type]
                payload.update({"@type": "searchChatMessages", "query": request.query, "sender_id": sender,
                    "topic_id": topic(request.topic_id), "filter": {"@type": "searchMessagesFilter" + filter_name} if filter_name else None})
            else:
                payload.update({"@type": "getChatHistory", "only_local": False})
            response = session.request(payload, timeout=max(1, min(15, deadline - time.monotonic())))
            rows = sorted(response.get("messages", []), key=lambda m: m["id"], reverse=True)
            progressed, finished = False, False
            for message in rows:
                mid = message["id"]
                if mid in seen or (start_id and mid >= start_id):
                    continue
                seen.add(mid)
                # A channel post's discussion can live in its linked group.
                if not isinstance(request, ThreadRequest) and message.get("chat_id") != request.chat_id:
                    continue
                progressed = True
                scanned += 1
                next_id = mid
                if low and message.get("date", 0) < low or unread and mid <= read_id:
                    finished = True
                    break
                if high and message.get("date", 0) >= high or unread and message.get("is_outgoing"):
                    continue
                actual_chat = chat if message["chat_id"] == chat["id"] else cloud_chat(session, message["chat_id"])
                items.append(message_record(message, actual_chat))
                if len(items) == request.limit:
                    return HistoryPage(items=items, next_cursor=cursor_at(mid, digest), scanned_count=scanned).model_dump(mode="json")
            if finished or not progressed or isinstance(request, ScheduledRequest):
                next_id = None
                break
            start_id = next_id
        return HistoryPage(items=items, next_cursor=cursor_at(next_id, digest) if next_id else None, scanned_count=scanned).model_dump(mode="json")


REQUESTS = {"list_chats": ChatListRequest, "get_chat_history": HistoryRequest,
            "search_chat_messages": SearchRequest, "get_message_thread": ThreadRequest,
            "get_scheduled_messages": ScheduledRequest}
RESULTS = {name: ChatPage if name == "list_chats" else HistoryPage for name in REQUESTS}
