"""Read regressions using sparse synthetic TDLib IDs and pinned offset semantics."""
from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest

from telegram_search_mcp.navigation import (
    HistoryRequest, Navigation, SearchRequest, ThreadRequest, cursor_position,
)
from telegram_search_mcp.tdjson import TdlibError

CHAT = -1000000000042
STEP = 1 << 20
BASE = 1_700_000_000


def message(number, *, date=None, value="НДС нулевые", outgoing=False):
    return {
        "id": (4_000_000 + number) * STEP, "chat_id": CHAT,
        "date": BASE + number if date is None else date,
        "sender_id": {"@type": "messageSenderUser", "user_id": 42},
        "is_outgoing": outgoing,
        "content": {"@type": "messageText", "text": {"text": value}},
    }


def iso(date):
    return datetime.fromtimestamp(date, timezone.utc).isoformat()


class StrictSession:
    """Unlike contiguous integer mocks, reject fabricated TDLib message IDs.

    The pinned OrderedMessages::get_history moves past an exact from_message_id
    with offset=0; offset=-1 keeps it. Chat search follows the same boundary.
    """

    user_id = 71

    def __init__(self, rows, *, chunk=50):
        self.rows = sorted(rows, key=lambda item: item["id"], reverse=True)
        self.chunk = chunk
        self.calls = []

    def get_chat(self, chat_id, timeout=None):
        return {"id": chat_id, "title": "Synthetic", "type": {"@type": "chatTypeSupergroup"},
                "last_read_inbox_message_id": 0,
                "last_message": self.rows[0] if self.rows else None}

    def request(self, payload, timeout=None):
        self.calls.append(payload)
        kind = payload["@type"]
        if kind == "getChatMessageByDate":
            for row in self.rows:
                if row["date"] <= payload["date"]:
                    return row
            raise TdlibError({"code": 404, "message": "Not found"})
        assert kind in {"getChatHistory", "searchChatMessages", "getMessageThreadHistory"}
        anchor = payload["from_message_id"]
        if anchor and anchor % STEP:
            raise TdlibError({"code": 400, "message": "Invalid value of parameter from_message_id specified"})
        assert payload["offset"] in {0, -1}
        rows = self.rows
        if kind == "searchChatMessages":
            rows = [row for row in rows if payload["query"] in row["content"]["text"]["text"]]
        if anchor:
            index = next((i for i, row in enumerate(rows) if row["id"] < anchor), len(rows))
            rows = rows[max(0, index + payload["offset"]):]
        selected = rows[:min(self.chunk, payload["limit"])]
        result = {"messages": selected}
        if kind == "searchChatMessages":
            result["next_from_message_id"] = selected[-1]["id"] if len(rows) > len(selected) else 0
        return result


def collect(session, request_type, *, limit=3, **params):
    nav, pages, cursor = Navigation(), [], None
    for _ in range(30):
        page = nav.messages(session, request_type(chat_id=CHAT, limit=limit, cursor=cursor, **params))
        pages.append(page)
        if page["next_cursor"] is None:
            return pages
        assert page["next_cursor"] != cursor, "pagination must make progress"
        cursor = page["next_cursor"]
    pytest.fail("pagination did not terminate")


def ids(pages):
    return [item["message_id"] for page in pages for item in page["items"]]


@pytest.mark.parametrize("query", ["НДС", "нулевые"])
def test_short_chat_search_results_do_not_fail_on_internal_continuation(query):
    session = StrictSession([message(3), message(2)], chunk=1)
    page = Navigation().messages(session, SearchRequest(chat_id=CHAT, query=query, limit=20))
    assert ids([page]) == [message(3)["id"], message(2)["id"]]
    assert page["next_cursor"] is None
    assert len(session.calls) == 2
    assert session.calls[1]["from_message_id"] == message(3)["id"]
    assert {call["query"] for call in session.calls} == {query}


@pytest.mark.parametrize("request_type", [HistoryRequest, SearchRequest])
def test_three_plus_pages_preserve_sparse_large_ids_and_terminate(request_type):
    rows = [message(i) for i in range(17, 0, -1)]
    session = StrictSession(rows, chunk=4)
    params = {"query": "НДС"} if request_type is SearchRequest else {}
    pages = collect(session, request_type, **params)
    assert len(pages) >= 3
    assert ids(pages) == [row["id"] for row in rows]
    assert len(ids(pages)) == len(set(ids(pages)))
    assert all(call.get("from_message_id", 0) % STEP == 0 for call in session.calls)


