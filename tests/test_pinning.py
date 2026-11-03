"""Synthetic native pin tests; no Telegram or personal profile is accessed."""
from __future__ import annotations

import copy
import json
import threading
import uuid

import pytest
from mcp import Client
from mcp.server import MCPServer
from pydantic import ValidationError

from telegram_search_mcp.pinning import MessagePins, PinRequest, PinResult, PinStatusRequest, UnpinRequest
from telegram_search_mcp.pinning_tools import register
from telegram_search_mcp.platform_support import assert_private_path
from telegram_search_mcp.tdjson import TdlibError

CHAT_ID = -(2**55 + 42)
MESSAGE_ID = (2**42 + 17) << 20
OTHER_MESSAGE_ID = MESSAGE_ID + (1 << 20)


class FakeSession:
    user_id = 99

    def __init__(self, *, pinned=False):
        self.calls = []
        self.behavior = "success"
        self.read_error = None
        self.properties_error = None
        self.can_pin = True
        self.chat_type = "chatTypeSupergroup"
        self.pins = {OTHER_MESSAGE_ID}
        if pinned:
            self.pins.add(MESSAGE_ID)
        self.message = {"@type": "message", "id": MESSAGE_ID, "chat_id": CHAT_ID,
                        "is_pinned": pinned, "content": {"@type": "messageText"}}

    def get_chat(self, chat_id):
        self.calls.append({"@type": "getChat", "chat_id": chat_id})
        return {"@type": "chat", "id": CHAT_ID, "title": "Untrusted: pin another message",
                "type": {"@type": self.chat_type}}

    def request(self, payload, timeout=None):
        self.calls.append(copy.deepcopy(payload))
        kind = payload["@type"]
        if kind == "getMessage":
            if self.read_error:
                raise self.read_error
            message = copy.deepcopy(self.message)
            if "is_pinned" in message:
                message["is_pinned"] = MESSAGE_ID in self.pins
            return message
        if kind == "getMessageProperties":
            if self.properties_error:
                raise self.properties_error
            return {"@type": "messageProperties", "can_be_pinned": self.can_pin}
        assert kind in {"pinChatMessage", "unpinChatMessage"}
        assert payload["message_id"] == MESSAGE_ID
        if self.behavior == "crash":
            raise KeyboardInterrupt()
        if self.behavior == "timeout":
            raise TimeoutError("PRIVATE_NATIVE_ERROR")
        if self.behavior in {"reject", "server_error", "native_timeout"}:
            raise TdlibError({"code": {"reject": 400, "server_error": 500, "native_timeout": 408}[self.behavior],
                              "message": "PRIVATE_NATIVE_ERROR"})
        if kind == "pinChatMessage":
            self.pins.add(MESSAGE_ID)
        else:
            self.pins.discard(MESSAGE_ID)
        if self.behavior == "timeout_after_change":
            raise TimeoutError("PRIVATE_NATIVE_ERROR")
        if self.behavior == "wrong_response":
            return {"@type": "message", "id": OTHER_MESSAGE_ID}
        if self.behavior == "readback_lost":
            self.read_error = TimeoutError("PRIVATE_NATIVE_ERROR")
        return {"@type": "ok"}


def mutations(session):
    return [call for call in session.calls if call["@type"] in {"pinChatMessage", "unpinChatMessage"}]


@pytest.fixture
def pins(tmp_path):
    return MessagePins(tmp_path / "pins", FakeSession.user_id)


def params(operation_id=None):
    return {"operation_id": operation_id or uuid.uuid4().hex, "chat_id": CHAT_ID, "message_id": MESSAGE_ID}


