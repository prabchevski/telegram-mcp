"""Separate pin permission, generated client filters, and upgrade preservation."""
import json
import tomllib
from argparse import Namespace

import pytest

from test_managed_updates import REVISION, make_version, managed, write_entry
from telegram_search_mcp import activation, cli, installation, launchers, registration, updater
from telegram_search_mcp.pinning_settings import pinning_enabled, set_pinning
from telegram_search_mcp.sending_settings import sending_enabled, set_sending
from telegram_search_mcp.tool_catalog import DEFAULT_TOOLS, OUTGOING_TOOLS, PIN_TOOLS

pytestmark = pytest.mark.usefixtures("trusted_macos_env_launcher")


def codex_entry(path):
    return tomllib.loads(path.read_text())["mcp_servers"]["telegram_search"]


@pytest.mark.parametrize("sending", [False, True])
def test_pin_permission_is_independent_and_preserves_sending_and_owner_policy(managed, sending):
    root, version, config = managed
    if sending:
        set_sending(root, True)
    entry = codex_entry(config)
    entry["default_tools_approval_mode"] = "approve"
    entry.pop("supports_parallel_tool_calls")
    write_entry("codex", config, entry)
    before = config.read_bytes()
    assert pinning_enabled(root) is False
    set_pinning(root, True)
    current = codex_entry(config)
    assert current["enabled_tools"] == list(DEFAULT_TOOLS) + (list(OUTGOING_TOOLS) if sending else []) + list(PIN_TOOLS)
    assert current["default_tools_approval_mode"] == "approve"
    assert "supports_parallel_tool_calls" not in current
    assert sending_enabled(root) is sending
    assert pinning_enabled(root) is True
    set_pinning(root, False)
    assert config.read_bytes() == before
    assert sending_enabled(root) is sending


def test_pinning_refreshes_both_generated_clients_and_sending_can_change_independently(managed, tmp_path):
    root, version, config = managed
    gemini = tmp_path / "gemini/settings.json"
    python = str(version / ".venv/bin/python")
    registration.configure("register", clients=["gemini"], python=python,
                           paths={"gemini": gemini}, install_root=root)
    updater.remember_clients(root, {"gemini": gemini})
    data = json.loads(gemini.read_text())
    data["mcpServers"]["telegram-search"]["trust"] = True
    data["ui"] = {"theme": "owner-theme"}
    gemini.write_text(json.dumps(data))
    set_pinning(root, True)
    set_sending(root, True)
    current = json.loads(gemini.read_text())
    assert current["mcpServers"]["telegram-search"]["includeTools"] == codex_entry(config)["enabled_tools"]
    assert current["mcpServers"]["telegram-search"]["trust"] is True
    assert current["ui"] == data["ui"]
    set_sending(root, False)
    assert pinning_enabled(root) is True
    assert set(PIN_TOOLS) <= set(codex_entry(config)["enabled_tools"])
    assert set(OUTGOING_TOOLS).isdisjoint(codex_entry(config)["enabled_tools"])


@pytest.mark.parametrize("change", ["removed", "filtered", "disabled"])
def test_pinning_does_not_restore_owner_limited_registration(managed, change):
    root, _, config = managed
    set_pinning(root, True)
    entry = codex_entry(config)
    if change == "removed":
        config.write_text('[mcp_servers.other]\ncommand="other"\n')
    else:
        if change == "filtered":
            entry["enabled_tools"].pop(0)
        else:
            entry["enabled"] = False
        write_entry("codex", config, entry)
    before = config.read_bytes()
    set_pinning(root, False)
    assert pinning_enabled(root) is False
    assert config.read_bytes() == before
    with pytest.raises(RuntimeError, match="No unchanged"):
        set_pinning(root, True)
    assert config.read_bytes() == before
    assert pinning_enabled(root) is False


