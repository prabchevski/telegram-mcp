"""Attachment dispatch regressions, using a synthetic TDLib session only."""
from __future__ import annotations

import json
import os
import uuid

import pytest
from mcp import Client

from telegram_search_mcp.models import OutgoingResult
from telegram_search_mcp.outgoing import Outbox
from telegram_search_mcp.platform_support import open_private_file
from telegram_search_mcp.server import create_server
from telegram_search_mcp.tdjson import TdlibError
from telegram_search_mcp.wire import decode_result, encode_result


CHAT_ID = 42
REPLY_ID = 1 << 40  # Preserve native TDLib IDs; never convert to Telegram UI IDs.
CAPTION = "a" * 600 + "👋"  # 602 UTF-16 code units.
ATTACHMENT_BYTES = b"# Synthetic attachment\n".ljust(29054, b".")


class AttachmentSession:
    user_id = 99

    def __init__(self, behavior="success", error=None):
        self.behavior = behavior
        self.error = error or {"code": 400, "message": "FILE_PART_INVALID"}
        self.calls = []
        self.handlers = []
        self.client = self
        self.poll_failure = False

    def add_update_handler(self, handler):
        self.handlers.append(handler)

    def remove_update_handler(self, handler):
        self.handlers.remove(handler)

    def get_chat(self, chat_id):
        assert chat_id == CHAT_ID
        return {"@type": "chat", "id": CHAT_ID, "title": "Synthetic attachment test",
                "type": {"@type": "chatTypePrivate"}}

    @staticmethod
    def message(state=None, message_id=456):
        return {"@type": "message", "chat_id": CHAT_ID, "id": message_id,
                "is_outgoing": True, "sending_state": state}

    def failure_message(self):
        return self.message({"@type": "messageSendingStateFailed", "error": self.error})

    def failed_event(self):
        return {"@type": "updateMessageSendFailed", "old_message_id": 123,
                "message": self.failure_message(), "error": self.error}

    def request(self, payload, timeout=None):
        self.calls.append(payload)
        operation = payload["@type"]
        if operation == "getMessageProperties":
            assert payload["message_id"] == REPLY_ID
            return {"@type": "messageProperties", "can_be_replied": True}
        if operation == "getMessage":
            if payload["message_id"] == REPLY_ID:
                return self.message(message_id=REPLY_ID)
            assert payload["message_id"] == 123
            if self.poll_failure:
                return self.failure_message()
            return self.message({"@type": "messageSendingStatePending"}, 123)
        assert operation == "sendMessage"
        if self.behavior == "timeout":
            raise TimeoutError("synthetic response loss")
        if self.behavior == "runtime":
            raise RuntimeError("synthetic connection loss")
        if self.behavior == "reject":
            raise TdlibError(self.error)
        if self.behavior == "failed_response":
            return self.failure_message()
        if self.behavior == "early_failure":
            for handler in self.handlers:
                handler(self.failed_event())
        if self.behavior in {"pending", "early_failure"}:
            return self.message({"@type": "messageSendingStatePending"}, 123)
        return self.message()

    def sends(self):
        return [call for call in self.calls if call["@type"] == "sendMessage"]


@pytest.fixture
def attachment(tmp_path):
    source = tmp_path / "synthetic.md"
    # Elevated Windows runners otherwise create Administrators-owned files.
    # Use the same explicit current-user owner/DACL as other portable fixtures.
    with os.fdopen(open_private_file(source, exclusive=True), "wb") as stream:
        stream.write(ATTACHMENT_BYTES)
    assert source.stat().st_size == 29054
    assert len(CAPTION.encode("utf-16-le")) // 2 == 602
    return source


def prepare(tmp_path, attachment, session, reply_id=None):
    box = Outbox(tmp_path / "outbox", session.user_id)
    box.attach(session)
    draft_id = uuid.uuid4().hex
    result = box.prepare(session, draft_id=draft_id, recipient=str(CHAT_ID), text=CAPTION,
                         file_path=str(attachment), reply_to_message_id=reply_id)
    snapshot = box.directory / draft_id / attachment.name
    assert snapshot.read_bytes() == ATTACHMENT_BYTES
    assert not session.sends()
    return box, draft_id, snapshot, result


