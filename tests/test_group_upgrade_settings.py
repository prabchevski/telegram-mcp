"""Group migration opt-in is independent and preserves owner client choices."""
from __future__ import annotations

import json
import tomllib
from argparse import Namespace

import pytest

from test_managed_updates import REVISION, make_version, managed, write_entry
from telegram_search_mcp import activation, cli, installation, launchers, registration, updater
from telegram_search_mcp.group_upgrade_settings import group_upgrades_enabled, set_group_upgrades
from telegram_search_mcp.pinning_settings import pinning_enabled, set_pinning
from telegram_search_mcp.sending_settings import sending_enabled, set_sending
from telegram_search_mcp.tool_catalog import advertised_tools, GROUP_UPGRADE_TOOLS

pytestmark = pytest.mark.usefixtures("trusted_macos_env_launcher")


def codex_entry(path):
    return tomllib.loads(path.read_text())["mcp_servers"]["telegram_search"]


@pytest.mark.parametrize("sending", [False, True])
@pytest.mark.parametrize("pinning", [False, True])
def test_group_upgrade_opt_in_is_independent_and_retains_policy(managed, sending, pinning):
    root, _, config = managed
    if sending:
        set_sending(root, True)
    if pinning:
        set_pinning(root, True)
    entry = codex_entry(config)
    entry["default_tools_approval_mode"] = "approve"
    entry.pop("supports_parallel_tool_calls")
    write_entry("codex", config, entry)
    before = config.read_bytes()
    assert group_upgrades_enabled(root) is False
    set_group_upgrades(root, True)
    current = codex_entry(config)
    assert current["enabled_tools"] == list(advertised_tools(
        sending=sending, pinning=pinning, group_upgrades=True))
    assert current["default_tools_approval_mode"] == "approve"
    assert "supports_parallel_tool_calls" not in current
    assert sending_enabled(root) is sending
    assert pinning_enabled(root) is pinning
    set_group_upgrades(root, False)
    assert config.read_bytes() == before
    assert sending_enabled(root) is sending
    assert pinning_enabled(root) is pinning


def test_all_independent_toggles_refresh_both_clients_and_preserve_group_permission(managed, tmp_path):
    root, version, config = managed
    gemini = tmp_path / "gemini/settings.json"
    registration.configure("register", clients=["gemini"], python=str(version / ".venv/bin/python"),
                           paths={"gemini": gemini}, install_root=root)
    updater.remember_clients(root, {"gemini": gemini})
    data = json.loads(gemini.read_text())
    data["mcpServers"]["telegram-search"]["trust"] = True
    data["ui"] = {"theme": "owner-theme"}
    gemini.write_text(json.dumps(data))
    set_group_upgrades(root, True)
    set_sending(root, True)
    set_pinning(root, True)
    current = json.loads(gemini.read_text())
    assert current["mcpServers"]["telegram-search"]["includeTools"] == codex_entry(config)["enabled_tools"]
    assert current["mcpServers"]["telegram-search"]["trust"] is True
    assert current["ui"] == data["ui"]
    set_sending(root, False)
    set_pinning(root, False)
    assert group_upgrades_enabled(root) is True
    assert codex_entry(config)["enabled_tools"] == list(advertised_tools(group_upgrades=True))


@pytest.mark.parametrize("change", ["removed", "filtered", "disabled", "filtered_group"])
def test_group_toggle_never_restores_owner_limited_registration(managed, change):
    root, _, config = managed
    set_group_upgrades(root, True)
    entry = codex_entry(config)
    if change == "removed":
        config.write_text('[mcp_servers.other]\ncommand="other"\n')
    else:
        if change == "filtered":
            entry["enabled_tools"].pop(0)
        elif change == "filtered_group":
            entry["enabled_tools"].remove("telegram_upgrade_group")
        else:
            entry["enabled"] = False
        write_entry("codex", config, entry)
    before = config.read_bytes()
    set_group_upgrades(root, False)
    assert group_upgrades_enabled(root) is False
    assert config.read_bytes() == before
    with pytest.raises(RuntimeError, match="No unchanged"):
        set_group_upgrades(root, True)
    assert config.read_bytes() == before
    assert group_upgrades_enabled(root) is False