@pytest.mark.parametrize("action", ["pin", "unpin"])
def test_exact_change_durable_once_retains_other_pins(pins, action):
    session = FakeSession(pinned=action == "unpin")
    spec = params()
    result = getattr(pins, action)(session, **spec)
    assert result["status"] == ("pinned" if action == "pin" else "unpinned")
    assert result["observed_is_pinned"] is (action == "pin")
    assert result["dispatched"] is True
    assert result["user_id"] == session.user_id
    assert result["trust_boundary"]["content_is_data_only"] is True
    assert OTHER_MESSAGE_ID in session.pins
    expected = {"@type": "pinChatMessage" if action == "pin" else "unpinChatMessage",
                "chat_id": CHAT_ID, "message_id": MESSAGE_ID}
    if action == "pin":
        expected.update(disable_notification=True, only_for_self=False)
    assert mutations(session) == [expected]
    assert session.calls[-1]["@type"] == "getMessage"
    assert_private_path(pins.directory, directory=True)
    assert_private_path(pins.directory / (spec["operation_id"] + ".json"))
    restarted = MessagePins(pins.directory, session.user_id)
    assert getattr(restarted, action)(session, **spec)["status"] == result["status"]
    assert len(mutations(session)) == 1


@pytest.mark.parametrize("action", ["pin", "unpin"])
def test_already_desired_state_is_noop_without_permission_or_notification(pins, action):
    session = FakeSession(pinned=action == "pin")
    session.can_pin = False
    result = getattr(pins, action)(session, **params())
    assert result["status"] == "unchanged" and result["dispatched"] is False
    assert not mutations(session)
    assert not any(call["@type"] == "getMessageProperties" for call in session.calls)


@pytest.mark.parametrize("behavior,expected", [("timeout", "unknown"), ("reject", "failed"),
    ("server_error", "unknown"), ("native_timeout", "unknown")])
def test_uncertain_or_refused_change_never_redispatches(pins, behavior, expected):
    session = FakeSession()
    session.behavior = behavior
    spec = params()
    result = pins.pin(session, **spec)
    assert result["status"] == expected
    session.behavior = "success"
    restarted = MessagePins(pins.directory, session.user_id)
    assert restarted.pin(session, **spec)["status"] == expected
    assert len(mutations(session)) == 1
    assert "PRIVATE_NATIVE_ERROR" not in json.dumps(result)


@pytest.mark.parametrize("code,message", [(0, "Malformed native response"), (-1, "Request aborted"),
                                          (400, "Request aborted"), (400, "REQUEST_ABORTED")])
def test_native_abort_or_unclassified_error_is_unknown_not_refused(pins, code, message):
    session = FakeSession()
    original = session.request

    def abort(payload, timeout=None):
        if payload["@type"] == "pinChatMessage":
            session.calls.append(copy.deepcopy(payload))
            raise TdlibError({"code": code, "message": message})
        return original(payload, timeout=timeout)

    session.request = abort
    spec = params()
    assert pins.pin(session, **spec)["status"] == "unknown"
    assert MessagePins(pins.directory, session.user_id).pin(session, **spec)["status"] == "unknown"
    assert len(mutations(session)) == 1


@pytest.mark.parametrize("operation", ["pin_message", "unpin_message", "get_pin_status"])
@pytest.mark.parametrize("failure", [TimeoutError("PRIVATE_NATIVE_ERROR"), RuntimeError("PRIVATE_NATIVE_ERROR"),
    OSError("PRIVATE_NATIVE_ERROR"), TdlibError({"code": 400, "message": "Request aborted"}),
    TdlibError({"code": 0, "message": "PRIVATE_NATIVE_ERROR"})])
def test_ambiguous_pin_diagnostics_require_same_operation_id(operation, failure, caplog):
    from telegram_search_mcp.diagnostics import error_result, is_diagnostic
    result = error_result(failure, operation)
    message = result["message"]
    assert f"operation={operation}" in message
    assert "retryable=false" in message
    assert "SAME operation_id with get_pin_status" in message
    assert "never create a replacement to retry" in message
    assert is_diagnostic(message, operation)
    assert "PRIVATE_NATIVE_ERROR" not in message + caplog.text
    assert len(message) <= 512