@pytest.mark.parametrize("request_type", [HistoryRequest, SearchRequest])
def test_date_anchor_keeps_newest_and_equal_timestamp_boundaries(request_type):
    rows = [message(9, date=BASE + 3), message(8, date=BASE + 2),
            message(7, date=BASE + 2), message(6, date=BASE + 1),
            message(5, date=BASE + 1), message(4, date=BASE)]
    rows[3]["content"] = {"@type": "messageChatChangeTitle", "title": "Synthetic"}
    rows[4]["content"] = {"@type": "messageUnsupported"}
    if request_type is SearchRequest:
        rows[3:5] = [message(6, date=BASE + 1), message(5, date=BASE + 1)]
    session = StrictSession(rows, chunk=3)
    pages = collect(session, request_type, limit=2, date_from=iso(BASE + 1), date_to=iso(BASE + 3))
    assert ids(pages) == [row["id"] for row in rows[1:5]]
    request = next(call for call in session.calls if "from_message_id" in call)
    assert request["from_message_id"] == rows[1]["id"]
    assert request["offset"] == -1


def test_first_history_without_date_includes_latest_supported_message():
    rows = [message(3), message(2), message(1)]
    session = StrictSession(rows, chunk=1)
    assert ids(collect(session, HistoryRequest)) == [row["id"] for row in rows]


def test_thread_history_preserves_sparse_anchors_across_short_pages():
    rows = [message(i) for i in range(17, 0, -1)]
    session = StrictSession(rows, chunk=2)
    pages = collect(session, ThreadRequest, message_id=rows[0]["id"])
    assert ids(pages) == [row["id"] for row in rows]
    assert len(ids(pages)) == len(set(ids(pages)))
    assert all(call["@type"] == "getMessageThreadHistory" for call in session.calls)
    assert all(call["message_id"] == rows[0]["id"] for call in session.calls)


@pytest.mark.parametrize("request_type", [HistoryRequest, ThreadRequest])
def test_single_cached_row_is_followed_by_a_full_twenty_item_page(request_type):
    rows = [message(i) for i in range(30, 0, -1)]
    session = StrictSession(rows)
    original = session.request

    def cached_first(payload, timeout=None):
        response = original(payload, timeout)
        if len(session.calls) == 1:
            response["messages"] = response["messages"][:1]
        return response

    session.request = cached_first
    params = {"message_id": rows[0]["id"]} if request_type is ThreadRequest else {}
    first = Navigation().messages(session, request_type(chat_id=CHAT, limit=20, **params))
    assert ids([first]) == [row["id"] for row in rows[:20]]
    assert len(session.calls) == 2
    assert session.calls[1]["from_message_id"] == rows[0]["id"]
    assert first["next_cursor"] is not None


def test_search_empty_page_with_continuation_is_not_end_of_results():
    session = StrictSession([])
    tokens = [message(i)["id"] for i in range(10, 4, -1)]
    pages = [
        {"messages": [], "next_from_message_id": token} for token in tokens[:5]
    ] + [{"messages": [message(4)], "next_from_message_id": 0}]

    def request(payload, timeout=None):
        session.calls.append(payload)
        expected = 0 if len(session.calls) == 1 else tokens[len(session.calls) - 2]
        assert payload["from_message_id"] == expected
        return pages[len(session.calls) - 1]

    session.request = request
    result = collect(session, SearchRequest, query="НДС")
    assert result[0]["items"] == []
    assert result[0]["next_cursor"] is not None
    assert ids(result) == [message(4)["id"]]
    assert len(result) == 2


def test_empty_search_has_no_continuation():
    session = StrictSession([message(1)])
    page = Navigation().messages(session, SearchRequest(chat_id=CHAT, query="нет совпадений"))
    assert page["items"] == []
    assert page["next_cursor"] is None
    assert len(session.calls) == 1


def test_filters_and_explicit_defaults_survive_cursor_roundtrip():
    rows = [message(i) for i in range(10, 0, -1)]
    session, nav = StrictSession(rows), Navigation()
    first = nav.messages(session, SearchRequest(chat_id=CHAT, query="НДС", limit=2))
    second = nav.messages(session, SearchRequest(chat_id=CHAT, query="НДС", cursor=first["next_cursor"],
        sender_id=None, media_type="all", topic_id=None, date_from=None, date_to=None,
        unread_only=False, limit=3))
    assert ids([first, second]) == [row["id"] for row in rows[:5]]
    for call in session.calls:
        assert call["sender_id"] is None
        assert call["topic_id"] is None
        assert call["filter"] is None
    filtered = StrictSession(rows)
    nav.messages(filtered, SearchRequest(chat_id=CHAT, query="НДС", sender_id=-1000000000088,
        media_type="document", topic_id=123, date_from=iso(BASE), date_to=iso(BASE + 20),
        unread_only=True, limit=2))
    payload = next(call for call in filtered.calls if call["@type"] == "searchChatMessages")
    assert payload["sender_id"] == {"@type": "messageSenderChat", "chat_id": -1000000000088}
    assert payload["topic_id"] == {"@type": "messageTopicForum", "forum_topic_id": 123}
    assert payload["filter"] == {"@type": "searchMessagesFilterDocument"}
    with pytest.raises(ValueError, match="cursor"):
        nav.messages(session, SearchRequest(chat_id=CHAT, query="нулевые", cursor=first["next_cursor"]))


