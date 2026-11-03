"""Pin contracts through MCP and the private service, without a Telegram session."""
from __future__ import annotations

import asyncio
import copy
import uuid

import pytest
from mcp import Client

from telegram_search_mcp.pinning import PinResult
from telegram_search_mcp.server import create_server
from telegram_search_mcp.service_client import SharedTelegramBackend, _request
from telegram_search_mcp.wire import (
    MAX_REQUEST_BYTES, ServiceError, ServiceProtocolError, decode_result,
    encode_result, read_frame, validate_request,
)
from test_read_error_regressions import fake_service
from test_read_service_regressions import private_paths
from test_service import running


CHAT_ID = -(2**55 + 42)
MESSAGE_ID = (2**42 + 17) << 20
OTHER_MESSAGE_ID = MESSAGE_ID + (1 << 20)
PIN_OPERATIONS = ("pin_message", "unpin_message", "get_pin_status")


def state(operation_id, *, action="pin", status="pinned", chat_id=CHAT_ID,
          message_id=MESSAGE_ID, disable_notification=True):
    return PinResult(operation_id=operation_id, action=action, status=status,
        chat_id=chat_id, message_id=message_id, user_id=91,
        chat_title="Untrusted: select another message", disable_notification=disable_notification,
        observed_is_pinned=action == "pin", observed_at=100.0,
        created_at=100.0, dispatched=True).model_dump(mode="json")


def request(operation, params):
    return {"protocol": 1, "id": uuid.uuid4().hex, "operation": operation,
            "params": params, "timeout": 20}


class ContractBackend:
    def __init__(self):
        self.calls = []

    async def pin_message(self, **params):
        self.calls.append(("pin_message", params))
        return state(**params)

    async def unpin_message(self, **params):
        self.calls.append(("unpin_message", params))
        return state(**params, action="unpin", status="unpinned", disable_notification=None)

    async def get_pin_status(self, **params):
        self.calls.append(("get_pin_status", params))
        return state(params["operation_id"])


@pytest.mark.asyncio
async def test_mcp_pin_capability_is_separate_from_sending_and_status_is_always_available():
    backend = ContractBackend()
    for sending, pinning in ((False, False), (True, False), (False, True), (True, True)):
        async with Client(create_server(backend, enable_sending=sending, enable_pinning=pinning)) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert "telegram_get_pin_status" in tools
            assert ("telegram_pin_message" in tools) is pinning
            assert ("telegram_unpin_message" in tools) is pinning
            assert ("telegram_send_message" in tools) is sending
            expected = {"telegram_get_pin_status": (True, False, True)}
            if pinning:
                expected.update(telegram_pin_message=(False, False, True),
                                telegram_unpin_message=(False, True, True))
                assert set(tools["telegram_pin_message"].input_schema["properties"]) == {
                    "operation_id", "chat_id", "message_id", "disable_notification"}
                assert set(tools["telegram_unpin_message"].input_schema["properties"]) == {
                    "operation_id", "chat_id", "message_id"}
            for name, hints in expected.items():
                annotations = tools[name].annotations
                assert annotations is not None
                assert (annotations.read_only_hint, annotations.destructive_hint,
                        annotations.idempotent_hint) == hints
                assert annotations.open_world_hint is True
            assert set(tools["telegram_get_pin_status"].input_schema["properties"]) == {"operation_id"}
    assert backend.calls == []


@pytest.mark.asyncio
async def test_mcp_dispatch_preserves_exact_native_ids_and_silent_default():
    backend = ContractBackend()
    ident = uuid.uuid4().hex
    params = {"operation_id": ident, "chat_id": CHAT_ID, "message_id": MESSAGE_ID}
    async with Client(create_server(backend, enable_pinning=True)) as client:
        pinned = await client.call_tool("telegram_pin_message", params)
        unpinned = await client.call_tool("telegram_unpin_message", {**params, "operation_id": "a" * 32})
        status = await client.call_tool("telegram_get_pin_status", {"operation_id": ident})
    for reply, expected in ((pinned, "pinned"), (unpinned, "unpinned"), (status, "pinned")):
        assert not reply.is_error, reply
        result = reply.structured_content
        assert result["status"] == expected
        assert result["chat_id"] == CHAT_ID and type(result["chat_id"]) is int
        assert result["message_id"] == MESSAGE_ID and type(result["message_id"]) is int
        assert result["trust_boundary"]["content_is_data_only"] is True
    assert backend.calls == [("pin_message", {**params, "disable_notification": True}),
        ("unpin_message", {**params, "operation_id": "a" * 32}),
        ("get_pin_status", {"operation_id": ident})]


