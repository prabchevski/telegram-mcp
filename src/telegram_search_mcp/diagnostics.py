"""Content-free diagnostics compatible with protocol 1's exact error shape.

Metadata lives in the bounded message because old proxies reject extra error
keys. Never format exception text, request parameters, locals or TDLib replies.
"""
from __future__ import annotations

import logging
import re
import uuid

from .tdjson import TdlibError
from .wire import MANAGEMENT_OPERATIONS, OUTGOING_OPERATIONS, READ_OPERATIONS, SPEECH_OPERATIONS
from .workflows import WORKFLOW_OPERATIONS

LOG = logging.getLogger(__name__)
KNOWN_CODES = frozenset({
    "ServiceError", "ServiceBusyError", "ServiceTimeoutError", "ServiceStoppingError",
    "ServiceProtocolError", "SetupRequiredError", "SessionBusyError", "CursorError",
    "PolicyError", "MediaError", "MediaTooLargeError", "ValueError", "TimeoutError",
})
DETAILS = {
    "validation": "Invalid request parameters.",
    "authorization": "Telegram authorization is required; run tgsearch doctor locally.",
    "access": "Telegram access or local policy denied this operation.",
    "flood_wait": "Telegram rate limit reached; wait before retrying.",
    "timeout": "Request timed out; accepted native work will finish safely.",
    "unavailable": "Telegram service is unavailable; check tgsearch service status.",
    "busy": "Telegram service is busy or draining; wait before retrying.",
    "cursor": "Invalid, expired or filter-mismatched cursor; restart pagination.",
    "media": "Telegram media is unavailable or failed a safety check.",
    "media_too_large": "Telegram media is too large for this request.",
    "not_found": "Telegram could not find the requested resource.",
    "internal": "Internal failure; use diagnostic_id to inspect the local service log.",
}
_FORMATTED = re.compile(
    r"^Telegram operation failed \[operation=([a-z_]+) category=([a-z_]+) "
    r"retryable=(true|false)(?: retry_after=([0-9]+))? diagnostic_id=([0-9a-f]{32})\]: "
)
_OPERATIONS = READ_OPERATIONS | OUTGOING_OPERATIONS | MANAGEMENT_OPERATIONS | SPEECH_OPERATIONS | WORKFLOW_OPERATIONS
# Exact, source-owned messages can provide extra specificity without echoing
# arbitrary exception strings (notably Pydantic's input_value representations).
_SAFE_DETAILS = {
    ("ServiceBusyError", "Telegram request queue is full; retry later"): "Telegram request queue is full; retry later.",
    ("ServiceProtocolError", "Operation is not permitted"): "Operation is not permitted.",
    ("ServiceProtocolError", "Private service authentication failed"): "Private service authentication failed.",
    ("CursorError", "Invalid cursor or changed filters; start a new search"): "Invalid cursor or changed filters; start a new search.",
    ("CursorError", "Chat cursor expired or filters changed; start a new listing"): "Chat cursor expired or filters changed; start a new listing.",
    ("CursorError", "Invalid or query-mismatched Telegram search cursor"): "Invalid or query-mismatched Telegram search cursor; restart the search.",
    ("CursorError", "Invalid search cursor; restart the search after upgrading"): "Invalid search cursor; restart the search after upgrading.",
}
_ACCESS_ERRORS = frozenset({
    "Can't access the chat", "CHAT_ADMIN_REQUIRED", "CHAT_WRITE_FORBIDDEN",
    "CHANNEL_PRIVATE", "USER_BANNED_IN_CHANNEL",
})
_AUTH_ERRORS = frozenset({"AUTH_KEY_UNREGISTERED", "AUTH_KEY_INVALID", "SESSION_REVOKED", "SESSION_EXPIRED"})


def is_diagnostic(message: str, operation: str) -> bool:
    match = _FORMATTED.match(message)
    return bool(match and match[1] == (operation if operation in _OPERATIONS else "service") and match[2] in DETAILS)


