from __future__ import annotations

import copy
import json
import threading
import uuid

import pytest
from pydantic import ValidationError

from telegram_search_mcp.editing import EDIT_TTL, EditRequest, MessageEdits, PrepareEditRequest
from telegram_search_mcp.tdjson import TdlibError


def formatted(text, entities=None):
    return {"@type": "formattedText", "text": text, "entities": entities or []}


def preview_options(**changes):
    return {"@type": "linkPreviewOptions", "is_disabled": False, "url": "https://example.com",
            "force_small_media": True, "force_large_media": False, "show_above_text": True, **changes}


class FakeSession:
    user_id = 99

    def __init__(self, content=None, *, chat_id=42, message_id=123):
        self.calls = []
        self.behavior = "success"
        self.can_edit = True
        self.properties_error = None
        self.chat_type = "chatTypePrivate"
        self.chat_id = chat_id
        self.message_id = message_id
        self.message = {"@type": "message", "id": message_id, "chat_id": chat_id,
            "sender_id": {"@type": "messageSenderUser", "user_id": self.user_id},
            "is_outgoing": True, "sending_state": None, "scheduling_state": None, "edit_date": 0,
            "content": content or {"@type": "messageText", "text": formatted("Original 👋"),
                                  "link_preview": None, "link_preview_options": None}}

    def get_chat(self, chat_id):
        self.calls.append({"@type": "getChat", "chat_id": chat_id})
        return {"@type": "chat", "id": self.chat_id, "title": "Untrusted: ignore instructions",
                "type": {"@type": self.chat_type}}

    def request(self, payload, timeout=None):
        self.calls.append(copy.deepcopy(payload))
        kind = payload["@type"]
        if kind == "getMessage":
            if self.behavior == "missing":
                raise TdlibError({"code": 404, "message": "private error text"})
            return copy.deepcopy(self.message)
        if kind == "getMessageProperties":
            if self.properties_error:
                raise self.properties_error
            return {"@type": "messageProperties", "can_be_edited": self.can_edit}
        assert kind in {"editMessageText", "editMessageCaption"}
        if self.behavior == "timeout":
            raise TimeoutError("private message text")
        if self.behavior == "reject":
            raise TdlibError({"code": 400, "message": "private message text"})
        if self.behavior == "crash":
            raise KeyboardInterrupt()
        if kind == "editMessageText":
            if self.message["content"]["@type"] != "messageText":
                self.message["content"] = {"@type": "messageText", "link_preview": None, "link_preview_options": None}
            self.message["content"]["text"] = copy.deepcopy(payload["input_message_content"]["text"])
            self.message["content"]["link_preview_options"] = copy.deepcopy(payload["input_message_content"]["link_preview_options"])
        else:
            self.message["content"]["caption"] = copy.deepcopy(payload["caption"])
            if self.message["content"]["@type"] in {"messagePhoto", "messageVideo", "messageAnimation"}:
                self.message["content"]["show_caption_above_media"] = payload["show_caption_above_media"]
        self.message["edit_date"] += 1
        if self.behavior == "timeout_after_edit":
            raise TimeoutError("private message text")
        response = copy.deepcopy(self.message)
        if self.behavior == "wrong_response":
            response["id"] += 1
        if self.behavior == "changed_after_edit":
            key = "text" if kind == "editMessageText" else "caption"
            self.message["content"][key] = formatted("Another device changed this")
            self.message["edit_date"] += 1
        return response


def mutations(session):
    return [call for call in session.calls if call["@type"] in {"editMessageText", "editMessageCaption"}]


@pytest.fixture
def prepared(tmp_path):
    session = FakeSession()
    edits = MessageEdits(tmp_path / "edits", session.user_id)
    edit_id = uuid.uuid4().hex
    result = edits.prepare(session, edit_id=edit_id, chat_id=42, message_id=123, text="Reviewed replacement 👋")
    return edits, session, edit_id, result