@pytest.mark.parametrize("raw", [b"{}", b"null", b"[true, \"abc\"]", b"[0, \"abc\"]", b"[]", b"[1,2,3]", b"\xff"])
def test_malformed_cursor_has_specific_error(raw):
    token = base64.urlsafe_b64encode(raw).decode()
    from telegram_search_mcp.tdlib_backend import CursorError
    with pytest.raises(CursorError, match="cursor"):
        cursor_position(token, "abc")


def test_same_timestamp_and_equivalent_timezone_filters_keep_cursor_valid():
    session, nav = StrictSession([message(i, date=BASE) for i in range(7, 0, -1)]), Navigation()
    first = nav.messages(session, HistoryRequest(chat_id=CHAT, date_from=iso(BASE), date_to=iso(BASE + 10), limit=2))
    same_instant = datetime.fromtimestamp(BASE, timezone.utc).isoformat().replace("+00:00", "Z")
    second = nav.messages(session, HistoryRequest(chat_id=CHAT, date_from=same_instant,
        date_to=iso(BASE + 10), limit=3, cursor=first["next_cursor"]))
    assert ids([first, second]) == [row["id"] for row in session.rows[:5]]


@pytest.mark.parametrize("request_type", [HistoryRequest, SearchRequest])
def test_date_bounded_first_page_includes_anchor_even_with_single_row_chunks(request_type):
    session = StrictSession([message(3), message(2), message(1)], chunk=1)
    first = Navigation().messages(session, request_type(chat_id=CHAT, date_to=iso(BASE + 10), limit=2))
    assert ids([first]) == [message(3)["id"], message(2)["id"]]


def test_empty_history_page_preserves_continuation_after_filtered_rows():
    rows = [message(i, outgoing=True) for i in range(300, 0, -1)] + [message(0)]
    session = StrictSession(rows)
    pages = collect(session, HistoryRequest, unread_only=True)
    assert pages[0]["items"] == []
    assert pages[0]["next_cursor"] is not None
    assert ids(pages) == [message(0)["id"]]


def test_cursor_is_bound_to_account_and_operation():
    from telegram_search_mcp.tdlib_backend import CursorError
    nav, session = Navigation(), StrictSession([message(3), message(2), message(1)])
    first = nav.messages(session, HistoryRequest(chat_id=CHAT, limit=1))
    with pytest.raises(CursorError):
        nav.messages(session, SearchRequest(chat_id=CHAT, cursor=first["next_cursor"]))
    session.user_id += 1
    with pytest.raises(CursorError):
        nav.messages(session, HistoryRequest(chat_id=CHAT, cursor=first["next_cursor"]))


def test_tdlib_search_failure_is_not_reported_as_empty_results():
    session = StrictSession([])
    def fail(payload, timeout=None):
        raise TdlibError({"code": 400, "message": "Chat not found"})
    session.request = fail
    with pytest.raises(TdlibError, match="Chat not found"):
        Navigation().messages(session, SearchRequest(chat_id=CHAT, query="НДС"))


def test_search_repeated_continuation_fails_instead_of_looping():
    from telegram_search_mcp.tdjson import TdlibProtocolError
    session = StrictSession([])
    def request(payload, timeout=None):
        return {"messages": [], "next_from_message_id": message(1)["id"]}
    session.request = request
    with pytest.raises(TdlibProtocolError, match="did not advance"):
        Navigation().messages(session, SearchRequest(chat_id=CHAT, query="НДС"))


@pytest.mark.parametrize("has_older_match", [False, True])
def test_date_search_with_nonmatching_anchor_filters_newer_match_and_continues(has_older_match):
    rows = [message(9), message(8, value="unrelated"), message(7, value="unrelated")]
    if has_older_match:
        rows.append(message(6))
    session = StrictSession(rows, chunk=1)
    original = session.request
    def request(payload, timeout=None):
        result = original(payload, timeout)
        # TDLib may supply a continuation even on the last nonempty page.
        if payload["@type"] == "searchChatMessages" and result["messages"]:
            result["next_from_message_id"] = result["messages"][-1]["id"]
        return result
    session.request = request
    pages = collect(session, SearchRequest, query="НДС", date_to=iso(BASE + 9))
    assert ids(pages) == ([message(6)["id"]] if has_older_match else [])
    searches = [call for call in session.calls if call["@type"] == "searchChatMessages"]
    assert searches[0]["offset"] == -1
    assert searches[1]["offset"] == 0


def test_search_rejects_overlapping_continuation_before_returning_full_page():
    from telegram_search_mcp.tdjson import TdlibProtocolError
    session = StrictSession([])
    def request(payload, timeout=None):
        return {"messages": [message(3), message(2)], "next_from_message_id": message(3)["id"]}
    session.request = request
    with pytest.raises(TdlibProtocolError, match="overlaps"):
        Navigation().messages(session, SearchRequest(chat_id=CHAT, query="НДС", limit=2))