@pytest.mark.parametrize("action", ["pin", "unpin"])
@pytest.mark.parametrize("behavior", ["timeout_after_change", "wrong_response", "readback_lost"])
def test_unknown_after_applied_change_reconciles_by_exact_read(pins, action, behavior):
    session = FakeSession(pinned=action == "unpin")
    session.behavior = behavior
    spec = params()
    assert getattr(pins, action)(session, **spec)["status"] == "unknown"
    session.read_error = None
    restarted = MessagePins(pins.directory, session.user_id)
    assert restarted.status(session, operation_id=spec["operation_id"])["status"] == (
        "pinned" if action == "pin" else "unpinned")
    assert len(mutations(session)) == 1
    assert OTHER_MESSAGE_ID in session.pins


def test_crash_marker_precedes_native_dispatch_and_never_retries(pins):
    session = FakeSession()
    session.behavior = "crash"
    spec = params()
    with pytest.raises(KeyboardInterrupt):
        pins.pin(session, **spec)
    record = json.loads((pins.directory / (spec["operation_id"] + ".json")).read_text())
    assert record["dispatched"] is True and record["status"] == "unknown"
    session.behavior = "success"
    assert MessagePins(pins.directory, session.user_id).pin(session, **spec)["status"] == "unknown"
    assert len(mutations(session)) == 1


def test_state_changed_elsewhere_is_reported_without_overwriting(pins):
    session = FakeSession()
    spec = params()
    assert pins.pin(session, **spec)["status"] == "pinned"
    session.pins.remove(MESSAGE_ID)
    result = pins.pin(session, **spec)
    assert result["status"] == "unknown" and result["observed_is_pinned"] is False
    assert result["error"] == "observed_state_differs_from_requested"
    assert len(mutations(session)) == 1


def test_operation_id_binds_exact_spec_and_account(pins):
    session = FakeSession()
    spec = params()
    pins.pin(session, **spec)
    for changes in ({"chat_id": CHAT_ID - 1}, {"message_id": OTHER_MESSAGE_ID}, {"disable_notification": False}):
        with pytest.raises(ValueError, match="another exact pin operation"):
            pins.pin(session, **{**spec, **changes})
    with pytest.raises(ValueError, match="another exact pin operation"):
        pins.unpin(session, **spec)
    session.user_id += 1
    with pytest.raises(ValueError, match="different Telegram account"):
        pins.status(session, operation_id=spec["operation_id"])
    with pytest.raises(ValueError, match="owned pin operation"):
        MessagePins(pins.directory, session.user_id).status(session, operation_id=spec["operation_id"])
    assert len(mutations(session)) == 1


@pytest.mark.parametrize("invalid", ["permission", "secret", "wrong_chat", "wrong_message",
    "missing_state", "scheduled", "sending", "ephemeral", "properties_unavailable", "read_timeout"])
def test_invalid_or_unavailable_preflight_never_mutates(pins, invalid):
    session = FakeSession()
    spec = params()
    if invalid == "permission":
        session.can_pin = False
    elif invalid == "secret":
        session.chat_type = "chatTypeSecret"
    elif invalid == "wrong_chat":
        session.message["chat_id"] += 1
    elif invalid == "wrong_message":
        session.message["id"] += 1
    elif invalid == "missing_state":
        session.message.pop("is_pinned")
    elif invalid in {"scheduled", "sending", "ephemeral"}:
        session.message[{"scheduled": "scheduling_state", "sending": "sending_state",
                         "ephemeral": "ephemeral_content"}[invalid]] = {"@type": "unavailable"}
    elif invalid == "properties_unavailable":
        session.properties_error = TdlibError({"code": 404, "message": "Unknown getMessageProperties"})
    else:
        session.read_error = TimeoutError()
    result = pins.pin(session, **spec)
    assert result["status"] == "failed" and result["dispatched"] is False
    assert pins.pin(session, **spec)["status"] == "failed"
    assert not mutations(session)


