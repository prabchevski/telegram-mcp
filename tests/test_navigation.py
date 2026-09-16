from datetime import datetime, timezone

import pytest

from telegram_search_mcp.navigation import (
    Navigation, ChatListRequest, HistoryRequest, SearchRequest, ThreadRequest,
    ScheduledRequest, cursor_position, fingerprint,
)
from telegram_search_mcp.wire import validate_request, ServiceProtocolError


class Session:
    user_id = 7

    def __init__(self):
        self.calls = []
        self.ids = list(range(1, 7))
        self.rows = [self.message(i) for i in range(100, 0, -1)]

    def message(self, i):
        return {"id": i, "chat_id": 1, "date": 1_700_000_000 + i,
                "is_outgoing": i % 2 == 0, "sender_id": {"user_id": 42},
                "content": {"@type": "messageDocument", "caption": {"text": ""},
                            "document": {"file_name": "sheet.xlsx", "document": {"id": i, "size": 100}}}}

    def get_chat(self, chat_id, timeout=None):
        return {"id": chat_id, "title": "Chat " + str(chat_id),
                "type": {"@type": "chatTypeSecret" if chat_id == 2 else "chatTypePrivate"},
                "last_read_inbox_message_id": 94, "unread_count": 4 if chat_id % 2 else 0}

    def request(self, payload, timeout=None):
        self.calls.append(payload)
        kind = payload["@type"]
        if kind in {"getChats", "searchChats"}:
            return {"chat_ids": self.ids}
        if kind == "getChatMessageByDate":
            return next(m for m in self.rows if m["date"] <= payload["date"])
        if kind == "getChatScheduledMessages":
            return {"messages": self.rows}
        if kind in {"getChatHistory", "searchChatMessages", "getMessageThreadHistory"}:
            before = payload["from_message_id"]
            return {"messages": [m for m in self.rows if not before or m['id'] <= before][:payload['limit']]}
        raise AssertionError(payload)


def test_chats_cursor_keeps_snapshot_and_filters_secrets_unread():
    nav, session = Navigation(), Session()
    first = nav.list_chats(session, ChatListRequest(unread_only=True, limit=1))
    assert [c['chat_id'] for c in first['items']] == [1]
    session.ids.reverse()
    second = nav.list_chats(session, ChatListRequest(unread_only=True, limit=2, cursor=first['next_cursor']))
    assert [c['chat_id'] for c in second['items']] == [3, 5]
    with pytest.raises(ValueError, match='filters changed'):
        nav.list_chats(session, ChatListRequest(cursor=first['next_cursor']))
    assert len(session.calls) == 1


def test_history_pages_have_no_duplicates_and_keep_uncaptioned_files():
    nav, session = Navigation(), Session()
    first = nav.messages(session, HistoryRequest(chat_id=1, limit=20))
    second = nav.messages(session, HistoryRequest(chat_id=1, limit=20, cursor=first['next_cursor']))
    assert [m['message_id'] for m in first['items'] + second['items']] == list(range(100, 60, -1))
    assert first['items'][0]['file_name']['value'] == 'sheet.xlsx'
    assert first['items'][0]['text']['value'] == ''
    with pytest.raises(ValueError, match='changed filters'):
        nav.messages(session, HistoryRequest(chat_id=3, cursor=first['next_cursor']))


def test_dates_are_timezone_aware_and_exclusive_at_upper_bound():
    nav, session = Navigation(), Session()
    iso = lambda i: datetime.fromtimestamp(1_700_000_000 + i, timezone.utc).isoformat()
    page = nav.messages(session, HistoryRequest(chat_id=1, date_from=iso(90), date_to=iso(95)))
    assert [m['message_id'] for m in page['items']] == [94, 93, 92, 91, 90]
    assert page['next_cursor'] is None
    assert session.calls[0]['date'] == 1_700_000_094
    with pytest.raises(ValueError, match='timezone'):
        HistoryRequest(chat_id=1, date_from='2026-09-16T00:00:00')


def test_unread_reads_do_not_acknowledge_and_skip_outgoing():
    nav, session = Navigation(), Session()
    result = nav.messages(session, HistoryRequest(chat_id=1, unread_only=True))
    assert [m['message_id'] for m in result['items']] == [99, 97, 95]
    assert all(m['is_unread'] for m in result['items'])
    assert {p['@type'] for p in session.calls} == {'getChatHistory'}


def test_filters_reach_tdlib_and_cursors_are_bound_to_them():
    nav, session = Navigation(), Session()
    request = SearchRequest(chat_id=1, sender_id=-100, media_type='document', topic_id=9)
    result = nav.messages(session, request)
    payload = session.calls[0]
    assert payload['sender_id'] == {'@type': 'messageSenderChat', 'chat_id': -100}
    assert payload['filter'] == {'@type': 'searchMessagesFilterDocument'}
    assert payload['topic_id'] == {'@type': 'messageTopicForum', 'forum_topic_id': 9}
    with pytest.raises(ValueError, match='changed filters'):
        nav.messages(session, SearchRequest(chat_id=1, cursor=result['next_cursor']))


def test_small_tdlib_pages_are_followed_instead_of_assumed_complete():
    session, nav = Session(), Navigation()
    original = session.request
    def small(payload, timeout=None):
        result = original(payload, timeout)
        result['messages'] = result['messages'][:3]
        return result
    session.request = small
    page = nav.messages(session, HistoryRequest(chat_id=1, limit=8))
    assert [m['message_id'] for m in page['items']] == list(range(100, 92, -1))


def test_thread_messages_use_linked_group_chat_and_scheduled_pagination():
    nav, session = Navigation(), Session()
    session.rows[0]['chat_id'] = -10
    page = nav.messages(session, ThreadRequest(chat_id=1, message_id=5, limit=1))
    assert page['items'][0]['chat_id'] == -10
    assert page['items'][0]['chat_title']['value'] == 'Chat -10'
    session.rows[0]['chat_id'] = 1
    first = nav.messages(session, ScheduledRequest(chat_id=1, limit=20))
    second = nav.messages(session, ScheduledRequest(chat_id=1, limit=20, cursor=first['next_cursor']))
    assert len({m['message_id'] for m in first['items'] + second['items']}) == 40


@pytest.mark.parametrize('params', [{'chat_id': True}, {'chat_id': 1, 'limit': 1000}, {'chat_id': 1, 'unexpected': 'eval'}])
def test_wire_rejects_invalid_navigation_before_dispatch(params):
    with pytest.raises(ServiceProtocolError):
        validate_request({'protocol': 1, 'id': 'a' * 32, 'operation': 'get_chat_history', 'params': params, 'timeout': 20})
