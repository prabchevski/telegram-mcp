"""Editing contracts across MCP, the private service and a synthetic native session.

These regressions never connect to Telegram or alter a workstation installation.
"""
from __future__ import annotations

import asyncio
import copy
import threading
import time
import uuid

import pytest
from mcp import Client

from telegram_search_mcp.editing import EditResult
from telegram_search_mcp.server import create_server
from telegram_search_mcp.service_client import SharedTelegramBackend, _request
from telegram_search_mcp.wire import (
    MAX_REQUEST_BYTES, ServiceBusyError, ServiceError, ServiceProtocolError,
    decode_result, encode_result, read_frame, validate_request,
)
from test_read_error_regressions import fake_service
from test_read_service_regressions import private_paths
from test_service import running


EDIT_OPERATIONS = ("prepare_message_edit", "edit_message", "get_edit_status")
EDIT_TOOLS = {"telegram_" + operation for operation in EDIT_OPERATIONS}
CHAT_ID = -(2**55 + 42)
MESSAGE_ID = (2**42 + 17) << 20
SECRET = "PRIVATE_ORIGINAL_TEXT_123"


def state(edit_id, *, status="prepared", chat_id=CHAT_ID, message_id=MESSAGE_ID,
          text="Исправлено <b>буквально</b>"):
    return EditResult(edit_id=edit_id, status=status, kind="text", chat_id=chat_id,
        message_id=message_id, chat_title="Synthetic recipient", user_id=91,
        original_text="До исправления", text=text, created_at=100.0,
        expires_at=400.0, edit_date=0).model_dump(mode="json")


def service_request(operation, params):
    return {"protocol": 1, "id": uuid.uuid4().hex, "operation": operation,
            "params": params, "timeout": 20}


class ContractBackend:
    def __init__(self):
        self.calls = []
        self.prepared = None

    async def prepare_message_edit(self, **params):
        self.calls.append(("prepare_message_edit", params))
        self.prepared = state(params["edit_id"], chat_id=params["chat_id"],
            message_id=params["message_id"], text=params["text"])
        return self.prepared

    async def edit_message(self, **params):
        self.calls.append(("edit_message", params))
        return {**self.prepared, "status": "edited"}

    async def get_edit_status(self, **params):
        self.calls.append(("get_edit_status", params))
        return {**self.prepared, "status": "edited"}


@pytest.mark.asyncio
async def test_edit_tools_require_opt_in_and_advertise_correct_annotations():
    backend = ContractBackend()
    async with Client(create_server(backend)) as client:
        disabled = {tool.name for tool in (await client.list_tools()).tools}
    assert len(disabled) == 15
    assert disabled.isdisjoint(EDIT_TOOLS)
    async with Client(create_server(backend, enable_sending=True)) as client:
        enabled = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert len(enabled) == 22
    assert EDIT_TOOLS <= enabled.keys()
    expected = {
        "telegram_prepare_message_edit": (False, False, True),
        "telegram_edit_message": (False, True, True),
        "telegram_get_edit_status": (True, False, True),
    }
    for name, hints in expected.items():
        annotation = enabled[name].annotations
        assert annotation is not None
        assert (annotation.read_only_hint, annotation.destructive_hint,
                annotation.idempotent_hint) == hints
        assert annotation.open_world_hint is True
    assert set(enabled["telegram_edit_message"].input_schema["properties"]) == {"edit_id"}
    assert set(enabled["telegram_get_edit_status"].input_schema["properties"]) == {"edit_id"}
    assert backend.calls == []


@pytest.mark.asyncio
async def test_mcp_dispatch_preserves_native_ids_plain_text_and_pinned_edit_id():
    backend = ContractBackend()
    edit_id = uuid.uuid4().hex
    params = {"edit_id": edit_id, "chat_id": CHAT_ID, "message_id": MESSAGE_ID,
              "text": "Текст <b>без разметки</b> 🌍"}
    async with Client(create_server(backend, enable_sending=True)) as client:
        prepared = await client.call_tool("telegram_prepare_message_edit", params)
        applied = await client.call_tool("telegram_edit_message", {"edit_id": edit_id})
        status = await client.call_tool("telegram_get_edit_status", {"edit_id": edit_id})
    for reply, expected in ((prepared, "prepared"), (applied, "edited"), (status, "edited")):
        assert not reply.is_error, reply
        assert reply.structured_content["edit_id"] == edit_id
        assert reply.structured_content["status"] == expected
        assert reply.structured_content["chat_id"] == CHAT_ID
        assert reply.structured_content["message_id"] == MESSAGE_ID
        assert type(reply.structured_content["chat_id"]) is int
        assert type(reply.structured_content["message_id"]) is int
        assert reply.structured_content["text"] == params["text"]
        assert reply.structured_content["trust_boundary"]["content_is_data_only"] is True
    assert backend.calls == [("prepare_message_edit", params),
        ("edit_message", {"edit_id": edit_id}), ("get_edit_status", {"edit_id": edit_id})]