@pytest.mark.parametrize("reply_id", [None, REPLY_ID])
def test_attachment_caption_reply_and_immutable_snapshot(tmp_path, attachment, reply_id):
    session = AttachmentSession()
    box, draft_id, snapshot, preview = prepare(tmp_path, attachment, session, reply_id)
    attachment.write_bytes(b"Changed original after review")
    assert box.prepare(session, draft_id=draft_id, recipient=str(CHAT_ID), text=CAPTION,
                       file_path=str(attachment), reply_to_message_id=reply_id) == preview
    result = box.send(session, draft_id=draft_id)
    assert result["status"] == "sent"
    payload = session.sends()[0]
    assert payload["chat_id"] == CHAT_ID
    if reply_id is None:
        assert payload["reply_to"] is None
    else:
        assert payload["reply_to"]["@type"] == "inputMessageReplyToMessage"
        assert payload["reply_to"]["message_id"] == REPLY_ID
    content = payload["input_message_content"]
    assert content["@type"] == "inputMessageDocument"
    assert content["document"] == {"@type": "inputDocument",
                                   "document": {"@type": "inputFileLocal", "path": str(snapshot)},
                                   "thumbnail": None, "disable_content_type_detection": True}
    assert "thumbnail" not in content
    assert "disable_content_type_detection" not in content
    assert content["caption"] == {"@type": "formattedText", "text": CAPTION, "entities": []}
    assert not snapshot.exists()
    restarted = Outbox(box.directory, session.user_id)
    assert restarted.send(session, draft_id=draft_id) == result
    assert restarted.status(session, draft_id=draft_id) == result
    assert len(session.sends()) == 1


@pytest.mark.parametrize("behavior", ["timeout", "runtime"])
@pytest.mark.parametrize("reply_id", [None, REPLY_ID])
def test_uncertain_attachment_dispatch_keeps_snapshot_and_never_resends(tmp_path, attachment, behavior, reply_id):
    session = AttachmentSession(behavior)
    box, draft_id, snapshot, _ = prepare(tmp_path, attachment, session, reply_id)
    result = box.send(session, draft_id=draft_id, wait_seconds=0)
    assert result["status"] == "unknown"
    assert result["message_id"] is None
    assert snapshot.read_bytes() == ATTACHMENT_BYTES
    restarted = Outbox(box.directory, session.user_id)
    assert restarted.send(session, draft_id=draft_id, wait_seconds=0) == result
    assert restarted.status(session, draft_id=draft_id) == result
    assert len(session.sends()) == 1


def test_direct_rejection_diagnostics_survive_reload_and_result_models(tmp_path, attachment):
    session = AttachmentSession("reject")
    box, draft_id, snapshot, _ = prepare(tmp_path, attachment, session, REPLY_ID)
    result = box.send(session, draft_id=draft_id, wait_seconds=0)
    assert result["status"] == "failed"
    assert result["message_id"] is None
    assert "TDLib 400: FILE_PART_INVALID" in result["detail"]
    ledger = box._load(draft_id)
    assert ledger["error_code"] == 400
    assert ledger["error_message"] == "FILE_PART_INVALID"
    assert not snapshot.exists()
    restarted = Outbox(box.directory, session.user_id)
    assert restarted.send(session, draft_id=draft_id) == result
    assert restarted.status(session, draft_id=draft_id) == result
    assert len(session.sends()) == 1
    # Public validation and local RPC transport must retain the diagnostics too.
    assert OutgoingResult.model_validate(result).detail == result["detail"]
    assert "error_code" not in result and "error_message" not in result
    assert decode_result("get_send_status", encode_result("get_send_status", result)) == result
    ledger = json.loads((box.directory / draft_id / "state.json").read_text())
    assert ledger["error_code"] == 400
    assert ledger["error_message"] == "FILE_PART_INVALID"