def _classification(exc: Exception) -> tuple[str, bool, int | None]:
    name = type(exc).__name__
    if isinstance(exc, TdlibError):
        if exc.code == 429:
            # Only recognized numeric wait formats are extracted. The raw
            # message may contain private input and must never be logged.
            wait = re.fullmatch(r"(?:Too Many Requests: retry after |FLOOD_WAIT_)([0-9]{1,9})", exc.message)
            return "flood_wait", True, int(wait[1]) if wait else None
        if exc.code == 401:
            return "authorization", False, None
        if exc.code == 403:
            return "access", False, None
        if exc.code == 404:
            return "not_found", False, None
        if exc.code == 408:
            return "timeout", True, None
        if 500 <= exc.code < 600:
            return "unavailable", True, None
        if exc.code == 400:
            if exc.message in _ACCESS_ERRORS:
                return "access", False, None
            if exc.message in _AUTH_ERRORS:
                return "authorization", False, None
            return "validation", False, None
    if name == "CursorError":
        return "cursor", False, None
    if name == "SetupRequiredError":
        return "authorization", False, None
    if name == "PolicyError" or isinstance(exc, PermissionError):
        return "access", False, None
    if name == "ServiceProtocolError" and str(exc) == "Private service authentication failed":
        return "access", False, None
    if isinstance(exc, TimeoutError) or name == "ServiceTimeoutError":
        return "timeout", True, None
    if name in {"ServiceBusyError", "ServiceStoppingError", "SessionBusyError"}:
        return "busy", True, None
    if name in {"_Unavailable", "TdlibStoppedError", "SessionCloseError"} or isinstance(exc, OSError):
        return "unavailable", True, None
    if name == "ServiceError" and str(exc) == "Telegram service could not start. Run `tgsearch doctor` locally.":
        return "unavailable", True, None
    if name == "MediaTooLargeError":
        return "media_too_large", False, None
    if name == "MediaError":
        return "media", False, None
    if isinstance(exc, ValueError) or name == "ServiceProtocolError":
        return "validation", False, None
    return "internal", False, None


def error_result(exc: Exception, operation: str = "service") -> dict[str, str]:
    operation = operation if operation in _OPERATIONS else "service"
    category, retryable, retry_after = _classification(exc)
    # Sending can already have committed when a response is lost. A caller
    # must resolve the existing draft even for an otherwise retryable failure.
    ambiguous_send = operation in OUTGOING_OPERATIONS and category in {
        "internal", "timeout", "unavailable", "busy",
    }
    ambiguous_draft = operation == "set_chat_draft" and category in {"internal", "timeout", "unavailable", "busy"}
    ambiguous_edit = operation in {"prepare_message_edit", "edit_message", "get_edit_status"} and category in {
        "internal", "timeout", "unavailable", "busy",
    }
    if ambiguous_send or ambiguous_draft or ambiguous_edit:
        retryable = False
    ident = uuid.uuid4().hex
    metadata = f"operation={operation} category={category} retryable={str(retryable).lower()}"
    if retry_after is not None:
        metadata += f" retry_after={retry_after}"
    detail = _SAFE_DETAILS.get((type(exc).__name__, str(exc)), DETAILS[category])
    message = f"Telegram operation failed [{metadata} diagnostic_id={ident}]: {detail}"
    if ambiguous_send:
        message += " Check the existing draft ID with get_send_status; never create a replacement to retry."
    if ambiguous_draft:
        message += " Read get_chat_draft and reconcile using the same operation_id before retrying."
    if ambiguous_edit:
        message += " Check the same edit_id with get_edit_status; never create a replacement to retry."

    # Trace exception identity and code locations, but never str/repr(exc),
    # traceback source lines, request frames, exception args or local variables.
    chain, codes, locations, seen = [], [], [], set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        chain.append(type(current).__name__)
        if isinstance(current, TdlibError):
            codes.append(current.code)
        tb = current.__traceback__
        while tb is not None and len(locations) < 16:
            module = tb.tb_frame.f_globals.get("__name__", "")
            if isinstance(module, str) and module.startswith("telegram_search_mcp."):
                locations.append(f"{module}:{tb.tb_frame.f_code.co_name}:{tb.tb_lineno}")
            tb = tb.tb_next
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    LOG.warning("telegram_error diagnostic_id=%s operation=%s category=%s exceptions=%s tdlib_codes=%s locations=%s",
                ident, operation, category, ",".join(chain), codes, ",".join(locations))
    name = type(exc).__name__
    return {"code": name if name in KNOWN_CODES else "ServiceError", "message": message}