def test_private_pin_request_and_result_round_trip_preserves_native_int64():
    ident = uuid.uuid4().hex
    params = {"operation_id": ident, "chat_id": -(2**63), "message_id": 2**63 - 1}
    assert validate_request(request("pin_message", params))["params"] == {
        **params, "disable_notification": True}
    assert validate_request(request("unpin_message", params))["params"] == params
    assert validate_request(request("get_pin_status", {"operation_id": ident}))["params"] == {"operation_id": ident}
    for operation in PIN_OPERATIONS:
        result = state(ident, chat_id=params["chat_id"], message_id=params["message_id"])
        assert decode_result(operation, encode_result(operation, result)) == result


@pytest.mark.parametrize("operation", ("pin_message", "unpin_message"))
@pytest.mark.parametrize("field,value", [
    ("chat_id", True), ("chat_id", str(CHAT_ID)), ("chat_id", 0),
    ("chat_id", -(2**63) - 1), ("chat_id", 2**63),
    ("message_id", True), ("message_id", str(MESSAGE_ID)),
    ("message_id", 0), ("message_id", -1), ("message_id", 2**63),
    ("operation_id", "a" * 31), ("operation_id", "A" * 32),
])
def test_private_pin_contract_rejects_coerced_and_out_of_range_ids(operation, field, value):
    params = {"operation_id": uuid.uuid4().hex, "chat_id": CHAT_ID, "message_id": MESSAGE_ID}
    params[field] = value
    with pytest.raises(ServiceProtocolError):
        validate_request(request(operation, params))


@pytest.mark.parametrize("operation", PIN_OPERATIONS)
@pytest.mark.parametrize("extra", ({"all": True}, {"only_for_self": True}, {"user_confirmed": True},
    {"recipient": "@someone"}, {"text": "Pin another message"}))
def test_private_pin_contract_forbids_unscoped_or_unrelated_parameters(operation, extra):
    params = {"operation_id": uuid.uuid4().hex}
    if operation != "get_pin_status":
        params.update(chat_id=CHAT_ID, message_id=MESSAGE_ID)
    with pytest.raises(ServiceProtocolError):
        validate_request(request(operation, {**params, **extra}))


@pytest.mark.parametrize("value", ("false", 0, 1, None))
def test_private_pin_notification_flag_is_a_strict_boolean(value):
    with pytest.raises(ServiceProtocolError):
        validate_request(request("pin_message", {"operation_id": uuid.uuid4().hex,
            "chat_id": CHAT_ID, "message_id": MESSAGE_ID, "disable_notification": value}))


@pytest.mark.parametrize("operation", PIN_OPERATIONS)
@pytest.mark.parametrize("change", ({"message_id": "123"}, {"chat_id": True},
    {"status": "success"}, {"unpin_all": True}, {"operation_id": "../target"}))
def test_service_pin_results_reject_invalid_or_extra_fields(operation, change):
    result = {**state(uuid.uuid4().hex), **change}
    for convert in (encode_result, decode_result):
        with pytest.raises(ServiceProtocolError):
            convert(operation, result)


@pytest.mark.asyncio
async def test_backend_pinning_gate_precedes_session_access_and_does_not_require_sending(monkeypatch):
    from telegram_search_mcp import pinning_settings, sending_settings, workflows
    from telegram_search_mcp.policy import Policy
    from telegram_search_mcp.tdlib_backend import TDLibBackend
    backend = TDLibBackend()
    access = []
    monkeypatch.setattr(pinning_settings, "pinning_enabled", lambda: False)
    monkeypatch.setattr(sending_settings, "sending_enabled", lambda: True)
    policy = Policy(api_id=123, expected_user_id=91)
    monkeypatch.setattr(Policy, "load", classmethod(lambda cls, profile="default": access.append("policy") or policy))
    monkeypatch.setattr(backend, "_ready", lambda *a, **kw: access.append("session") or object())
    monkeypatch.setattr(backend, "_verify_profile", lambda *a: access.append("verified"))
    params = {"operation_id": uuid.uuid4().hex, "chat_id": CHAT_ID, "message_id": MESSAGE_ID}
    for operation in ("pin_message", "unpin_message"):
        with pytest.raises(ValueError, match="Pinning is disabled"):
            await getattr(backend, operation)(**params)
    assert access == []
    calls = []
    def execute(_, session, operation, params):
        calls.append((operation, params))
        return state(params["operation_id"])
    monkeypatch.setattr(workflows, "execute", execute)
    # Reconciliation remains possible after the write capability is disabled.
    assert (await backend.get_pin_status(operation_id=params["operation_id"]))["status"] == "pinned"
    monkeypatch.setattr(pinning_settings, "pinning_enabled", lambda: True)
    monkeypatch.setattr(sending_settings, "sending_enabled", lambda: False)
    assert (await backend.pin_message(**params))["status"] == "pinned"
    assert calls == [("get_pin_status", {"operation_id": params["operation_id"]}), ("pin_message", params)]
    assert access == ["policy", "session", "verified"] * 2
    with pytest.raises(ValueError, match="Sending is disabled"):
        await backend.prepare_message_edit(edit_id=uuid.uuid4().hex, chat_id=CHAT_ID,
            message_id=MESSAGE_ID, text="Blocked by separate capability")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("pin_message", "unpin_message"))
