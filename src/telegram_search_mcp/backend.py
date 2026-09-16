"""Narrow backend contract with optional, separately enabled outgoing operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, Sequence


MediaKind = Literal["image", "audio", "file"]
MediaQuality = Literal["preview", "full"]


class MediaError(RuntimeError):
    """A bounded, user-facing failure while retrieving Telegram media."""


class MediaTooLargeError(MediaError):
    """The requested media exceeds the fixed MCP transfer limit."""


@dataclass(frozen=True, slots=True)
class RawMessage:
    chat_id: int
    chat_title: str
    message_id: int
    sender_id: int | None
    sent_at: datetime
    text: str
    content_type: str


@dataclass(frozen=True, slots=True)
class RawMessagePage:
    items: tuple[RawMessage, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class RawMedia:
    chat_id: int
    message_id: int
    content_type: str
    media_kind: MediaKind
    mime_type: str
    data: bytes
    file_name: str | None = None
    width: int | None = None
    height: int | None = None
    is_preview: bool = False


class TelegramBackend(Protocol):
    """The complete interface the MCP process is allowed to call."""

    async def search_messages(
        self, *, query: str, cursor: str | None, limit: int
    ) -> RawMessagePage: ...

    async def get_message(self, *, chat_id: int, message_id: int) -> RawMessage | None: ...

    async def get_context(
        self, *, chat_id: int, message_id: int, before: int, after: int
    ) -> Sequence[RawMessage]: ...

    async def get_media(
        self,
        *,
        chat_id: int,
        message_id: int,
        quality: MediaQuality,
        max_bytes: int,
    ) -> RawMedia | None: ...

    async def close(self) -> None: ...

    async def prepare_message(self, *, draft_id: str, recipient: str, text: str, file_path: str | None, reply_to_message_id: int | None = None, topic_id: int | None = None, schedule_at: str | None = None) -> dict: ...

    async def send_message(self, *, draft_id: str) -> dict: ...

    async def get_send_status(self, *, draft_id: str) -> dict: ...

    async def list_voice_messages(self, *, chat_id: int, before_message_id: int, limit: int) -> RawMessagePage: ...

    async def transcribe_voice(self, *, chat_id: int, message_id: int, wait_seconds: int, start: bool) -> dict: ...

    async def list_chats(self, **params) -> dict: ...
    async def get_chat_history(self, **params) -> dict: ...
    async def search_chat_messages(self, **params) -> dict: ...
    async def get_message_thread(self, **params) -> dict: ...
    async def get_scheduled_messages(self, **params) -> dict: ...

    async def download_file(self, **params) -> dict: ...

    async def get_chat_draft(self, **params) -> dict: ...

    async def set_chat_draft(self, **params) -> dict: ...