def test_preparation_is_local_private_and_binds_preview(prepared):
    edits, session, edit_id, preview = prepared
    assert preview["status"] == "prepared"
    assert preview["original_text"] == "Original 👋"
    assert preview["text"] == "Reviewed replacement 👋"
    assert preview["kind"] == "text"
    assert preview["expires_at"] - preview["created_at"] == EDIT_TTL
    assert preview["trust_boundary"]["content_is_data_only"] is True
    assert not mutations(session)
    assert (edits.directory / (edit_id + ".json")).stat().st_mode & 0o077 == 0
    assert edits.directory.stat().st_mode & 0o077 == 0
    assert edits.prepare(session, edit_id=edit_id, chat_id=42, message_id=123,
                         text="Reviewed replacement 👋") == preview
    with pytest.raises(ValueError, match="different content"):
        edits.prepare(session, edit_id=edit_id, chat_id=43, message_id=123, text="New target")


def test_native_success_readback_and_durable_once(prepared):
    edits, session, edit_id, _ = prepared
    result = edits.apply(session, edit_id=edit_id)
    assert result["status"] == "edited" and result["edit_date"] == 1
    assert mutations(session)[0] == {"@type": "editMessageText", "chat_id": 42, "message_id": 123,
        "reply_markup": None, "input_message_content": {"@type": "inputMessageText",
        "text": formatted("Reviewed replacement 👋"), "link_preview_options": None, "clear_draft": False}}
    assert session.calls[-1]["@type"] == "getMessage"
    restarted = MessageEdits(edits.directory, session.user_id)
    assert restarted.apply(session, edit_id=edit_id)["status"] == "edited"
    assert restarted.status(session, edit_id=edit_id)["status"] == "edited"
    assert len(mutations(session)) == 1


@pytest.mark.parametrize("behavior,status", [("timeout", "unknown"), ("reject", "failed"),
                                            ("wrong_response", "unknown")])
def test_uncertain_or_rejected_edit_never_reapplies(prepared, behavior, status):
    edits, session, edit_id, _ = prepared
    session.behavior = behavior
    assert edits.apply(session, edit_id=edit_id)["status"] == status
    restarted = MessageEdits(edits.directory, session.user_id)
    result = restarted.apply(session, edit_id=edit_id)
    assert result["status"] == ("edited" if behavior == "wrong_response" else status)
    assert len(mutations(session)) == 1
    assert "private message text" not in json.dumps(result)


def test_timeout_after_success_reconciles_by_read_only_status(prepared):
    edits, session, edit_id, _ = prepared
    session.behavior = "timeout_after_edit"
    assert edits.apply(session, edit_id=edit_id)["status"] == "unknown"
    restarted = MessageEdits(edits.directory, session.user_id)
    assert restarted.status(session, edit_id=edit_id)["status"] == "edited"
    assert restarted.apply(session, edit_id=edit_id)["status"] == "edited"
    assert len(mutations(session)) == 1


def test_crash_after_dispatch_marker_and_before_native_reply_is_not_retried(prepared):
    edits, session, edit_id, _ = prepared
    session.behavior = "crash"
    with pytest.raises(KeyboardInterrupt):
        edits.apply(session, edit_id=edit_id)
    restarted = MessageEdits(edits.directory, session.user_id)
    session.behavior = "success"
    assert restarted.apply(session, edit_id=edit_id)["status"] == "unknown"
    assert len(mutations(session)) == 1


def test_native_rpc_sees_durable_unknown_marker(prepared):
    edits, session, edit_id, _ = prepared
    original = session.request
    def request(payload, timeout=None):
        if payload["@type"] == "editMessageText":
            recovered = MessageEdits(edits.directory, session.user_id)._load(edit_id)
            assert recovered["status"] == "unknown" and recovered["dispatched"] is True
        return original(payload, timeout=timeout)
    session.request = request
    assert edits.apply(session, edit_id=edit_id)["status"] == "edited"