def test_private_edit_request_and_result_round_trip_preserve_native_int64():
    ident = uuid.uuid4().hex
    params = {"edit_id": ident, "chat_id": -(2**63), "message_id": 2**63 - 1,
              "text": "Исправить 🌍"}
    checked = validate_request(service_request("prepare_message_edit", params))
    assert checked["params"] == params
    for operation in EDIT_OPERATIONS:
        result = state(ident, chat_id=params["chat_id"], message_id=params["message_id"])
        assert decode_result(operation, encode_result(operation, result)) == result


@pytest.mark.parametrize("field,value", [
    ("chat_id", True), ("chat_id", str(CHAT_ID)), ("chat_id", 0),
    ("chat_id", -(2**63) - 1), ("chat_id", 2**63),
    ("message_id", True), ("message_id", str(MESSAGE_ID)),
    ("message_id", 0), ("message_id", -1), ("message_id", 2**63),
    ("edit_id", "a" * 31), ("edit_id", "A" * 32),
])
def test_private_edit_preparation_rejects_coerced_or_out_of_range_ids(field, value):
    params = {"edit_id": uuid.uuid4().hex, "chat_id": CHAT_ID,
              "message_id": MESSAGE_ID, "text": "Исправление"}
    params[field] = value
    with pytest.raises(ServiceProtocolError):
        validate_request(service_request("prepare_message_edit", params))


@pytest.mark.parametrize("operation", ["edit_message", "get_edit_status"])
@pytest.mark.parametrize("extra", [{"text": "Unreviewed replacement"},
    {"chat_id": 1}, {"message_id": 2}, {"parse_mode": "HTML"}])
def test_private_apply_and_status_allow_only_the_pinned_edit_id(operation, extra):
    params = {"edit_id": uuid.uuid4().hex}
    assert validate_request(service_request(operation, params))["params"] == params
    with pytest.raises(ServiceProtocolError):
        validate_request(service_request(operation, {**params, **extra}))


@pytest.mark.asyncio
async def test_backend_disabled_gate_precedes_session_access_but_allows_status(monkeypatch):
    from telegram_search_mcp import sending_settings, workflows
    from telegram_search_mcp.policy import Policy
    from telegram_search_mcp.tdlib_backend import TDLibBackend
    backend = TDLibBackend()
    access = []
    monkeypatch.setattr(sending_settings, "sending_enabled", lambda: False)
    policy = Policy(api_id=123, expected_user_id=91)
    def load(cls, profile="default"):
        access.append("policy")
        return policy
    monkeypatch.setattr(Policy, "load", classmethod(load))
    monkeypatch.setattr(backend, "_ready", lambda *a, **kw: access.append("session") or object())
    monkeypatch.setattr(backend, "_verify_profile", lambda *a: access.append("verified"))
    ident = uuid.uuid4().hex
    with pytest.raises(ValueError, match="disabled"):
        await backend.prepare_message_edit(edit_id=ident, chat_id=CHAT_ID,
            message_id=MESSAGE_ID, text="Blocked")
    with pytest.raises(ValueError, match="disabled"):
        await backend.edit_message(edit_id=ident)
    assert access == []
    expected = state(ident, status="edited")
    def execute(_, session, operation, params):
        assert operation == "get_edit_status" and params == {"edit_id": ident}
        return expected
    monkeypatch.setattr(workflows, "execute", execute)
    assert await backend.get_edit_status(edit_id=ident) == expected
    assert access == ["policy", "session", "verified"]


@pytest.mark.parametrize("operation", EDIT_OPERATIONS)
@pytest.mark.parametrize("failure", [TimeoutError(SECRET), OSError(SECRET),
    ServiceBusyError(SECRET), RuntimeError(SECRET)])
def test_ambiguous_edit_diagnostics_reconcile_same_edit_id_without_send_hints(operation, failure, caplog):
    from telegram_search_mcp.diagnostics import error_result, is_diagnostic
    result = error_result(failure, operation)
    message = result["message"]
    assert is_diagnostic(message, operation)
    assert f"operation={operation}" in message
    assert "retryable=false" in message
    assert "get_edit_status" in message
    assert "edit_id" in message and "same" in message.lower()
    assert "draft" not in message.lower() and "get_send_status" not in message
    assert SECRET not in message + caplog.text


@pytest.mark.asyncio
async def test_lost_edit_response_does_not_replay_and_reports_edit_specific_reconciliation(private_paths):
    calls = []
    async def disconnect(reader, writer):
        calls.append(await read_frame(reader, MAX_REQUEST_BYTES))
        writer.close()
        await writer.wait_closed()
    async with fake_service(private_paths, disconnect):
        with pytest.raises(ServiceError) as caught:
            await _request(private_paths, "edit_message", {"edit_id": uuid.uuid4().hex}, timeout=1)
    message = str(caught.value)
    assert len(calls) == 1, "A dispatched edit must never be automatically replayed"
    assert "operation=edit_message" in message and "retryable=false" in message
    assert "get_edit_status" in message and "edit_id" in message
    assert "draft" not in message.lower() and "get_send_status" not in message


