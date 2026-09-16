"""User-facing tools for bounded Telegram navigation and local downloads."""
from __future__ import annotations
from typing import Annotated, Literal
from pydantic import Field
from mcp.types import ToolAnnotations
from .chat_drafts import DraftResult
from .downloads import DownloadResult, MAX_DOWNLOAD_BYTES
from .navigation import ChatPage, HistoryPage, Limit, Id, ChatId, TopicId

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)


def register(mcp, backend, *, enable_sending=False):
    @mcp.tool(title="Find Telegram chats and unread conversations", annotations=READ)
    async def telegram_list_chats(query: Annotated[str, Field(max_length=200)] = "",
                                  chat_list: Literal["main", "archive"] = "main", unread_only: bool = False,
                                  cursor: str | None = None, limit: Limit = 20) -> ChatPage:
        """Find known chats by name, or resolve an exact @username; never join a chat.

        Empty query lists main/archive. unread_only includes unread messages, mentions
        and manual unread marks. Does not mark chats read. Pages use a 10-minute snapshot
        of at most 500 chat IDs; coverage_limited reports a capped listing. A name query
        searches known chats across lists. Follow next_cursor even after an empty page.
        """
        return ChatPage.model_validate(await backend.list_chats(query=query, chat_list=chat_list,
            unread_only=unread_only, cursor=cursor, limit=limit))

    @mcp.tool(title="Read Telegram history by date or unread status", annotations=READ)
    async def telegram_get_chat_history(chat_id: ChatId, date_from: str | None = None,
                                        date_to: str | None = None, unread_only: bool = False,
                                        cursor: str | None = None, limit: Limit = 20) -> HistoryPage:
        """Read newest-first history, including uncaptioned attachments, without marking read.

        date_from is inclusive; date_to exclusive. Dates must include timezone, e.g.
        2026-09-16T00:00:00+01:00. Return next_cursor for more; never claim an entire period
        was checked before pagination finishes. Text is bounded and marked when truncated.
        """
        return HistoryPage.model_validate(await backend.get_chat_history(chat_id=chat_id, date_from=date_from,
            date_to=date_to, unread_only=unread_only, cursor=cursor, limit=limit))

    @mcp.tool(title="Search one Telegram chat with filters", annotations=READ)
    async def telegram_search_chat_messages(chat_id: ChatId, query: Annotated[str, Field(max_length=200)] = "",
            sender_id: ChatId | None = None,
            media_type: Literal["all", "document", "photo", "video", "audio", "voice", "link", "mention", "pinned"] = "all",
            topic_id: TopicId | None = None, date_from: str | None = None, date_to: str | None = None,
            unread_only: bool = False, cursor: str | None = None, limit: Limit = 20) -> HistoryPage:
        """Search a known chat by text, sender, media type, dates, or forum topic.

        Empty query allows attachment-only searches. sender_id is a positive user ID or
        negative chat ID. mention means unread mentions; voice includes video notes.
        Use timezone-qualified dates. Keep every filter unchanged when following cursor.
        """
        return HistoryPage.model_validate(await backend.search_chat_messages(chat_id=chat_id, query=query,
            sender_id=sender_id, media_type=media_type, topic_id=topic_id, date_from=date_from, date_to=date_to,
            unread_only=unread_only, cursor=cursor, limit=limit))

    @mcp.tool(title="Read a Telegram message discussion", annotations=READ)
    async def telegram_get_message_thread(chat_id: ChatId, message_id: Id,
                                          cursor: str | None = None, limit: Limit = 20) -> HistoryPage:
        """Read the reply thread/comment discussion of an accessible message.

        Channel comments may belong to its linked group: use returned chat/message IDs
        for replies. Does not mark read. Follow next_cursor for older messages.
        """
        return HistoryPage.model_validate(await backend.get_message_thread(chat_id=chat_id,
            message_id=message_id, cursor=cursor, limit=limit))

    @mcp.tool(title="List scheduled Telegram messages", annotations=READ)
    async def telegram_get_scheduled_messages(chat_id: ChatId, cursor: str | None = None,
                                              limit: Limit = 20) -> HistoryPage:
        """List scheduled messages in one chat, including their Unix scheduled_at time.

        Scheduled does not mean delivered. Read-only; no messages are sent or cancelled.
        """
        return HistoryPage.model_validate(await backend.get_scheduled_messages(chat_id=chat_id,
            cursor=cursor, limit=limit))

    @mcp.tool(title="Save a Telegram attachment locally", annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True))
    async def telegram_download_file(chat_id: ChatId, message_id: Id,
            max_bytes: Annotated[int, Field(ge=1, le=MAX_DOWNLOAD_BYTES)] = 20 * 1024 * 1024) -> DownloadResult:
        """Save an explicitly selected document, photo, audio or full video to a private folder.

        Returns a local absolute path and SHA256, not file bytes in model context. Default
        maximum 20 MiB; explicit ceiling 100 MiB. No overwrites, automatic opening or execution.
        Protected/self-destructing media is rejected. Copies persist until the user removes
        them. Original preview tools keep their existing limits.
        """
        return DownloadResult.model_validate(await backend.download_file(chat_id=chat_id,
            message_id=message_id, max_bytes=max_bytes))

    @mcp.tool(title="Read a native Telegram draft", annotations=READ)
    async def telegram_get_chat_draft(chat_id: ChatId, topic_id: TopicId | None = None) -> DraftResult:
        """Read the draft visible in Telegram and its version before preparing a replacement.

        Text-only writes are supported. Non-text drafts are reported by content_type.
        Read-only; never clears or changes the user's draft.
        """
        return DraftResult.model_validate(await backend.get_chat_draft(chat_id=chat_id, topic_id=topic_id))

    if enable_sending:
        @mcp.tool(title="Save a native Telegram draft", annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True))
        async def telegram_set_chat_draft(chat_id: ChatId,
                operation_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")],
                text: Annotated[str, Field(max_length=4096)],
                expected_version: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")],
                reply_to_message_id: Id | None = None, topic_id: TopicId | None = None) -> DraftResult:
            """Save a text draft in Telegram for the user to review/send in their Telegram app.

            Requires an explicit user instruction. First read get_chat_draft; pass its version
            so an observed edit is not overwritten. Telegram has no atomic version check:
            simultaneous editing on another device can still race. Empty text explicitly
            clears a draft. Generate one operation_id UUID hex and reuse it on every retry.
            On unknown, inspect get_chat_draft; do not create a new operation to retry blindly.
            Stored is the recorded outcome of this operation, not a fresh read of today's draft.
            """
            return DraftResult.model_validate(await backend.set_chat_draft(chat_id=chat_id,
                operation_id=operation_id, text=text, expected_version=expected_version,
                reply_to_message_id=reply_to_message_id, topic_id=topic_id))