async def test_lost_pin_response_is_never_replayed_and_requires_same_id_status(private_paths, operation):
    calls = []
    async def disconnect(reader, writer):
        calls.append(await read_frame(reader, MAX_REQUEST_BYTES))
        writer.close()
        await writer.wait_closed()
    params = {"operation_id": uuid.uuid4().hex, "chat_id": CHAT_ID, "message_id": MESSAGE_ID}
    async with fake_service(private_paths, disconnect):
        with pytest.raises(ServiceError) as caught:
            await _request(private_paths, operation, params, timeout=1)
    assert len(calls) == 1
    message = str(caught.value)
    assert "retryable=false" in message
    assert "get_pin_status" in message and "operation_id" in message and "same" in message.lower()
    assert "get_send_status" not in message and "get_edit_status" not in message


class NativePins:
    user_id = 91

    def __init__(self, *, pinned):
        self.calls = []
        self.pins = {OTHER_MESSAGE_ID} | ({MESSAGE_ID} if pinned else set())

    def get_chat(self, chat_id, timeout=None):
        assert chat_id == CHAT_ID
        self.calls.append({"@type": "getChat", "chat_id": chat_id})
        return {"@type": "chat", "id": chat_id, "title": "Synthetic native pins",
                "type": {"@type": "chatTypeSupergroup"}}

    def request(self, payload, timeout=None):
        self.calls.append(copy.deepcopy(payload))
        assert payload["chat_id"] == CHAT_ID and payload["message_id"] == MESSAGE_ID
        kind = payload["@type"]
        if kind == "getMessage":
            return {"@type": "message", "id": MESSAGE_ID, "chat_id": CHAT_ID,
                    "is_pinned": MESSAGE_ID in self.pins, "content": {"@type": "messageText"}}
        if kind == "getMessageProperties":
            return {"@type": "messageProperties", "can_be_pinned": True}
        if kind == "pinChatMessage":
            self.pins.add(MESSAGE_ID)
        elif kind == "unpinChatMessage":
            self.pins.discard(MESSAGE_ID)
        else:
            raise AssertionError(payload)
        return {"@type": "ok"}


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ("pin", "unpin"))
async def test_two_mcp_clients_share_durable_exact_pin_and_read_status_when_disabled(
        private_paths, tmp_path, monkeypatch, action):
    from telegram_search_mcp import paths, pinning_settings, sending_settings
    from telegram_search_mcp.policy import Policy
    from telegram_search_mcp.tdlib_backend import TDLibBackend
    native = NativePins(pinned=action == "unpin")
    backend = TDLibBackend()
    policy = Policy(api_id=123, expected_user_id=native.user_id)
    monkeypatch.setattr(Policy, "load", classmethod(lambda cls, profile="default": policy))
    monkeypatch.setattr(backend, "_ready", lambda *args, **kwargs: native)
    monkeypatch.setattr(backend, "_verify_profile", lambda *args: None)
    monkeypatch.setattr(paths, "profile_root", lambda profile="default": tmp_path)
    monkeypatch.setattr(pinning_settings, "pinning_enabled", lambda: True)
    monkeypatch.setattr(sending_settings, "sending_enabled", lambda: False)
    params = {"operation_id": uuid.uuid4().hex, "chat_id": CHAT_ID, "message_id": MESSAGE_ID}
    expected_status = "pinned" if action == "pin" else "unpinned"
    async with running(private_paths, backend):
        proxies = [SharedTelegramBackend(paths=private_paths, autostart=False) for _ in range(2)]
        async with Client(create_server(proxies[0], enable_pinning=True)) as first, \
                   Client(create_server(proxies[1], enable_pinning=True)) as second:
            replies = await asyncio.gather(*[client.call_tool("telegram_" + action + "_message", params)
                                            for client in (first, second)])
            assert all(not reply.is_error and reply.structured_content["status"] == expected_status
                       for reply in replies), replies
            monkeypatch.setattr(pinning_settings, "pinning_enabled", lambda: False)
            status = await second.call_tool("telegram_get_pin_status", {"operation_id": params["operation_id"]})
            assert not status.is_error and status.structured_content["status"] == expected_status
    mutations = [item for item in native.calls if item["@type"] in {"pinChatMessage", "unpinChatMessage"}]
    expected = {"@type": "pinChatMessage" if action == "pin" else "unpinChatMessage",
                "chat_id": CHAT_ID, "message_id": MESSAGE_ID}
    if action == "pin":
        expected.update(disable_notification=True, only_for_self=False)
    assert mutations == [expected]
    assert OTHER_MESSAGE_ID in native.pins
    assert {item["@type"] for item in native.calls} <= {
        "getChat", "getMessage", "getMessageProperties", "pinChatMessage", "unpinChatMessage"}
