"""User-facing tools for bounded Telegram navigation and local downloads."""
from __future__ import annotations
from typing import Annotated, Literal
from pydantic import Field
from mcp.types import ToolAnnotations
from .chat_drafts import DraftResult
from .downloads import DownloadResult, MAX_DOWNLOAD_BYTES
from .navigation import ChatPage, HistoryPage, Limit, Id, ChatId, TopicId

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)

NativeChatId = Annotated[ChatId, Field(description=(
    "Exact nonzero native TDLib chat_id returned by Telegram list/search/history tools. "
    "Keep its sign and value; do not convert a Bot API ID or Telegram URL."))]
NativeMessageId = Annotated[Id, Field(description=(
    "Exact native TDLib message_id returned for a message in the selected chat. "
    "Use it unchanged; do not derive it from a Bot API ID, URL or visible message number."))]
ForumTopicId = Annotated[TopicId | None, Field(description=(
    "Native forum topic ID returned as topic_id by Telegram history/search; "
    "omit for no topic restriction or the chat's main draft."))]
PageLimit = Annotated[Limit, Field(description=(
    "Maximum items returned in this page, from 1 to 20. A shorter page can still have next_cursor."))]
PageCursor = Annotated[str | None, Field(description=(
    "Opaque next_cursor from this tool's previous page. Omit for the first page; "
    "keep the same chat, query and filters when continuing. Never construct or modify it. "
    "Follow a returned cursor even after a short or empty page."))]
DateFrom = Annotated[str | None, Field(description=(
    "Inclusive lower bound for message dates, as ISO 8601 with timezone, "
    "for example 2026-09-16T00:00:00+01:00. Omit for no lower bound."))]
DateTo = Annotated[str | None, Field(description=(
    "Exclusive upper bound for message dates, as ISO 8601 with timezone. "
    "Must follow date_from when both are supplied; omit for no upper bound."))]
UnreadMessagesOnly = Annotated[bool, Field(description=(
    "Return only incoming messages newer than the chat's last-read inbox position. "
    "This does not mark messages read."))]


