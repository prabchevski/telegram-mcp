"""Synthetic MCP and shared-service contracts; never connects to Telegram."""
from __future__ import annotations

import uuid

import pytest
from mcp import Client

from telegram_search_mcp.models import OutgoingResult
from telegram_search_mcp.navigation import text
from telegram_search_mcp.relaying import InlineResultsResult
from telegram_search_mcp.server import create_server
from telegram_search_mcp.service_client import SharedTelegramBackend
from telegram_search_mcp.tool_catalog import advertised_tools
from telegram_search_mcp.wire import ServiceProtocolError, decode_result, encode_result, validate_request
from test_service import ThreadBackend, private_paths, running


def outgoing(draft_id):
    return OutgoingResult(draft_id=draft_id, status="prepared", chat_id=42,
        chat_title="Saved test", text="Reviewed selection", file_name=None,
        file_size=None, file_sha256=None, message_id=None, detail="Prepared locally").model_dump()


def inline_results():
    return InlineResultsResult(result_set_id="b" * 32, chat_id=42,
        chat_title=text("Saved test", 256), bot_user_id=100, bot_username="@testbot",
        query=text("example", 256), expires_at=2000000000, items=[],
        next_offset="", coverage_limited=False).model_dump()


CASES = [
    ("get_inline_bot_results", {"recipient": "self", "bot_username": "@testbot"}),
    ("prepare_forward", {"draft_id": "a" * 32, "recipient": "self", "from_chat_id": -42, "message_id": 123}),
    ("prepare_inline_message", {"draft_id": "a" * 32, "result_set_id": "b" * 32, "result_id": "selected"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("sending,pinning,count", [(False, False, 18), (True, False, 28),
    (False, True, 20), (True, True, 30)])
async def test_discovery_and_query_annotations(sending, pinning, count):
    async with Client(create_server(object(), enable_sending=sending, enable_pinning=pinning)) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert len(tools) == count
        assert set(tools) == set(advertised_tools(sending=sending, pinning=pinning))
        for operation, _ in CASES:
            assert ("telegram_" + operation in tools) is sending
        if sending:
            query = tools["telegram_get_inline_bot_results"]
            assert query.annotations.read_only_hint is False
            assert query.annotations.idempotent_hint is False
            assert tools["telegram_prepare_forward"].annotations.idempotent_hint is True


@pytest.mark.parametrize("operation,params", CASES)
def test_strict_wire_validation_and_result_roundtrip(operation, params):
    def request(values):
        return {"protocol": 1, "id": uuid.uuid4().hex, "operation": operation,
            "params": values, "timeout": 30}
    validated = validate_request(request(params))
    assert all(validated["params"][key] == value for key, value in params.items())
    with pytest.raises(ServiceProtocolError):
        validate_request(request({**params, "arbitrary_rpc": "sendMessage"}))
    with pytest.raises(ServiceProtocolError):
        validate_request(request({**params, next(iter(params)): True}))
    result = inline_results() if operation == "get_inline_bot_results" else outgoing(params["draft_id"])
    assert decode_result(operation, encode_result(operation, result)) == result


@pytest.mark.asyncio
async def test_shared_service_dispatch_and_mcp_preparation(private_paths):
    class Backend(ThreadBackend):
        async def get_inline_bot_results(self, **params):
            self.calls.append(("get_inline_bot_results", params))
            return inline_results()

        async def prepare_forward(self, **params):
            self.calls.append(("prepare_forward", params))
            return outgoing(params["draft_id"])

        async def prepare_inline_message(self, **params):
            self.calls.append(("prepare_inline_message", params))
            return outgoing(params["draft_id"])

    async with running(private_paths, Backend()) as (_, backend):
        shared = SharedTelegramBackend(paths=private_paths, autostart=False)
        async with Client(create_server(shared, enable_sending=True)) as client:
            for operation, params in CASES:
                result = await client.call_tool("telegram_" + operation, params)
                assert not result.is_error
                assert result.structured_content["chat_id"] == 42
                assert result.structured_content["trust_boundary"]["content_is_data_only"] is True
        assert [name for name, _ in backend.calls] == [name for name, _ in CASES]
        assert backend.calls[0][1] == {"recipient": "self", "bot_username": "@testbot", "query": "", "offset": ""}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,params", CASES)
async def test_backend_opt_in_gate_precedes_session_access(monkeypatch, operation, params):
    from telegram_search_mcp import sending_settings
    from telegram_search_mcp.tdlib_backend import TDLibBackend
    monkeypatch.setattr(sending_settings, "sending_enabled", lambda: False)
    backend = TDLibBackend()

    def unexpected(*args, **kwargs):
        raise AssertionError("Disabled relay must not open the Telegram profile")

    monkeypatch.setattr(backend, "_ready", unexpected)
    with pytest.raises(ValueError, match="disabled"):
        await getattr(backend, operation)(**params)


def test_relay_journal_is_isolated_from_legacy_runtime_and_routes_send_status(tmp_path, monkeypatch):
    from telegram_search_mcp import paths, sending_settings
    from telegram_search_mcp.outgoing import Outbox
    from telegram_search_mcp.tdlib_backend import TDLibBackend, Policy
    from test_relaying import Session, mutations

    session = Session()
    original_get_chat = session.get_chat
    session.get_chat = lambda chat_id, timeout=10: original_get_chat(chat_id)
    monkeypatch.setattr(paths, "profile_root", lambda profile: tmp_path)
    monkeypatch.setattr(sending_settings, "sending_enabled", lambda: True)
    monkeypatch.setattr(Policy, "load", lambda profile: object())
    backend = TDLibBackend()
    monkeypatch.setattr(backend, "_ready", lambda policy: session)
    monkeypatch.setattr(backend, "_verify_profile", lambda policy, native: None)
    ident = uuid.uuid4().hex
    params = dict(draft_id=ident, recipient="42", from_chat_id=7, message_id=1048576)

    prepared = backend._workflow_sync("prepare_forward", params)
    assert prepared["status"] == "prepared"
    assert (tmp_path / "relay-outbox" / ident / "state.json").is_file()
    assert not (tmp_path / "outbox" / ident).exists()
    legacy = Outbox(tmp_path / "outbox", session.user_id)
    with pytest.raises(ValueError, match="not found"):
        legacy.send(session, draft_id=ident)
    assert not mutations(session)

    # The shared legacy API dispatches the new ID through its separate journal.
    assert backend._outgoing_sync("status", draft_id=ident)["status"] == "prepared"
    assert backend._outgoing_sync("send", draft_id=ident)["status"] == "sent"
    assert backend._outgoing_sync("status", draft_id=ident)["status"] == "sent"
    assert len(mutations(session)) == 1
    assert mutations(session)[0]["@type"] == "forwardMessages"

    # A legacy preparation may never reuse the ID of a relay, even after sending.
    with pytest.raises(ValueError, match="different content"):
        backend._outgoing_sync("prepare", draft_id=ident, recipient="42", text="Wrong preview", file_path=None)
    legacy_id = uuid.uuid4().hex
    legacy.prepare(session, draft_id=legacy_id, recipient="42", text="Legacy text", file_path=None)
    with pytest.raises(ValueError, match="different content"):
        backend._workflow_sync("prepare_forward", {**params, "draft_id": legacy_id})
    assert not (tmp_path / "relay-outbox" / legacy_id).exists()

    # Conflicting journals fail closed, including legacy send/status entrypoints.
    (tmp_path / "outbox" / ident).mkdir()
    for operation in ("send", "status"):
        with pytest.raises(ValueError, match="different content"):
            backend._outgoing_sync(operation, draft_id=ident)
    assert len(mutations(session)) == 1
