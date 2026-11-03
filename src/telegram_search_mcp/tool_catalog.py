"""Canonical advertised tool names for every supported local client/platform."""
DEFAULT_TOOLS = (
    "telegram_search_messages",
    "telegram_get_message",
    "telegram_get_context",
    "telegram_get_media",
    "telegram_list_voice_messages",
    "telegram_transcribe_voice",
    "telegram_list_chats",
    "telegram_get_chat_history",
    "telegram_search_chat_messages",
    "telegram_download_file",
    "telegram_get_message_thread",
    "telegram_get_chat_draft",
    "telegram_get_scheduled_messages",
    "telegram_search_public_posts",
    "telegram_get_public_search_quota",
    "telegram_get_me",
    "telegram_get_pin_info",
    "telegram_get_pin_status",
)

PIN_TOOLS = (
    "telegram_pin_message",
    "telegram_unpin_message",
)

EDIT_TOOLS = (
    "telegram_prepare_message_edit",
    "telegram_edit_message",
    "telegram_get_edit_status",
)
OUTGOING_TOOLS = (
    "telegram_prepare_message",
    "telegram_send_message",
    "telegram_get_send_status",
    "telegram_set_chat_draft",
    *EDIT_TOOLS,
)


def advertised_tools(*, sending: bool = False, pinning: bool = False) -> tuple[str, ...]:
    return DEFAULT_TOOLS + (OUTGOING_TOOLS if sending else ()) + (PIN_TOOLS if pinning else ())
