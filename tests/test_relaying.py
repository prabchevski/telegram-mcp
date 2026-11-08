from __future__ import annotations

import copy
import json
import uuid

import pytest

from telegram_search_mcp.outgoing import Outbox
from telegram_search_mcp.platform_support import assert_private_path
from telegram_search_mcp.relaying import (
    INLINE_TTL, InlineResultsResult, get_inline_results, prepare_forward, prepare_inline,
)
from telegram_search_mcp.tdjson import TdlibError


class Session:
    user_id = 99

    def __init__(self):
        self.calls = []
        self.client = self
        self.handlers = []
        self.forwardable = True
        self.protected = False
        self.draft = None
        self.behavior = "success"
        self.source = {"@type": "message", "chat_id": 7, "id": 1048576,
            "edit_date": 0, "content": {"@type": "messageText", "text": {"text": "Source text"}}}
        self.results = [{"@type": "inlineQueryResultArticle", "id": "a", "title": "A", "description": "First"},
                        {"@type": "inlineQueryResultArticle", "id": "b", "title": "B", "description": "Second"}]

    def add_update_handler(self, handler):
        self.handlers.append(handler)

    def remove_update_handler(self, handler):
        self.handlers.remove(handler)

    def get_chat(self, chat_id):
        return {"@type": "chat", "id": chat_id, "title": f"Chat {chat_id}",
                "type": {"@type": "chatTypePrivate", "user_id": chat_id},
                "draft_message": self.draft if chat_id == 42 else None,
                "has_protected_content": self.protected if chat_id == 7 else False}

    def request(self, payload, timeout=None):
        self.calls.append(payload)
        kind = payload["@type"]
        if kind == "searchPublicChat":
            return self.get_chat(314 if payload["username"] == "examplebot" else 42)
        if kind == "getUser":
            return {"@type": "user", "id": 314, "type": {"@type": "userTypeBot", "is_inline": True}}
        if kind == "getInlineQueryResults":
            return {"@type": "inlineQueryResults", "inline_query_id": "9223372036854775807",
                    "results": self.results, "next_offset": "next-native-offset"}
        if kind == "getMessageProperties":
            return {"@type": "messageProperties", "can_be_forwarded": self.forwardable}
        if kind == "getMessage":
            if payload["chat_id"] == 7:
                return copy.deepcopy(self.source)
            return self.sent(pending=False)
        assert kind in {"forwardMessages", "sendInlineQueryResultMessage"}
        if self.behavior == "timeout":
            raise TimeoutError()
        if self.behavior == "reject":
            raise TdlibError({"code": 400, "message": "PRIVATE INPUT SECRET"})
        message = self.sent(pending=self.behavior in {"pending", "early"})
        if self.behavior == "early":
            for handler in self.handlers:
                handler({"@type": "updateMessageSendSucceeded", "old_message_id": 123,
                         "message": self.sent(pending=False)})
        return {"@type": "messages", "total_count": 1, "messages": [message]} if kind == "forwardMessages" else message

    def sent(self, pending=False):
        return {"@type": "message", "chat_id": 42, "id": 123 if pending else 456,
                "is_outgoing": True,
                "sending_state": {"@type": "messageSendingStatePending"} if pending else None}


@pytest.fixture
def environment(tmp_path):
    session = Session()
    box = Outbox(tmp_path / "outbox", session.user_id)
    box.attach(session)
    return session, box, tmp_path / "inline"


def mutations(session):
    return [call for call in session.calls if call["@type"] in {"forwardMessages", "sendInlineQueryResultMessage", "sendMessage"}]


def forward(box, session, **extra):
    return prepare_forward(box, session, draft_id=uuid.uuid4().hex, recipient="42",
                           from_chat_id=7, message_id=1048576, **extra)


def inline(session, box, directory):
    results = get_inline_results(session, directory=directory, recipient="@recipient", bot_username="@examplebot")
    preview = prepare_inline(box, session, directory=directory, draft_id=uuid.uuid4().hex,
                             result_set_id=results["result_set_id"], result_id="b")
    return results, preview