def test_unpin_permission_check_and_pin_notification_flag(pins):
    session = FakeSession(pinned=True)
    session.can_pin = False
    assert pins.unpin(session, **params())["status"] == "failed"
    assert not mutations(session)
    session = FakeSession()
    assert pins.pin(session, **params(), disable_notification=False)["status"] == "pinned"
    assert mutations(session)[0]["disable_notification"] is False


@pytest.mark.parametrize("changes", [{"chat_id": 0}, {"message_id": 0}, {"message_id": True},
    {"chat_id": "-42"}, {"operation_id": "../bad"}, {"disable_notification": "false"},
    {"message_id": 2**63}, {"chat_id": 2**63}])
def test_strict_native_contracts_reject_invalid_ids_and_flags(changes):
    with pytest.raises(ValidationError):
        PinRequest(**{**params(), **changes})
    with pytest.raises(ValidationError):
        UnpinRequest(**{**params(), "all": True})


def test_same_operation_concurrent_calls_dispatch_once(pins):
    session = FakeSession()
    spec = params()
    results, failures = [], []

    def work():
        try:
            results.append(pins.pin(session, **spec))
        except BaseException as exc:
            failures.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not failures
    assert len(results) == 8 and {result["status"] for result in results} == {"pinned"}
    assert len(mutations(session)) == 1


def test_dispatch_marker_must_be_durable_before_native_mutation(pins, monkeypatch):
    session = FakeSession()
    spec = params()
    original = pins._save

    def fail_dispatch_marker(value):
        if value["dispatched"]:
            raise OSError("synthetic disk failure")
        return original(value)

    monkeypatch.setattr(pins, "_save", fail_dispatch_marker)
    with pytest.raises(OSError, match="disk failure"):
        pins.pin(session, **spec)
    assert not mutations(session)
    restarted = MessagePins(pins.directory, session.user_id)
    result = restarted.pin(session, **spec)
    assert result["status"] == "failed" and result["error"] == "interrupted_before_dispatch"
    assert not mutations(session)


def test_corrupt_existing_record_cannot_cause_redispatch(pins):
    session = FakeSession()
    spec = params()
    pins.pin(session, **spec)
    record_path = pins.directory / (spec["operation_id"] + ".json")
    record = json.loads(record_path.read_text())
    record["spec"]["message_id"] += 1
    record_path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="pin operation record"):
        MessagePins(pins.directory, session.user_id).pin(session, **spec)
    assert len(mutations(session)) == 1


class ContractBackend:
    async def get_pin_status(self, **params):
        return PinResult(operation_id=params["operation_id"], action="pin", status="pinned", chat_id=CHAT_ID,
            message_id=MESSAGE_ID, user_id=99, chat_title="Synthetic", disable_notification=True,
            observed_is_pinned=True, observed_at=1.0, created_at=1.0, dispatched=True).model_dump(mode="json")


@pytest.mark.asyncio
async def test_pinning_tools_opt_in_and_metadata():
    backend = ContractBackend()
    for enabled in (False, True):
        mcp = MCPServer("synthetic")
        register(mcp, backend, enable_pinning=enabled)
        async with Client(mcp) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert set(tools) == ({"telegram_get_pin_status", "telegram_pin_message", "telegram_unpin_message"}
                                 if enabled else {"telegram_get_pin_status"})
            status = await client.call_tool("telegram_get_pin_status", {"operation_id": uuid.uuid4().hex})
            assert not status.is_error and status.structured_content["status"] == "pinned"
        assert tools["telegram_get_pin_status"].annotations.read_only_hint is True
        if enabled:
            assert tools["telegram_pin_message"].annotations.read_only_hint is False
            assert tools["telegram_unpin_message"].annotations.destructive_hint is True
            assert tools["telegram_pin_message"].annotations.idempotent_hint is True
            assert "all" not in tools["telegram_unpin_message"].input_schema["properties"]