@pytest.mark.parametrize("change", ["text", "entities", "date", "preview", "kind"])
def test_changed_message_conflicts_before_dispatch(prepared, change):
    edits, session, edit_id, _ = prepared
    if change == "text":
        session.message["content"]["text"]["text"] = "A newer text"
    elif change == "entities":
        session.message["content"]["text"]["entities"] = [{"offset": 0, "length": 1, "type": {"@type": "textEntityTypeBold"}}]
    elif change == "date":
        session.message["edit_date"] = 17
    elif change == "preview":
        session.message["content"]["link_preview_options"] = preview_options()
    else:
        session.message["content"]["@type"] = "messageUnsupported"
    expected = "failed" if change == "kind" else "conflict"
    assert edits.apply(session, edit_id=edit_id)["status"] == expected
    assert not mutations(session)


def test_readback_detects_post_dispatch_change(prepared):
    edits, session, edit_id, _ = prepared
    session.behavior = "changed_after_edit"
    assert edits.apply(session, edit_id=edit_id)["status"] == "conflict"
    assert len(mutations(session)) == 1


def test_plain_identical_text_is_unchanged_but_entities_are_cleared(tmp_path):
    session = FakeSession()
    edits = MessageEdits(tmp_path / "edits", session.user_id)
    edit_id = uuid.uuid4().hex
    edits.prepare(session, edit_id=edit_id, chat_id=42, message_id=123, text="Original 👋")
    assert edits.apply(session, edit_id=edit_id)["status"] == "unchanged"
    assert not mutations(session)
    session.message["content"]["text"]["entities"] = [{"offset": 0, "length": 1, "type": {"@type": "textEntityTypeBold"}}]
    new_id = uuid.uuid4().hex
    edits.prepare(session, edit_id=new_id, chat_id=42, message_id=123, text="Original 👋")
    assert edits.apply(session, edit_id=new_id)["status"] == "edited"
    assert len(mutations(session)) == 1
    assert session.message["content"]["text"]["entities"] == []


@pytest.mark.parametrize("change", ["sender", "sender_chat", "incoming", "pending", "scheduled", "secret", "unsupported", "wrong_id", "wrong_chat"])
def test_nonowned_or_invalid_targets_are_rejected(tmp_path, change):
    session = FakeSession()
    if change == "sender":
        session.message["sender_id"]["user_id"] = 88
    elif change == "sender_chat":
        session.message["sender_id"] = {"@type": "messageSenderChat", "chat_id": 42}
    elif change == "incoming":
        session.message["is_outgoing"] = False
    elif change == "pending":
        session.message["sending_state"] = {"@type": "messageSendingStatePending"}
    elif change == "scheduled":
        session.message["scheduling_state"] = {"@type": "messageSchedulingStateSendAtDate", "send_date": 123}
    elif change == "secret":
        session.chat_type = "chatTypeSecret"
    elif change == "unsupported":
        session.message["content"] = {"@type": "messagePoll"}
    elif change == "wrong_id":
        session.message["id"] = 124
    else:
        session.message["chat_id"] = 43
    edits = MessageEdits(tmp_path / "edits", session.user_id)
    with pytest.raises(ValueError):
        edits.prepare(session, edit_id=uuid.uuid4().hex, chat_id=42, message_id=123, text="Replacement")
    assert not mutations(session)


def test_saved_own_message_with_native_incoming_flag_is_supported(tmp_path):
    session = FakeSession(chat_id=99)
    session.message["is_outgoing"] = False
    edits = MessageEdits(tmp_path / "edits", session.user_id)
    edit_id = uuid.uuid4().hex
    edits.prepare(session, edit_id=edit_id, chat_id=99, message_id=123, text="Replacement")
    assert edits.apply(session, edit_id=edit_id)["status"] == "edited"