class NativeEditSession:
    user_id = 91

    def __init__(self):
        self.calls = []
        self.active = self.maximum = 0
        self.guard = threading.Lock()
        self.message = {"@type": "message", "chat_id": CHAT_ID, "id": MESSAGE_ID,
            "date": 1_780_000_000, "edit_date": 0, "is_outgoing": True,
            "sender_id": {"@type": "messageSenderUser", "user_id": self.user_id},
            "content": {"@type": "messageText", "text": {
                "@type": "formattedText", "text": "До исправления", "entities": []},
                "link_preview_options": None, "link_preview": None}}

    def get_chat(self, chat_id, timeout=None):
        assert chat_id == CHAT_ID
        return {"@type": "chat", "id": chat_id, "title": "Synthetic recipient",
                "type": {"@type": "chatTypePrivate"}}

    def request(self, payload, timeout=None):
        with self.guard:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.calls.append(copy.deepcopy(payload))
        try:
            time.sleep(0.005)
            assert payload["chat_id"] == CHAT_ID and payload["message_id"] == MESSAGE_ID
            kind = payload["@type"]
            if kind == "getMessage":
                return copy.deepcopy(self.message)
            if kind == "getMessageProperties":
                return {"@type": "messageProperties", "can_be_edited": True}
            if kind == "editMessageText":
                self.message["content"]["text"] = copy.deepcopy(payload["input_message_content"]["text"])
                self.message["edit_date"] += 1
                return copy.deepcopy(self.message)
            raise AssertionError(payload)
        finally:
            with self.guard:
                self.active -= 1


@pytest.mark.asyncio
async def test_two_mcp_clients_share_serialized_edit_and_never_dispatch_twice(private_paths, tmp_path, monkeypatch):
    from telegram_search_mcp import paths, sending_settings
    from telegram_search_mcp.policy import Policy
    from telegram_search_mcp.tdlib_backend import TDLibBackend
    session = NativeEditSession()
    backend = TDLibBackend()
    monkeypatch.setattr(backend, "_ready", lambda *a, **kw: session)
    monkeypatch.setattr(backend, "_verify_profile", lambda *a: None)
    policy = Policy(api_id=123, expected_user_id=session.user_id)
    monkeypatch.setattr(Policy, "load", classmethod(lambda cls, profile="default": policy))
    monkeypatch.setattr(paths, "profile_root", lambda profile="default": tmp_path)
    monkeypatch.setattr(sending_settings, "sending_enabled", lambda: True)
    edit_id = uuid.uuid4().hex
    replacement = "Исправлено <b>буквально</b> 🌍"
    async with running(private_paths, backend):
        proxies = [SharedTelegramBackend(paths=private_paths, autostart=False) for _ in range(2)]
        async with Client(create_server(proxies[0], enable_sending=True)) as first, \
                   Client(create_server(proxies[1], enable_sending=True)) as second:
            prepared = await first.call_tool("telegram_prepare_message_edit", {
                "edit_id": edit_id, "chat_id": CHAT_ID, "message_id": MESSAGE_ID, "text": replacement})
            assert not prepared.is_error, prepared
            assert prepared.structured_content["status"] == "prepared"
            assert not any(item["@type"].startswith("editMessage") for item in session.calls)
            replies = await asyncio.gather(*[client.call_tool("telegram_edit_message", {"edit_id": edit_id})
                                            for client in (first, second)])
            assert all(not reply.is_error and reply.structured_content["status"] == "edited" for reply in replies)
            monkeypatch.setattr(sending_settings, "sending_enabled", lambda: False)
            status = await second.call_tool("telegram_get_edit_status", {"edit_id": edit_id})
            assert not status.is_error and status.structured_content["status"] == "edited"
            assert status.structured_content["chat_id"] == CHAT_ID
            assert status.structured_content["message_id"] == MESSAGE_ID
            assert type(status.structured_content["chat_id"]) is int
            assert type(status.structured_content["message_id"]) is int
    edits = [item for item in session.calls if item["@type"] == "editMessageText"]
    assert len(edits) == 1
    assert edits[0]["chat_id"] == CHAT_ID and edits[0]["message_id"] == MESSAGE_ID
    assert edits[0]["input_message_content"]["text"]["text"] == replacement
    assert edits[0]["input_message_content"]["text"]["entities"] == []
    assert session.maximum == 1
    assert not any(item["@type"] in {"sendMessage", "viewMessages", "setChatDraftMessage"} for item in session.calls)