def test_forward_prepares_exact_preview_and_preserves_attribution(environment):
    session, box, _ = environment
    preview = forward(box, session)
    assert preview["status"] == "prepared" and "1048576" in preview["text"] and "Source text" in preview["text"]
    assert not mutations(session)
    assert box.send(session, draft_id=preview["draft_id"])["status"] == "sent"
    assert Outbox(box.directory, session.user_id).send(session, draft_id=preview["draft_id"])["status"] == "sent"
    payload, = mutations(session)
    assert payload["from_chat_id"] == 7 and payload["message_ids"] == [1048576]
    assert payload["chat_id"] == 42 and payload["send_copy"] is False and payload["remove_caption"] is False
    assert payload["options"]["paid_message_star_count"] == 0
    assert payload["options"]["allow_paid_broadcast"] is False


def test_forward_same_id_repeats_original_preview_but_cannot_retarget(environment):
    session, box, _ = environment
    ident = uuid.uuid4().hex
    params = dict(draft_id=ident, recipient="42", from_chat_id=7, message_id=1048576)
    first = prepare_forward(box, session, **params)
    session.source["content"]["text"]["text"] = "Changed"
    assert prepare_forward(box, session, **params) == first
    with pytest.raises(ValueError, match="different content"):
        prepare_forward(box, session, **{**params, "recipient": "43"})
    with pytest.raises(ValueError, match="changed"):
        box.send(session, draft_id=ident)
    assert not mutations(session)


@pytest.mark.parametrize("protected,forwardable", [(True, True), (False, False)])
def test_protected_or_unforwardable_source_rejected(environment, protected, forwardable):
    session, box, _ = environment
    session.protected, session.forwardable = protected, forwardable
    with pytest.raises(ValueError):
        forward(box, session)
    assert not mutations(session)


def test_source_download_progress_does_not_change_fingerprint(environment):
    session, box, _ = environment
    session.source["content"] = {"@type": "messageDocument", "document": {
        "@type": "file", "id": 17, "local": {"downloaded_size": 0}, "remote": {"uploaded_size": 0}}}
    preview = forward(box, session)
    session.source["content"]["document"]["local"]["downloaded_size"] = 100
    assert box.send(session, draft_id=preview["draft_id"])["status"] == "sent"


def test_forward_preview_handles_long_emoji_without_exceeding_utf16_limit(environment):
    session, box, _ = environment
    session.source["content"]["text"]["text"] = "👋" * 4096
    preview = forward(box, session)
    assert len(preview["text"].encode("utf-16-le")) // 2 <= 4096
    assert box.send(session, draft_id=preview["draft_id"])["status"] == "sent"


def test_inline_query_is_bounded_private_and_has_no_location(environment):
    session, _, directory = environment
    session.results *= 20
    for index, item in enumerate(session.results):
        session.results[index] = {**item, "id": str(index)}
    result = get_inline_results(session, directory=directory, recipient="@recipient", bot_username="@examplebot", query="demo")
    InlineResultsResult.model_validate(result)
    assert len(result["items"]) == 20 and result["coverage_limited"] is True
    assert result["next_offset"] == "next-native-offset"
    assert "query_id" not in result and result["bot_user_id"] == 314
    path = directory / (result["result_set_id"] + ".json")
    assert_private_path(path)
    snapshot = json.loads(path.read_text())
    assert snapshot["query_id"] == "9223372036854775807"
    request, = [c for c in session.calls if c["@type"] == "getInlineQueryResults"]
    assert request["user_location"] is None and request["chat_id"] == 42 and request["query"] == "demo"
    assert not mutations(session)


def test_inline_selection_pins_recipient_result_and_sends_once(environment):
    session, box, directory = environment
    results, preview = inline(session, box, directory)
    assert "@examplebot" in preview["text"] and "result b" in preview["text"]
    assert preview["chat_id"] == 42 and not mutations(session)
    assert box.send(session, draft_id=preview["draft_id"])["status"] == "sent"
    assert box.send(session, draft_id=preview["draft_id"])["status"] == "sent"
    payload, = mutations(session)
    assert payload["result_id"] == "b" and payload["query_id"] == "9223372036854775807"
    assert payload["hide_via_bot"] is False and payload["options"]["paid_message_star_count"] == 0


def test_bounded_unicode_inline_page_can_be_loaded_and_prepared(environment):
    session, box, directory = environment
    session.results = [{"@type": "inlineQueryResultArticle", "id": str(index) + "👋" * 1000,
        "title": "👋" * 256, "description": "👋" * 512} for index in range(20)]
    result = get_inline_results(session, directory=directory, recipient="42", bot_username="@examplebot")
    selected = result["items"][0]["result_id"]
    preview = prepare_inline(box, session, directory=directory, draft_id=uuid.uuid4().hex,
                             result_set_id=result["result_set_id"], result_id=selected)
    assert len(preview["text"].encode("utf-16-le")) // 2 <= 4096