def test_capability_is_checked_again_at_apply_and_legacy_fallback_is_explicit(tmp_path, prepared):
    edits, session, edit_id, _ = prepared
    session.can_edit = False
    session.message["can_be_edited"] = True
    assert edits.apply(session, edit_id=edit_id)["status"] == "failed"
    assert not mutations(session)
    legacy = FakeSession()
    legacy.message["can_be_edited"] = True
    legacy.properties_error = TdlibError({"code": 404, "message": "Unknown function getMessageProperties"})
    legacy_edits = MessageEdits(tmp_path / "legacy", legacy.user_id)
    new_id = uuid.uuid4().hex
    assert legacy_edits.prepare(legacy, edit_id=new_id, chat_id=42, message_id=123, text="New")["status"] == "prepared"
    assert legacy_edits.apply(legacy, edit_id=new_id)["status"] == "edited"
    legacy.properties_error = TdlibError({"code": 403, "message": "getMessageProperties not permitted"})
    with pytest.raises(TdlibError):
        legacy_edits.prepare(legacy, edit_id=uuid.uuid4().hex, chat_id=42, message_id=123, text="Another")


def test_link_preview_options_preserved_exactly_and_malformed_settings_rejected(tmp_path):
    session = FakeSession()
    options = preview_options()
    session.message["content"]["link_preview_options"] = options
    edits = MessageEdits(tmp_path / "edits", session.user_id)
    edit_id = uuid.uuid4().hex
    edits.prepare(session, edit_id=edit_id, chat_id=42, message_id=123, text="https://example.com New")
    assert edits.apply(session, edit_id=edit_id)["status"] == "edited"
    assert mutations(session)[0]["input_message_content"]["link_preview_options"] == options
    session.message["content"]["link_preview_options"] = {"@type": "linkPreviewOptions"}
    with pytest.raises(ValueError, match="Incomplete"):
        edits.prepare(session, edit_id=uuid.uuid4().hex, chat_id=42, message_id=123, text="Replacement")


@pytest.mark.parametrize("omitted", [("link_preview",), ("link_preview_options",),
                                    ("link_preview", "link_preview_options")])
def test_native_omitted_null_preview_objects_allow_owned_saved_message_edit(tmp_path, omitted):
    session = FakeSession(chat_id=99)
    session.message["is_outgoing"] = False
    for key in omitted:
        session.message["content"].pop(key)
    original_request = session.request
    def native_json_request(payload, timeout=None):
        response = original_request(payload, timeout=timeout)
        if isinstance(response, dict) and response.get("@type") == "message":
            for key in ("link_preview", "link_preview_options"):
                if response["content"].get(key) is None:
                    response["content"].pop(key, None)
                if session.message["content"].get(key) is None:
                    session.message["content"].pop(key, None)
        return response
    session.request = native_json_request
    edits = MessageEdits(tmp_path / "edits", session.user_id)
    edit_id = uuid.uuid4().hex
    preview = edits.prepare(session, edit_id=edit_id, chat_id=session.user_id,
                            message_id=123, text="Replacement")
    assert preview["status"] == "prepared"
    assert edits.apply(session, edit_id=edit_id)["status"] == "edited"
    assert mutations(session)[0]["input_message_content"]["link_preview_options"] is None
    assert edits.status(session, edit_id=edit_id)["status"] == "edited"


def media_content(kind):
    field, native_type = {
        "messagePhoto": ("photo", "photo"), "messageVideo": ("video", "video"),
        "messageAnimation": ("animation", "animation"), "messageAudio": ("audio", "audio"),
        "messageDocument": ("document", "document"), "messageVoiceNote": ("voice_note", "voiceNote"),
    }[kind]
    content = {"@type": kind, "caption": formatted("Old caption"),
        field: {"@type": native_type, "file": {"@type": "file", "id": 17, "size": 100,
            "local": {"downloaded_size": 0}, "remote": {"unique_id": "stable-media", "uploaded_size": 100}}}}
    if kind in {"messagePhoto", "messageVideo", "messageAnimation"}:
        content.update(show_caption_above_media=True, has_spoiler=True, is_secret=False)
    return content


