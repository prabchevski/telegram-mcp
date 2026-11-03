"""Identity and pin observations never dispatch writes or infer full pin coverage."""
import copy

import pytest
from pydantic import ValidationError

from telegram_search_mcp.account_info import get_me, get_pin_info, PinInfoRequest
from telegram_search_mcp.tdjson import TdlibError


class Session:
    user_id = 123

    def __init__(self):
        self.calls = []
        self.user = {"@type": "user", "id": 123, "first_name": "Name", "last_name": "",
                     "usernames": {"active_usernames": ["example_user"]}, "phone_number": "private"}
        self.chat = {"@type": "chat", "id": -456, "type": {"@type": "chatTypeBasicGroup"}}
        self.message = {"@type": "message", "chat_id": -456, "id": 1048576, "is_pinned": True}
        self.props = {"@type": "messageProperties", "can_be_pinned": False}

    def get_chat(self, chat_id):
        return copy.deepcopy(self.chat)

    def request(self, payload):
        self.calls.append(payload)
        operation = payload["@type"]
        if operation == "getMe":
            return copy.deepcopy(self.user)
        if operation == "getMessageProperties":
            return copy.deepcopy(self.props)
        assert operation in {"getMessage", "getChatPinnedMessage"}
        if isinstance(self.message, Exception):
            raise self.message
        return copy.deepcopy(self.message)


def test_identity_is_bound_and_omits_private_fields():
    session = Session()
    result = get_me(session)
    assert result["user_id"] == 123
    assert result["active_usernames"][0]["value"] == "example_user"
    assert "phone_number" not in result
    assert session.calls == [{"@type": "getMe"}]
    session.user["id"] = 999
    with pytest.raises(ValueError, match="identity"):
        get_me(session)


@pytest.mark.parametrize("message_id,scope,operation", [(None, "newest_pinned_message", "getChatPinnedMessage"),
                                                       (1048576, "exact_message", "getMessage")])
def test_read_reports_exact_scope_and_ids(message_id, scope, operation):
    session = Session()
    result = get_pin_info(session, PinInfoRequest(chat_id=-456, message_id=message_id))
    assert result["scope"] == scope
    assert result["message_id"] == 1048576 and result["is_pinned"] is True
    assert result["can_be_pinned"] is False
    assert session.calls[0]["@type"] == operation
    assert [call["@type"] for call in session.calls] == [operation, "getMessageProperties"]


def test_no_newest_pin_is_explicit_null_but_exact_missing_is_error():
    session = Session()
    session.message = TdlibError({"@type": "error", "code": 404, "message": "not found"})
    result = get_pin_info(session, PinInfoRequest(chat_id=-456))
    assert result["message_id"] is None and result["is_pinned"] is None
    with pytest.raises(TdlibError):
        get_pin_info(session, PinInfoRequest(chat_id=-456, message_id=1048576))


@pytest.mark.parametrize("field,value", [("id", 2), ("chat_id", -789), ("is_pinned", None), ("is_pinned", 1)])
def test_inexact_or_unknown_pin_state_is_rejected(field, value):
    session = Session()
    session.message[field] = value
    with pytest.raises(ValueError, match="exact message"):
        get_pin_info(session, PinInfoRequest(chat_id=-456, message_id=1048576))


def test_unpinned_exact_target_is_valid_and_secret_chat_is_rejected():
    session = Session()
    session.message["is_pinned"] = False
    assert get_pin_info(session, PinInfoRequest(chat_id=-456, message_id=1048576))["is_pinned"] is False
    with pytest.raises(ValueError):
        get_pin_info(session, PinInfoRequest(chat_id=-456))
    session.chat["type"]["@type"] = "chatTypeSecret"
    session.calls.clear()
    with pytest.raises(ValueError, match="non-secret"):
        get_pin_info(session, PinInfoRequest(chat_id=-456))
    assert not session.calls


@pytest.mark.parametrize("params", [{"chat_id": 0}, {"chat_id": True}, {"chat_id": -456, "message_id": 0},
                                  {"chat_id": -456, "message_id": True}, {"chat_id": -456, "other": 1}])
def test_pin_info_request_is_strict(params):
    with pytest.raises(ValidationError):
        PinInfoRequest(**params)