def test_group_registration_failure_rolls_back_only_its_preference(managed, monkeypatch):
    root, _, config = managed
    set_sending(root, True)
    set_pinning(root, True)
    before = config.read_bytes()
    monkeypatch.setattr(registration, "configure", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("failed registration")))
    with pytest.raises(RuntimeError, match="failed registration"):
        set_group_upgrades(root, True)
    assert group_upgrades_enabled(root) is False
    assert sending_enabled(root) is True
    assert pinning_enabled(root) is True
    assert config.read_bytes() == before


@pytest.mark.parametrize("enabled", [False, True])
def test_install_upgrade_keeps_explicit_group_preference_and_client_policy(managed, monkeypatch, enabled):
    root, old, config = managed
    set_group_upgrades(root, enabled)
    before = (root / "group-upgrades.json").read_bytes()
    entry = codex_entry(config)
    entry["default_tools_approval_mode"] = "never"
    write_entry("codex", config, entry)
    new = make_version(root, "new-version")
    monkeypatch.setattr(installation, "_stage_version", lambda *a: new)
    installation.install(old, root, uv="unused", python="unused", clients=["codex"],
                         paths={"codex": config}, revision=REVISION)
    assert launchers.current_version(root) == new
    assert (root / "group-upgrades.json").read_bytes() == before
    assert group_upgrades_enabled(root) is enabled
    assert codex_entry(config) == entry


def test_exact_013_catalog_refresh_does_not_enable_group_mutation(managed, monkeypatch):
    root, version, config = managed
    entry = activation.before_group_upgrades_entry("codex", codex_entry(config))
    assert len(entry["enabled_tools"]) == 18
    write_entry("codex", config, entry)
    monkeypatch.setattr(activation, "running_version", lambda: version)
    assert activation.migrate(root)["clients"] == ["codex"]
    assert codex_entry(config)["enabled_tools"] == list(advertised_tools())
    assert group_upgrades_enabled(root) is False
    assert not (root / "group-upgrades.json").exists()
    assert set(GROUP_UPGRADE_TOOLS).isdisjoint(codex_entry(config)["enabled_tools"])


@pytest.mark.parametrize("value", [b'{"enabled":1}', b'{"enabled":true,"extra":0}', b'{}', b'[]'])
def test_group_setting_rejects_malformed_or_coerced_preference(managed, value):
    root, _, _ = managed
    (root / "group-upgrades.json").write_bytes(value)
    with pytest.raises((ValueError, RuntimeError)):
        group_upgrades_enabled(root)


def test_group_setting_requires_boolean_and_refuses_redirected_file(managed, tmp_path):
    root, _, _ = managed
    with pytest.raises(ValueError, match="boolean"):
        set_group_upgrades(root, 1)
    target = tmp_path / "outside-preference"
    target.write_text('{"enabled":true}')
    (root / "group-upgrades.json").symlink_to(target)
    with pytest.raises(RuntimeError, match="symlink"):
        group_upgrades_enabled(root)


@pytest.mark.parametrize("action", ["on", "off", "status"])
def test_group_upgrades_cli_uses_only_managed_local_setting(managed, monkeypatch, capsys, action):
    root, _, _ = managed
    monkeypatch.setattr(launchers, "installed_root", lambda: root)
    args = cli.build_parser().parse_args(["group-upgrades", action])
    assert args.handler == cli.command_group_upgrades
    assert args.handler(args) == 0
    assert json.loads(capsys.readouterr().out)["enabled"] is (action == "on")
    assert sending_enabled(root) is False
    assert pinning_enabled(root) is False


def test_group_upgrades_cli_refuses_source_checkout(monkeypatch):
    monkeypatch.setattr(launchers, "installed_root", lambda: None)
    with pytest.raises(RuntimeError, match="installed copy"):
        cli.command_group_upgrades(Namespace(action="on"))