@pytest.mark.parametrize("kind", ["messagePhoto", "messageVideo", "messageAnimation", "messageAudio", "messageDocument", "messageVoiceNote"])
def test_caption_edits_preserve_media_and_caption_position(tmp_path, kind):
    content = media_content(kind)
    session = FakeSession(content)
    edits = MessageEdits(tmp_path / "edits", session.user_id)
    edit_id = uuid.uuid4().hex
    result = edits.prepare(session, edit_id=edit_id, chat_id=42, message_id=123, text="New caption")
    assert result["kind"] == "caption"
    assert edits.apply(session, edit_id=edit_id)["status"] == "edited"
    assert mutations(session) == [{"@type": "editMessageCaption", "chat_id": 42, "message_id": 123,
        "reply_markup": None, "caption": formatted("New caption"),
        "show_caption_above_media": kind in {"messagePhoto", "messageVideo", "messageAnimation"}}]
    session.message["content"]["caption"] = formatted("Old caption")
    assert session.message["content"] == content


def test_empty_caption_removes_caption_and_missing_position_fails_closed(tmp_path):
    session = FakeSession(media_content("messagePhoto"))
    edits = MessageEdits(tmp_path / "edits", session.user_id)
    edit_id = uuid.uuid4().hex
    edits.prepare(session, edit_id=edit_id, chat_id=42, message_id=123, text="")
    assert edits.apply(session, edit_id=edit_id)["status"] == "edited"
    assert mutations(session)[0]["caption"] == formatted("")
    del session.message["content"]["show_caption_above_media"]
    with pytest.raises(ValueError, match="position"):
        edits.prepare(session, edit_id=uuid.uuid4().hex, chat_id=42, message_id=123, text="Caption")


def test_media_file_transfer_and_voice_listening_do_not_cause_conflicts(tmp_path):
    session = FakeSession(media_content("messageVoiceNote"))
    session.message["content"]["is_listened"] = False
    edits = MessageEdits(tmp_path / "edits", session.user_id)
    edit_id = uuid.uuid4().hex
    edits.prepare(session, edit_id=edit_id, chat_id=42, message_id=123, text="Caption")
    session.message["content"]["voice_note"]["file"]["local"]["downloaded_size"] = 100
    session.message["content"]["is_listened"] = True
    assert edits.apply(session, edit_id=edit_id)["status"] == "edited"


def test_changed_media_identity_causes_conflict(tmp_path):
    session = FakeSession(media_content("messageDocument"))
    edits = MessageEdits(tmp_path / "edits", session.user_id)
    edit_id = uuid.uuid4().hex
    edits.prepare(session, edit_id=edit_id, chat_id=42, message_id=123, text="Caption")
    session.message["content"]["document"]["file"]["id"] = 18
    assert edits.apply(session, edit_id=edit_id)["status"] == "conflict"
    assert not mutations(session)


def test_account_binding_and_expiry_block_dispatch(prepared):
    edits, session, edit_id, _ = prepared
    other = FakeSession()
    other.user_id = 100
    with pytest.raises(ValueError, match="different Telegram account"):
        edits.apply(other, edit_id=edit_id)
    with pytest.raises(ValueError, match="different Telegram account"):
        MessageEdits(edits.directory, 100).apply(session, edit_id=edit_id)
    value = edits._load(edit_id)
    value["expires_at"] = 0
    edits._save(value)
    assert edits.apply(session, edit_id=edit_id)["status"] == "failed"
    assert edits.status(session, edit_id=edit_id)["error"] == "prepared_edit_expired"
    assert not mutations(session)


def test_status_uses_only_native_reads_and_unavailable_read_does_not_resend(prepared):
    edits, session, edit_id, _ = prepared
    assert edits.status(session, edit_id=edit_id)["status"] == "prepared"
    session.behavior = "timeout"
    assert edits.apply(session, edit_id=edit_id)["status"] == "unknown"
    session.behavior = "missing"
    before = len(session.calls)
    assert edits.status(session, edit_id=edit_id)["status"] == "unknown"
    assert all(call["@type"] == "getMessage" for call in session.calls[before:])
    assert edits.apply(session, edit_id=edit_id)["status"] == "unknown"
    assert len(mutations(session)) == 1