@pytest.mark.parametrize("query_id", [True, 1.5, "1.0", "1e3", "01", str(2**63)])
def test_inline_native_query_identity_rejects_coercion(environment, query_id):
    session, _, directory = environment
    original = session.request
    def request(payload, timeout=None):
        response = original(payload, timeout=timeout)
        if payload["@type"] == "getInlineQueryResults":
            response["inline_query_id"] = query_id
        return response
    session.request = request
    with pytest.raises(ValueError, match="invalid inline"):
        get_inline_results(session, directory=directory, recipient="42", bot_username="@examplebot")
    assert not list(directory.glob("*.json"))


def test_inline_unknown_result_and_foreign_account_fail_closed(environment):
    session, box, directory = environment
    result = get_inline_results(session, directory=directory, recipient="42", bot_username="@examplebot")
    params = dict(draft_id=uuid.uuid4().hex, result_set_id=result["result_set_id"], result_id="made-up")
    with pytest.raises(ValueError, match="not returned"):
        prepare_inline(box, session, directory=directory, **params)
    path = directory / (result["result_set_id"] + ".json")
    snapshot = json.loads(path.read_text())
    snapshot["user_id"] = 777
    path.write_text(json.dumps(snapshot))
    with pytest.raises(ValueError, match="different Telegram account"):
        prepare_inline(box, session, directory=directory, **{**params, "result_id": "a"})
    assert not mutations(session)


@pytest.mark.parametrize("stage", ["prepare", "send"])
def test_inline_existing_draft_is_preserved(environment, stage):
    session, box, directory = environment
    result = get_inline_results(session, directory=directory, recipient="42", bot_username="@examplebot")
    params = dict(draft_id=uuid.uuid4().hex, result_set_id=result["result_set_id"], result_id="a")
    if stage == "send":
        prepare_inline(box, session, directory=directory, **params)
    session.draft = {"content": {"text": {"text": "Owner's unsent work"}}}
    with pytest.raises(ValueError, match="draft"):
        if stage == "send":
            box.send(session, draft_id=params["draft_id"])
        else:
            prepare_inline(box, session, directory=directory, **params)
    assert not mutations(session)


def test_inline_expiry_checked_before_prepare_and_dispatch(environment, monkeypatch):
    session, box, directory = environment
    results, preview = inline(session, box, directory)
    monkeypatch.setattr("telegram_search_mcp.relaying.time.time", lambda: results["expires_at"] + 1)
    with pytest.raises(ValueError, match="expired"):
        prepare_inline(box, session, directory=directory, draft_id=uuid.uuid4().hex,
                       result_set_id=results["result_set_id"], result_id="a")
    with pytest.raises(ValueError, match="expired"):
        box.send(session, draft_id=preview["draft_id"])
    assert not mutations(session)


@pytest.mark.parametrize("relay", ["forward", "inline"])
@pytest.mark.parametrize("behavior,expected", [("timeout", "unknown"), ("reject", "failed"), ("early", "sent")])
def test_relay_outcomes_reuse_durable_outbox(environment, relay, behavior, expected):
    session, box, directory = environment
    preview = forward(box, session) if relay == "forward" else inline(session, box, directory)[1]
    session.behavior = behavior
    assert box.send(session, draft_id=preview["draft_id"], wait_seconds=0)["status"] == expected
    restarted = Outbox(box.directory, session.user_id)
    assert restarted.send(session, draft_id=preview["draft_id"], wait_seconds=0)["status"] == expected
    assert len(mutations(session)) == 1
    assert "PRIVATE INPUT" not in json.dumps(box._load(preview["draft_id"]))


def test_dispatch_marker_is_durable_before_native_send(environment):
    session, box, _ = environment
    preview = forward(box, session)
    original = session.request
    def crash(payload, timeout=None):
        if payload["@type"] == "forwardMessages":
            assert box._load(preview["draft_id"])["status"] == "unknown"
            raise KeyboardInterrupt()
        return original(payload, timeout=timeout)
    session.request = crash
    with pytest.raises(KeyboardInterrupt):
        box.send(session, draft_id=preview["draft_id"])
    assert Outbox(box.directory, session.user_id).send(session, draft_id=preview["draft_id"])["status"] == "unknown"
