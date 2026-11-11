"""Acceptance-runner validation with synthetic messages, never a live profile."""
import importlib.util
import json
from pathlib import Path
import copy
import sys
from types import SimpleNamespace

import pytest


spec = importlib.util.spec_from_file_location(
    "verify_live_release", Path(__file__).parents[1] / "scripts" / "verify-live-release.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class SyntheticClient:
    def __init__(self, *, inconsistent=False, duplicates=False):
        self.rows = [{"chat_id": -42, "message_id": (2**30 + i) << 20,
                      "content_type": "messageText", "text": {"value": "private sentinel"},
                      "chat_title": {"value": "private title"}}
                     for i in range(45, 0, -1)]
        self.calls = []
        self.inconsistent, self.duplicates = inconsistent, duplicates

    async def call_tool(self, tool, params):
        self.calls.append((tool, params))
        assert tool in {"telegram_get_chat_history", "telegram_search_chat_messages", "telegram_get_context"}
        if tool == "telegram_get_context":
            index = next(i for i, row in enumerate(self.rows) if row["message_id"] == params["message_id"])
            result = {"messages": list(reversed(self.rows[max(0, index - 2):index + 3]))}
        else:
            start, limit = int(params.get("cursor") or 0), params["limit"]
            if self.inconsistent and limit == 1:
                start += 1
            if self.duplicates and start:
                start -= 1
            end = start + limit
            result = {"items": self.rows[start:end], "next_cursor": str(end) if end < len(self.rows) else None}
        return SimpleNamespace(is_error=False, structured_content=result)


@pytest.mark.asyncio
async def test_acceptance_checks_native_ids_sizes_and_context_without_reporting_content():
    client = SyntheticClient()
    report = await runner.read_checks(client, -42, "needle", 5)
    assert report["history"]["page_counts"] == [20, 20, 5]
    assert report["history"]["page_size_invariant"]
    assert report["search"]["messages"] == 45
    assert report["context"]["anchor_present"]
    assert "private" not in json.dumps(report)
    assert "message_id" not in json.dumps(report)
    bounds = {params["date_to"] for tool, params in client.calls if tool != "telegram_get_context"}
    assert len(bounds) == 1
    assert sum(params.get("limit") == 1 for _, params in client.calls) == 20


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,match", [("duplicates", "duplicate"), ("inconsistent", "page size")])
async def test_acceptance_rejects_duplicate_pages_and_page_size_changes(mode, match):
    with pytest.raises(runner.AcceptanceError, match=match):
        await runner.read_checks(SyntheticClient(**{mode: True}), -42, None, 5)


@pytest.mark.asyncio
async def test_acceptance_rejects_repeated_cursor_instead_of_claiming_completion():
    class LoopClient:
        async def call_tool(self, *_):
            return SimpleNamespace(is_error=False, structured_content={"items": [], "next_cursor": "same"})
    with pytest.raises(runner.AcceptanceError, match="cursor"):
        await runner.pages(LoopClient(), "telegram_get_chat_history", {"chat_id": -42, "limit": 20}, 5)


@pytest.mark.asyncio
async def test_acceptance_rejects_old_shared_service_without_activating_or_connecting(monkeypatch):
    import telegram_search_mcp.service_client as service_client
    async def stale_status(*, connect):
        assert connect is False
        return {"running": True, "version": "0.8.1", "stopping": False}
    monkeypatch.setattr(service_client, "service_status", stale_status)
    with pytest.raises(runner.AcceptanceError, match="service version"):
        await runner.check_service("0.9.3", optional=True)


OPERATION_ID = "0123456789abcdef" * 2
OLD_CHAT_ID, NEW_CHAT_ID = -42, -1001234567


class UpgradeStatusClient:
    def __init__(self):
        self.calls = []
        self.result = {"operation_id": OPERATION_ID, "chat_id": OLD_CHAT_ID,
            "new_chat_id": NEW_CHAT_ID, "basic_group_id": 42, "new_supergroup_id": 1234567,
            "status": "upgraded", "dispatched": True, "error": None,
            "chat_title": "private title sentinel", "user_id": 123}

    async def call_tool(self, tool, params):
        self.calls.append((tool, params))
        assert tool == "telegram_get_group_upgrade_status"
        assert params == {"operation_id": OPERATION_ID}
        return SimpleNamespace(is_error=False, structured_content=copy.deepcopy(self.result))


@pytest.mark.asyncio
async def test_upgrade_scope_reads_only_one_existing_status_and_reports_no_ids_or_content():
    client = UpgradeStatusClient()
    report = await runner.group_upgrade_checks(client, OLD_CHAT_ID, OPERATION_ID, NEW_CHAT_ID)
    assert report == {"acceptance_scope": "existing_group_upgrade_status", "migration_status": "passed"}
    assert client.calls == [("telegram_get_group_upgrade_status", {"operation_id": OPERATION_ID})]
    rendered = json.dumps(report)
    assert "private" not in rendered and "chat_id" not in rendered and OPERATION_ID not in rendered
    assert str(OLD_CHAT_ID) not in rendered and str(NEW_CHAT_ID) not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize("key,value", [
    ("operation_id", "b" * 32), ("operation_id", None),
    ("chat_id", -43), ("chat_id", True), ("chat_id", 0), ("chat_id", str(OLD_CHAT_ID)),
    ("new_chat_id", -1007654321), ("new_chat_id", True), ("new_chat_id", 0),
    ("new_chat_id", OLD_CHAT_ID), ("new_chat_id", str(NEW_CHAT_ID)),
    ("basic_group_id", True), ("basic_group_id", 0), ("basic_group_id", -42),
    ("basic_group_id", 2**53), ("basic_group_id", "42"),
    ("new_supergroup_id", True), ("new_supergroup_id", 0), ("new_supergroup_id", -42),
    ("new_supergroup_id", 2**53), ("new_supergroup_id", "1234567"),
    ("status", "prepared"), ("status", "already_upgraded"), ("status", "unknown"),
    ("status", "failed"), ("dispatched", False), ("dispatched", 1),
    ("dispatched", "true"), ("error", "native_result_unknown"),
])
async def test_upgrade_scope_rejects_wrong_target_unconfirmed_or_invalid_native_identity(key, value):
    client = UpgradeStatusClient()
    client.result[key] = value
    with pytest.raises(runner.AcceptanceError):
        await runner.group_upgrade_checks(client, OLD_CHAT_ID, OPERATION_ID, NEW_CHAT_ID)
    assert len(client.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["error", "unstructured"])
async def test_upgrade_scope_does_not_accept_tool_error_or_unstructured_status(kind):
    class InvalidClient:
        async def call_tool(self, tool, params):
            assert tool == "telegram_get_group_upgrade_status"
            return SimpleNamespace(is_error=kind == "error", structured_content=None)
    with pytest.raises(runner.AcceptanceError):
        await runner.group_upgrade_checks(InvalidClient(), OLD_CHAT_ID, OPERATION_ID, NEW_CHAT_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize("old,operation,new", [
    (True, OPERATION_ID, NEW_CHAT_ID), (OLD_CHAT_ID, OPERATION_ID, True),
    (0, OPERATION_ID, NEW_CHAT_ID), (OLD_CHAT_ID, OPERATION_ID, 0),
    (OLD_CHAT_ID, OPERATION_ID, OLD_CHAT_ID),
    (-(2**53), OPERATION_ID, NEW_CHAT_ID), (OLD_CHAT_ID, OPERATION_ID, 2**53),
    (OLD_CHAT_ID, "A" * 32, NEW_CHAT_ID), (OLD_CHAT_ID, "a" * 31, NEW_CHAT_ID),
    (OLD_CHAT_ID, True, NEW_CHAT_ID), (OLD_CHAT_ID, None, NEW_CHAT_ID), (OLD_CHAT_ID, None, None),
])
async def test_invalid_direct_upgrade_arguments_fail_before_any_read(old, operation, new):
    client = UpgradeStatusClient()
    with pytest.raises(runner.AcceptanceError):
        await runner.group_upgrade_checks(client, old, operation, new)
    assert client.calls == []


@pytest.mark.parametrize("flags", [
    ["--group-upgrade-operation-id", OPERATION_ID],
    ["--expected-new-chat-id", str(NEW_CHAT_ID)],
    ["--group-upgrade-operation-id", "A" * 32, "--expected-new-chat-id", str(NEW_CHAT_ID)],
    ["--group-upgrade-operation-id", "a" * 31, "--expected-new-chat-id", str(NEW_CHAT_ID)],
    ["--group-upgrade-operation-id", OPERATION_ID, "--expected-new-chat-id", "false"],
    ["--group-upgrade-operation-id", OPERATION_ID, "--expected-new-chat-id", "0"],
    ["--group-upgrade-operation-id", OPERATION_ID, "--expected-new-chat-id", str(OLD_CHAT_ID)],
    ["--group-upgrade-operation-id", OPERATION_ID, "--expected-new-chat-id", str(2**53)],
    ["--group-upgrade-operation-id", OPERATION_ID, "--expected-new-chat-id", str(NEW_CHAT_ID), "--query", "needle"],
])
def test_malformed_upgrade_cli_flags_rejected_before_verification(monkeypatch, tmp_path, flags):
    def forbidden_verification(*_):
        pytest.fail("Invalid CLI flags must not contact the installed service or Telegram")
    monkeypatch.setattr(runner, "verify", forbidden_verification)
    monkeypatch.setattr(sys, "argv", ["verify-live-release.py", "--install-root", str(tmp_path.resolve()),
        "--version", "0.14.0", "--revision", "a" * 40, "--chat-id", str(OLD_CHAT_ID), *flags])
    with pytest.raises(SystemExit) as raised:
        runner.main()
    assert raised.value.code == 2


@pytest.mark.asyncio
async def test_upgrade_verify_retains_install_proxy_and_service_checks_and_skips_history(monkeypatch, tmp_path):
    import mcp
    import mcp.client.stdio as stdio
    client = UpgradeStatusClient()
    checks = []
    sentinel_transport = object()

    class FakeClient:
        def __init__(self, transport):
            assert transport is sentinel_transport
            self.server_info = SimpleNamespace(version="0.14.0")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def list_tools(self):
            checks.append("discovery")

        async def call_tool(self, tool, params):
            return await client.call_tool(tool, params)

    def install(root, version, revision):
        assert root == tmp_path and version == "0.14.0" and revision == "a" * 40
        checks.append("install")
        return tmp_path / "current"

    async def service(version, *, optional=False):
        assert version == "0.14.0"
        checks.append(("service", optional))
        return {"running": True, "version": version, "protocol": 1}

    async def no_history(*_):
        pytest.fail("Migration-status scope must never run history/search/context acceptance")

    monkeypatch.setattr(mcp, "Client", FakeClient)
    monkeypatch.setattr(stdio, "stdio_client", lambda *_args, **_kwargs: sentinel_transport)
    monkeypatch.setattr(runner, "check_install", install)
    monkeypatch.setattr(runner, "check_service", service)
    monkeypatch.setattr(runner, "read_checks", no_history)
    report = await runner.verify(SimpleNamespace(install_root=tmp_path, version="0.14.0", revision="a" * 40,
        chat_id=OLD_CHAT_ID, group_upgrade_operation_id=OPERATION_ID, expected_new_chat_id=NEW_CHAT_ID,
        query=None, pages=5))
    assert checks == ["install", ("service", True), "discovery", ("service", False), "install"]
    assert report["status"] == "passed"
    assert report["acceptance_scope"] == "existing_group_upgrade_status"
    assert report["migration_status"] == "passed"
    assert len(client.calls) == 1
    rendered = json.dumps(report)
    assert "history" not in rendered and "private" not in rendered and OPERATION_ID not in rendered


@pytest.mark.asyncio
async def test_direct_verify_rejects_incomplete_upgrade_scope_before_install_check(monkeypatch):
    def forbidden_install(*_):
        pytest.fail("Scope validation must precede installed/live checks")
    monkeypatch.setattr(runner, "check_install", forbidden_install)
    with pytest.raises(runner.AcceptanceError, match="supplied together"):
        await runner.verify(SimpleNamespace(group_upgrade_operation_id=OPERATION_ID, expected_new_chat_id=None))


@pytest.mark.asyncio
async def test_existing_history_acceptance_still_requires_second_page():
    client = SyntheticClient()
    client.rows = client.rows[:20]
    with pytest.raises(runner.AcceptanceError, match="more than twenty"):
        await runner.read_checks(client, OLD_CHAT_ID, None, 5)