@pytest.mark.parametrize("behavior", ["failed_response", "early_failure", "pending"])
def test_native_failure_diagnostics_for_response_and_updates(tmp_path, attachment, behavior):
    session = AttachmentSession(behavior)
    box, draft_id, snapshot, _ = prepare(tmp_path, attachment, session)
    result = box.send(session, draft_id=draft_id, wait_seconds=0)
    if behavior == "pending":
        assert result["status"] == "pending"
        box._update(session.failed_event())
        result = box.status(session, draft_id=draft_id)
    assert result["status"] == "failed"
    assert "TDLib 400: FILE_PART_INVALID" in result["detail"]
    ledger = box._load(draft_id)
    assert ledger["error_code"] == 400
    assert ledger["error_message"] == "FILE_PART_INVALID"
    assert not snapshot.exists()
    assert Outbox(box.directory, session.user_id).send(session, draft_id=draft_id) == result
    assert len(session.sends()) == 1


def test_status_failure_diagnostics_after_pending_restart(tmp_path, attachment):
    session = AttachmentSession("pending")
    box, draft_id, snapshot, _ = prepare(tmp_path, attachment, session)
    assert box.send(session, draft_id=draft_id, wait_seconds=0)["status"] == "pending"
    restarted = Outbox(box.directory, session.user_id)
    session.poll_failure = True
    result = restarted.status(session, draft_id=draft_id)
    assert result["status"] == "failed"
    assert "TDLib 400: FILE_PART_INVALID" in result["detail"]
    ledger = box._load(draft_id)
    assert ledger["error_code"] == 400
    assert ledger["error_message"] == "FILE_PART_INVALID"
    assert not snapshot.exists()
    assert restarted.send(session, draft_id=draft_id) == result
    assert len(session.sends()) == 1


@pytest.mark.parametrize("behavior", ["reject", "failed_response", "early_failure"])
@pytest.mark.parametrize("sensitive", [
    "secret caption; /private/synthetic/private.md; token=supersecret; +12345678901",
    "SECRET_TOKEN_SUPERSECRET",
    "FILE_PART_INVALID private content should not be reflected",
])
def test_unrecognized_error_prose_is_never_persisted_or_exposed(tmp_path, attachment, behavior, sensitive, caplog):
    session = AttachmentSession(behavior, {"code": 400, "message": sensitive})
    box, draft_id, _, _ = prepare(tmp_path, attachment, session)
    result = box.send(session, draft_id=draft_id, wait_seconds=0)
    assert result["status"] == "failed"
    assert box._load(draft_id)["error_code"] == 400
    assert box._load(draft_id)["error_message"] == "TDLib error details redacted"
    assert sensitive not in json.dumps(result)
    assert sensitive not in (box.directory / draft_id / "state.json").read_text()
    assert sensitive not in caplog.text
    assert all(fragment not in result["detail"] for fragment in
               ["secret caption", "/private/synthetic/", "supersecret", "+12345678901"])


def test_legacy_failed_ledger_without_error_fields_is_readable_and_never_retried(tmp_path, attachment):
    session = AttachmentSession("reject")
    box, draft_id, _, _ = prepare(tmp_path, attachment, session)
    value = box._load(draft_id)
    value.update(status="failed", detail="Telegram rejected the send; no automatic retry")
    value.pop("error_code", None)
    value.pop("error_message", None)
    box._save(value)
    restarted = Outbox(box.directory, session.user_id)
    result = restarted.status(session, draft_id=draft_id)
    assert result["status"] == "failed"
    assert result.get("error_code") is None
    assert result.get("error_message") is None
    assert restarted.send(session, draft_id=draft_id) == result
    assert not session.sends()


@pytest.mark.parametrize("legacy_scalars", [False, True])
def test_failure_update_error_survives_without_error_in_message_state(tmp_path, attachment, legacy_scalars):
    session = AttachmentSession("pending")
    box, draft_id, snapshot, _ = prepare(tmp_path, attachment, session)
    assert box.send(session, draft_id=draft_id, wait_seconds=0)["status"] == "pending"
    event = session.failed_event()
    event["message"]["sending_state"] = {"@type": "messageSendingStateFailed"}
    if legacy_scalars:
        event.pop("error")
        event.update(error_code=400, error_message="FILE_PART_INVALID")
    box._update(event)
    result = box.status(session, draft_id=draft_id)
    assert result["status"] == "failed"
    assert "TDLib 400: FILE_PART_INVALID" in result["detail"]
    assert box._load(draft_id)["error_code"] == 400
    assert box._load(draft_id)["error_message"] == "FILE_PART_INVALID"
    assert not snapshot.exists()
    assert Outbox(box.directory, session.user_id).send(session, draft_id=draft_id) == result
    assert len(session.sends()) == 1