def test_failed_registration_rolls_back_pin_preference_without_changing_sending(managed, monkeypatch):
    root, _, config = managed
    set_sending(root, True)
    before = config.read_bytes()
    monkeypatch.setattr(registration, "configure", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("failed registration")))
    with pytest.raises(RuntimeError, match="failed registration"):
        set_pinning(root, True)
    assert pinning_enabled(root) is False
    assert sending_enabled(root) is True
    assert config.read_bytes() == before


@pytest.mark.parametrize("enabled", [False, True])
def test_upgrade_preserves_explicit_pin_preference_and_policy(managed, monkeypatch, enabled):
    root, old, config = managed
    set_pinning(root, enabled)
    before = (root / "pinning.json").read_bytes()
    entry = codex_entry(config)
    entry["default_tools_approval_mode"] = "never"
    write_entry("codex", config, entry)
    new = make_version(root, "new-version")
    monkeypatch.setattr(installation, "_stage_version", lambda *a: new)
    installation.install(old, root, uv="unused", python="unused", clients=["codex"],
                         paths={"codex": config}, revision=REVISION)
    assert launchers.current_version(root) == new
    assert (root / "pinning.json").read_bytes() == before
    assert pinning_enabled(root) is enabled
    assert codex_entry(config) == entry


def test_exact_0112_catalog_upgrades_without_enabling_pinning(managed, monkeypatch):
    root, version, config = managed
    entry = activation.before_pinning_entry("codex", codex_entry(config))
    write_entry("codex", config, entry)
    monkeypatch.setattr(activation, "running_version", lambda: version)
    assert activation.migrate(root)["clients"] == ["codex"]
    assert codex_entry(config)["enabled_tools"] == list(DEFAULT_TOOLS)
    assert pinning_enabled(root) is False
    assert not (root / "pinning.json").exists()


@pytest.mark.parametrize("change", ["disabled", "filtered_pin"])
def test_automatic_upgrade_preserves_owner_limits_with_pinning_enabled(managed, monkeypatch, change):
    root, version, config = managed
    set_pinning(root, True)
    entry = codex_entry(config)
    if change == "disabled":
        entry["enabled"] = False
    else:
        entry["enabled_tools"].remove("telegram_unpin_message")
    write_entry("codex", config, entry)
    before = config.read_bytes()
    monkeypatch.setattr(activation, "running_version", lambda: version)
    assert activation.migrate(root) == {"status": "unchanged", "clients": [], "skipped": ["codex"]}
    monkeypatch.setattr(updater, "checked_revision", lambda: REVISION)
    monkeypatch.setattr(installation, "_stage_version", lambda *a: pytest.fail("must not stage a customized connection"))
    assert updater.update(root) == {"status": "registration_changed", "client": "codex"}
    assert config.read_bytes() == before
    assert pinning_enabled(root) is True


def test_malformed_and_symlinked_pin_preference_fail_closed(managed, tmp_path):
    root, _, _ = managed
    path = root / "pinning.json"
    for contents in ('{"enabled":1}', '{"enabled":true,"extra":false}', 'x' * 129):
        path.write_text(contents)
        with pytest.raises((RuntimeError, ValueError)):
            pinning_enabled(root)
    path.unlink()
    other = tmp_path / "other.json"
    other.write_text('{"enabled":true}')
    path.symlink_to(other)
    with pytest.raises(RuntimeError):
        pinning_enabled(root)
    with pytest.raises(ValueError, match="boolean"):
        set_pinning(root, 1)


@pytest.mark.parametrize("action", ["status", "on", "off"])
def test_cli_pinning_parser_and_dispatch(managed, monkeypatch, capsys, action):
    root, _, _ = managed
    monkeypatch.setattr(launchers, "installed_root", lambda: root)
    args = cli.build_parser().parse_args(["pinning", action])
    assert args.handler == cli.command_pinning
    assert args.handler(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["enabled"] is (action == "on")
    assert sending_enabled(root) is False


def test_cli_pinning_refuses_source_checkout(monkeypatch):
    monkeypatch.setattr(launchers, "installed_root", lambda: None)
    with pytest.raises(RuntimeError, match="installed copy"):
        cli.command_pinning(Namespace(action="on"))