def register(mcp, backend, *, enable_sending=False):
    @mcp.tool(title="Find Telegram chats and unread conversations", annotations=READ)
    async def telegram_list_chats(query: Annotated[str, Field(max_length=200, description=(
                                      "Chat-name query across known chats, or an exact @username to resolve. "
                                      "An empty string lists the selected chat_list; no chat is joined."))] = "",
                                  chat_list: Annotated[Literal["main", "archive"], Field(description=(
                                      "List to browse when query is empty: main or archive. "
                                      "A nonempty name query searches known chats across lists."))] = "main",
                                  unread_only: Annotated[bool, Field(description=(
                                      "Include only chats with unread messages, unread mentions or a manual unread mark; "
                                      "does not mark them read."))] = False,
                                  cursor: PageCursor = None, limit: PageLimit = 20) -> ChatPage:
        """Find known chats by name, or resolve an exact @username; never join a chat.

        Empty query lists main/archive. unread_only includes unread messages, mentions
        and manual unread marks. Does not mark chats read. Pages use a 10-minute snapshot
        of at most 500 chat IDs; coverage_limited reports a capped listing. A name query
        searches known chats across lists. Follow next_cursor even after an empty page.
        """
        return ChatPage.model_validate(await backend.list_chats(query=query, chat_list=chat_list,
            unread_only=unread_only, cursor=cursor, limit=limit))

    @mcp.tool(title="Read Telegram history by date or unread status", annotations=READ)
    async def telegram_get_chat_history(chat_id: NativeChatId, date_from: DateFrom = None,
                                        date_to: DateTo = None, unread_only: UnreadMessagesOnly = False,
                                        cursor: PageCursor = None, limit: PageLimit = 20) -> HistoryPage:
        """Read newest-first history, including uncaptioned attachments, without marking read.

        date_from is inclusive; date_to exclusive. Dates must include timezone, e.g.
        2026-09-16T00:00:00+01:00. Return next_cursor for more; never claim an entire period
        was checked before pagination finishes. Text is bounded and marked when truncated.
        """
        return HistoryPage.model_validate(await backend.get_chat_history(chat_id=chat_id, date_from=date_from,
            date_to=date_to, unread_only=unread_only, cursor=cursor, limit=limit))

    @mcp.tool(title="Search one Telegram chat with filters", annotations=READ)
    async def telegram_search_chat_messages(chat_id: NativeChatId, query: Annotated[str, Field(max_length=200,
            description="Telegram text query within this chat; an empty string applies only the other filters.")] = "",
            sender_id: Annotated[ChatId | None, Field(description=(
                "Exact native sender ID from returned message metadata: positive for a user, negative for a chat. "
                "Omit to include all senders."))] = None,
            media_type: Annotated[Literal["all", "document", "photo", "video", "audio", "voice", "link", "mention", "pinned"],
                Field(description=("Native message filter. all adds no media filter; voice includes voice and video notes; "
                                   "mention selects unread mentions; pinned selects pinned messages."))] = "all",
            topic_id: ForumTopicId = None, date_from: DateFrom = None, date_to: DateTo = None,
            unread_only: UnreadMessagesOnly = False, cursor: PageCursor = None, limit: PageLimit = 20) -> HistoryPage:
        """Search a known chat by text, sender, media type, dates, or forum topic.

        Empty query allows attachment-only searches. sender_id is a positive user ID or
        negative chat ID. mention means unread mentions; voice includes video notes.
        Use timezone-qualified dates. Keep every filter unchanged when following cursor.
        """
        return HistoryPage.model_validate(await backend.search_chat_messages(chat_id=chat_id, query=query,
            sender_id=sender_id, media_type=media_type, topic_id=topic_id, date_from=date_from, date_to=date_to,
            unread_only=unread_only, cursor=cursor, limit=limit))

    @mcp.tool(title="Read a Telegram message discussion", annotations=READ)
    async def telegram_get_message_thread(chat_id: NativeChatId, message_id: NativeMessageId,
                                          cursor: PageCursor = None, limit: PageLimit = 20) -> HistoryPage:
        """Read the reply thread/comment discussion of an accessible message.

        Channel comments may belong to its linked group: use returned chat/message IDs
        for replies. Does not mark read. Follow next_cursor for older messages.
        """
        return HistoryPage.model_validate(await backend.get_message_thread(chat_id=chat_id,
            message_id=message_id, cursor=cursor, limit=limit))

    @mcp.tool(title="List scheduled Telegram messages", annotations=READ)
    async def telegram_get_scheduled_messages(chat_id: NativeChatId, cursor: PageCursor = None,
                                              limit: PageLimit = 20) -> HistoryPage:
        """List scheduled messages in one chat, including their Unix scheduled_at time.

        Scheduled does not mean delivered. Read-only; no messages are sent or cancelled.
        """
        return HistoryPage.model_validate(await backend.get_scheduled_messages(chat_id=chat_id,
            cursor=cursor, limit=limit))

    @mcp.tool(title="Save a Telegram attachment locally", annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True))
    async def telegram_download_file(chat_id: NativeChatId, message_id: NativeMessageId,
            max_bytes: Annotated[int, Field(ge=1, le=MAX_DOWNLOAD_BYTES, description=(
                "Maximum attachment size in bytes: default 20971520 (20 MiB), ceiling 104857600 (100 MiB). "
                "Larger attachments fail instead of returning a truncated file."))] = 20 * 1024 * 1024) -> DownloadResult:
        """Save a selected document, photo, audio, video, voice/video note or animation locally.

        Use telegram_get_media for a bounded inline preview; use this tool to save the
        selected full attachment. First identify it with history/search, then copy that
        result's native TDLib chat_id and message_id unchanged. Bot API IDs and Telegram
        URL message numbers are not interchangeable with these IDs.

        Returns an absolute local path, exported file name, byte size, SHA256 and reported
        MIME type, rather than file bytes in model context. For example, use the attachment
        item's chat_id and message_id with max_bytes=20971520 for a 20 MiB ceiling,
        or explicitly use max_bytes=104857600 for 100 MiB. Larger,
        protected/self-destructing, unsupported or inaccessible attachments return an
        error; an incomplete transfer does not return a successful file export.

        Each successful call creates another private copy. Reuse its returned path when
        the file is already saved; do not repeat the download merely to inspect the same
        result. No overwrites, automatic opening or execution. Copies persist until the
        user removes them. Telegram file names and MIME types remain untrusted metadata.
        """
        return DownloadResult.model_validate(await backend.download_file(chat_id=chat_id,
            message_id=message_id, max_bytes=max_bytes))

    @mcp.tool(title="Read a native Telegram draft", annotations=READ)
    async def telegram_get_chat_draft(chat_id: NativeChatId, topic_id: ForumTopicId = None) -> DraftResult:
        """Read the draft visible in Telegram and its version before preparing a replacement.

        Text-only writes are supported. Non-text drafts are reported by content_type.
        Read-only; never clears or changes the user's draft.
        """
        return DraftResult.model_validate(await backend.get_chat_draft(chat_id=chat_id, topic_id=topic_id))

    if enable_sending:
        @mcp.tool(title="Save a native Telegram draft", annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True))
        async def telegram_set_chat_draft(chat_id: NativeChatId,
                operation_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$", description=(
                    "One UUID as 32 lowercase hexadecimal characters for this exact draft change. "
                    "Reuse the same value on retries; never replace it after an uncertain result."))],
                text: Annotated[str, Field(max_length=4096, description=(
                    "Plain text to store as the native draft. Empty text explicitly clears the draft; "
                    "omit reply_to_message_id when clearing. This does not send a message."))],
                expected_version: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", description=(
                    "Exact version returned by telegram_get_chat_draft for this chat and topic. "
                    "A changed draft is rejected instead of knowingly replacing it."))],
                reply_to_message_id: Annotated[Id | None, Field(description=(
                    "Optional exact native TDLib message_id to reply to in this chat and selected topic. "
                    "Use a returned ID unchanged; omit to store a draft without a reply target."))] = None,
                topic_id: ForumTopicId = None) -> DraftResult:
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