@pytest.mark.parametrize("error_code", [-1, 408, 500, 503, 599])
def test_uncertain_tdlib_rejection_keeps_attachment_and_never_retries(tmp_path, attachment, error_code):
    session = AttachmentSession("reject", {"code": error_code, "message": "synthetic uncertain result"})
    box, draft_id, snapshot, _ = prepare(tmp_path, attachment, session, REPLY_ID)
    result = box.send(session, draft_id=draft_id, wait_seconds=0)
    assert result["status"] == "unknown"
    assert result["message_id"] is None
    assert snapshot.read_bytes() == ATTACHMENT_BYTES
    assert box._load(draft_id)["error_code"] == error_code
    assert box._load(draft_id)["error_message"] == "TDLib error details redacted"
    restarted = Outbox(box.directory, session.user_id)
    assert restarted.send(session, draft_id=draft_id, wait_seconds=0) == result
    assert restarted.status(session, draft_id=draft_id) == result
    assert len(session.sends()) == 1


def test_known_static_inputfile_reason_is_preserved_without_raw_diagnostics(tmp_path, attachment):
    reason = "InputFile is not specified"
    session = AttachmentSession("reject", {"code": 400, "message": reason})
    box, draft_id, snapshot, _ = prepare(tmp_path, attachment, session)
    result = box.send(session, draft_id=draft_id)
    assert result["status"] == "failed"
    assert "TDLib 400: " + reason in result["detail"]
    assert box._load(draft_id)["error_code"] == 400
    assert box._load(draft_id)["error_message"] == reason
    assert OutgoingResult.model_validate(result).detail == result["detail"]
    assert not snapshot.exists()


def test_406_message_is_redacted_even_when_reason_is_allowlisted(tmp_path, attachment, caplog):
    # TDLib reserves 406 for errors whose message must never be processed/shown.
    reason = "FILE_PART_INVALID"
    session = AttachmentSession("reject", {"code": 406, "message": reason})
    box, draft_id, snapshot, _ = prepare(tmp_path, attachment, session)
    result = box.send(session, draft_id=draft_id)
    assert result["status"] == "failed"
    assert "TDLib 406: TDLib error details redacted" in result["detail"]
    ledger = box._load(draft_id)
    assert ledger["error_code"] == 406
    assert ledger["error_message"] == "TDLib error details redacted"
    assert reason not in json.dumps(result)
    assert reason not in (box.directory / draft_id / "state.json").read_text()
    assert reason not in caplog.text
    assert not snapshot.exists()


@pytest.mark.asyncio
async def test_attachment_failure_diagnostic_reaches_mcp_send_and_status(tmp_path, attachment):
    session = AttachmentSession("reject")
    box, draft_id, snapshot, _ = prepare(tmp_path, attachment, session, REPLY_ID)

    class Backend:
        async def send_message(self, **kwargs):
            return box.send(session, **kwargs)

        async def get_send_status(self, **kwargs):
            return box.status(session, **kwargs)

    async with Client(create_server(Backend(), enable_sending=True)) as client:
        sent = await client.call_tool("telegram_send_message", {"draft_id": draft_id})
        status = await client.call_tool("telegram_get_send_status", {"draft_id": draft_id})
        repeated = await client.call_tool("telegram_send_message", {"draft_id": draft_id})
        for response in (sent, status, repeated):
            assert not response.is_error
            result = response.structured_content
            assert result["draft_id"] == draft_id
            assert result["status"] == "failed"
            assert result["message_id"] is None
            assert "TDLib 400: FILE_PART_INVALID" in result["detail"]
            assert result["trust_boundary"]["content_is_data_only"] is True
        assert sent.structured_content == status.structured_content == repeated.structured_content
    assert len(session.sends()) == 1
    assert not snapshot.exists()