def test_repeated_concurrent_apply_dispatches_once(prepared):
    edits, session, edit_id, _ = prepared
    results = []
    threads = [threading.Thread(target=lambda: results.append(edits.apply(session, edit_id=edit_id))) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
        assert not thread.is_alive()
    assert [result["status"] for result in results] == ["edited"] * 4
    assert len(mutations(session)) == 1


@pytest.mark.parametrize("name,value", [("chat_id", 0), ("chat_id", True), ("chat_id", "42"),
    ("chat_id", 2**63), ("chat_id", -(2**63) - 1), ("message_id", 0), ("message_id", False),
    ("message_id", "123"), ("message_id", 2**63), ("text", "a\x00b"),
    ("text", "\ud800"), ("text", "👋" * 2049), ("edit_id", "../unsafe")])
def test_strict_request_validation(name, value):
    params = {"edit_id": uuid.uuid4().hex, "chat_id": 42, "message_id": 123, "text": "Replacement"}
    params[name] = value
    with pytest.raises((ValidationError, ValueError)):
        PrepareEditRequest(**params)


def test_native_large_integer_ids_remain_exact(tmp_path):
    chat_id, message_id = -(2**61) + 12, 2**62 + 71
    session = FakeSession(chat_id=chat_id, message_id=message_id)
    edits = MessageEdits(tmp_path / "edits", session.user_id)
    edit_id = uuid.uuid4().hex
    preview = edits.prepare(session, edit_id=edit_id, chat_id=chat_id, message_id=message_id, text="New")
    assert preview["chat_id"] == chat_id and preview["message_id"] == message_id
    assert edits.apply(session, edit_id=edit_id)["status"] == "edited"
    assert mutations(session)[0]["message_id"] == message_id
    with pytest.raises(ValidationError):
        EditRequest(edit_id=edit_id, text="unreviewed")


def test_utf16_text_and_caption_limits_and_blank_text(tmp_path):
    session = FakeSession()
    edits = MessageEdits(tmp_path / "edits", session.user_id)
    with pytest.raises(ValueError, match="blank"):
        edits.prepare(session, edit_id=uuid.uuid4().hex, chat_id=42, message_id=123, text=" \n")
    session.message["content"] = media_content("messagePhoto")
    with pytest.raises(ValueError, match="1024"):
        edits.prepare(session, edit_id=uuid.uuid4().hex, chat_id=42, message_id=123, text="👋" * 513)
    edit_id = uuid.uuid4().hex
    assert edits.prepare(session, edit_id=edit_id, chat_id=42, message_id=123, text="👋" * 512)["status"] == "prepared"


def test_unsafe_or_corrupted_ledger_never_dispatches(prepared, tmp_path):
    edits, session, edit_id, _ = prepared
    path = edits.directory / (edit_id + ".json")
    backup = tmp_path / "backup.json"
    path.rename(backup)
    path.symlink_to(backup)
    with pytest.raises((ValueError, RuntimeError)):
        edits.apply(session, edit_id=edit_id)
    path.unlink()
    backup.rename(path)
    value = edits._load(edit_id)
    value["spec"]["message_id"] = 456
    edits._save(value)
    with pytest.raises(ValueError, match="Invalid message edit record"):
        edits.apply(session, edit_id=edit_id)
    assert not mutations(session)


def native_normalizer(session, callback):
    original = session.request
    def request(payload, timeout=None):
        response = original(payload, timeout=timeout)
        if payload["@type"] in {"editMessageText", "editMessageCaption"}:
            callback(session.message, payload)
            response = copy.deepcopy(session.message)
        return response
    session.request = request


@pytest.mark.parametrize("kind,token", [
    ("Url", "https://example.com"), ("Mention", "@example"), ("Hashtag", "#note"),
    ("Cashtag", "$AAPL"), ("BotCommand", "/hello"), ("EmailAddress", "name@example.com"),
    ("PhoneNumber", "+351912345678"), ("BankCardNumber", "4242 4242 4242 4242"),
    ("MediaTimestamp", "1:23"),
])
def test_native_automatic_plain_entities_confirm_success(tmp_path, kind, token):
    session = FakeSession()
    edits = MessageEdits(tmp_path / "edits", session.user_id)
    edit_id = uuid.uuid4().hex
    text = "See " + token
    entity_type = {"@type": "textEntityType" + kind}
    if kind == "MediaTimestamp":
        entity_type["media_timestamp"] = 83
    entity = {"@type": "textEntity", "offset": 4, "length": len(token), "type": entity_type}
    native_normalizer(session, lambda message, payload: message["content"]["text"].update(entities=[entity]))
    edits.prepare(session, edit_id=edit_id, chat_id=42, message_id=123, text=text)
    assert edits.apply(session, edit_id=edit_id)["status"] == "edited"
    assert mutations(session)[0]["input_message_content"]["text"]["entities"] == []
    same_id = uuid.uuid4().hex
    edits.prepare(session, edit_id=same_id, chat_id=42, message_id=123, text=text)
    assert edits.apply(session, edit_id=same_id)["status"] == "unchanged"
    assert len(mutations(session)) == 1


@pytest.mark.parametrize("entity", [
    {"offset": 0, "length": 1, "type": {"@type": "textEntityTypeBold"}},
    {"offset": -1, "length": 1, "type": {"@type": "textEntityTypeUrl"}},
    {"offset": 0, "length": 100, "type": {"@type": "textEntityTypeUrl"}},
    {"offset": 0, "length": 1, "type": {"@type": "textEntityTypeMentionName", "user_id": 99}},
])
def test_retained_manual_or_malformed_native_entities_cannot_confirm_plain_edit(tmp_path, entity):
    session = FakeSession()
    edits = MessageEdits(tmp_path / "edits", session.user_id)
    edit_id = uuid.uuid4().hex
    native_normalizer(session, lambda message, payload: message["content"]["text"].update(entities=[entity]))
    edits.prepare(session, edit_id=edit_id, chat_id=42, message_id=123, text="New")
    assert edits.apply(session, edit_id=edit_id)["status"] == "unknown"
    assert edits.status(session, edit_id=edit_id)["status"] == "conflict"
    assert len(mutations(session)) == 1


def test_preview_disabled_only_normalizes_to_null_when_url_removed(tmp_path):
    session = FakeSession()
    session.message["content"]["link_preview_options"] = preview_options(is_disabled=True,
        url="", force_small_media=False, force_large_media=False, show_above_text=False)
    edits = MessageEdits(tmp_path / "edits", session.user_id)
    edit_id = uuid.uuid4().hex
    native_normalizer(session, lambda message, payload: message["content"].update(link_preview_options=None))
    edits.prepare(session, edit_id=edit_id, chat_id=42, message_id=123, text="No URL remains")
    assert edits.apply(session, edit_id=edit_id)["status"] == "edited"
    assert mutations(session)[0]["input_message_content"]["link_preview_options"]["is_disabled"] is True


def animated_emoji(emoji="👍", full_type="stickerFullTypeRegular"):
    return {"@type": "messageAnimatedEmoji", "emoji": emoji, "animated_emoji": {"@type": "animatedEmoji",
        "sticker": {"@type": "sticker", "full_type": {"@type": full_type}}}}


def test_ordinary_animated_emoji_are_editable_text_and_native_emoji_result_is_confirmed(tmp_path):
    session = FakeSession(animated_emoji())
    edits = MessageEdits(tmp_path / "edits", session.user_id)
    first_id = uuid.uuid4().hex
    assert edits.prepare(session, edit_id=first_id, chat_id=42, message_id=123, text="Hello")["original_text"] == "👍"
    assert edits.apply(session, edit_id=first_id)["status"] == "edited"
    new_id = uuid.uuid4().hex
    def normalize(message, payload):
        message["content"] = animated_emoji("😊")
    native_normalizer(session, normalize)
    edits.prepare(session, edit_id=new_id, chat_id=42, message_id=123, text="😊")
    assert edits.apply(session, edit_id=new_id)["status"] == "edited"
    same_id = uuid.uuid4().hex
    edits.prepare(session, edit_id=same_id, chat_id=42, message_id=123, text="😊")
    assert edits.apply(session, edit_id=same_id)["status"] == "unchanged"


def test_custom_animated_emoji_are_not_silently_downgraded(tmp_path):
    session = FakeSession(animated_emoji(full_type="stickerFullTypeCustomEmoji"))
    edits = MessageEdits(tmp_path / "edits", session.user_id)
    with pytest.raises(ValueError, match="ordinary"):
        edits.prepare(session, edit_id=uuid.uuid4().hex, chat_id=42, message_id=123, text="👍")
    assert not mutations(session)


@pytest.mark.parametrize("code", [408, 500, 502, 503])
def test_native_ambiguous_errors_stay_unknown_and_never_retry(prepared, code):
    edits, session, edit_id, _ = prepared
    original = session.request
    def request(payload, timeout=None):
        if payload["@type"] == "editMessageText":
            session.calls.append(copy.deepcopy(payload))
            raise TdlibError({"code": code, "message": "private ambiguity"})
        return original(payload, timeout=timeout)
    session.request = request
    assert edits.apply(session, edit_id=edit_id)["status"] == "unknown"
    assert MessageEdits(edits.directory, session.user_id).apply(session, edit_id=edit_id)["status"] == "unknown"
    assert len(mutations(session)) == 1


def test_not_modified_error_reconciles_desired_state_instead_of_reporting_failure(prepared):
    edits, session, edit_id, preview = prepared
    original = session.request
    def request(payload, timeout=None):
        if payload["@type"] == "editMessageText":
            session.calls.append(copy.deepcopy(payload))
            session.message["content"]["text"] = formatted(preview["text"])
            session.message["edit_date"] += 1
            raise TdlibError({"code": 400, "message": "MESSAGE_NOT_MODIFIED"})
        return original(payload, timeout=timeout)
    session.request = request
    assert edits.apply(session, edit_id=edit_id)["status"] == "edited"
    assert len(mutations(session)) == 1


@pytest.mark.parametrize("field,value", [
    ("self_destruct_type", {"@type": "messageSelfDestructTypeImmediately"}),
    ("self_destruct_in", 60), ("receiver_id", {"@type": "messageSenderUser", "user_id": 88}),
    ("ephemeral_content", {"@type": "ephemeralMessageContentText"}),
])
def test_self_destructing_or_ephemeral_messages_are_rejected(tmp_path, field, value):
    session = FakeSession()
    session.message[field] = value
    edits = MessageEdits(tmp_path / "edits", session.user_id)
    with pytest.raises(ValueError):
        edits.prepare(session, edit_id=uuid.uuid4().hex, chat_id=42, message_id=123, text="New")
    assert not mutations(session)


@pytest.mark.parametrize("text", [" leading", "trailing ", "\nnew", "new\n", "with\ttab", "with\rcarriage",
    "with\x1fcontrol", "with\u2028separator", "with\u202eright", "with\u0333mark", "with\u033fmark",
    "with\u030aring", "with\u200e\u200fmarks"])
def test_replacement_native_cleanup_forms_rejected_before_dispatch(tmp_path, text):
    session = FakeSession()
    edits = MessageEdits(tmp_path / "edits", session.user_id)
    with pytest.raises(ValueError):
        edits.prepare(session, edit_id=uuid.uuid4().hex, chat_id=42, message_id=123, text=text)
    assert not mutations(session)


def test_interior_newlines_and_full_int64_chat_minimum_are_supported(tmp_path):
    chat_id = -(2**63)
    session = FakeSession(chat_id=chat_id)
    edits = MessageEdits(tmp_path / "edits", session.user_id)
    edit_id = uuid.uuid4().hex
    preview = edits.prepare(session, edit_id=edit_id, chat_id=chat_id, message_id=123, text="First\nSecond")
    assert preview["chat_id"] == chat_id
    assert edits.apply(session, edit_id=edit_id)["status"] == "edited"
