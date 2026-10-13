"""Managed macOS updates refresh complete catalogs without changing owner policy.

All configurations, receipts and version launchers live in disposable directories.
No Telegram profile, credential store or background schedule is accessed.
"""
from __future__ import annotations

import copy

import pytest

from telegram_search_mcp import activation, installation, launchers, registration, updater
from telegram_search_mcp.sending_settings import set_sending
from test_managed_updates import REVISION, downloaded_update, make_version, managed, write_entry


HISTORICAL_ENTRIES = (
    activation.legacy_entry, activation.previous_entry, activation.navigation_entry,
    activation.initial_public_entry, activation.before_editing_entry,
)


@pytest.fixture
def clients(managed):
    root, version, codex = managed
    gemini = root / "gemini/settings.json"
    registration.configure("register", clients=["gemini"], python=str(version / ".venv/bin/python"),
                           paths={"gemini": gemini}, install_root=root)
    updater.remember_clients(root, {"gemini": gemini})
    return root, version, {"codex": codex, "gemini": gemini}


def load_entry(client, path):
    return registration._load(client, path, path.read_bytes())[registration.NAMES[client]][registration.ALIASES[client]]


def with_policy(client, entry, preference):
    result = copy.deepcopy(entry)
    if preference == "absent":
        for key in registration.POLICY_KEYS[client]:
            result.pop(key, None)
    elif client == "codex":
        result["default_tools_approval_mode"] = preference
        result["supports_parallel_tool_calls"] = True
    else:
        result["trust"] = preference == "approve"
    return result


@pytest.mark.parametrize("old_entry", HISTORICAL_ENTRIES)
@pytest.mark.parametrize("sending", [False, True])
@pytest.mark.parametrize("preference", ["approve", "never", "absent"])
def test_known_historical_catalogs_migrate_both_clients_preserving_policy_and_absence(
        clients, monkeypatch, old_entry, sending, preference):
    root, version, paths = clients
    if sending:
        set_sending(root, True)
    expected = {}
    for client, path in paths.items():
        wanted = registration.expected_entry(client, str(version / ".venv/bin/python"), root)
        write_entry(client, path, with_policy(client, old_entry(client, wanted), preference))
        expected[client] = with_policy(client, wanted, preference)
    before_receipt = (root / installation.RECEIPT).read_bytes()
    monkeypatch.setattr(activation, "running_version", lambda: version)
    result = activation.migrate(root)
    unchanged = old_entry is activation.before_editing_entry and not sending
    assert result["status"] == ("unchanged" if unchanged else "migrated")
    assert result["skipped"] == []
    for client, path in paths.items():
        assert load_entry(client, path) == expected[client]
        assert len(expected[client]["enabled_tools" if client == "codex" else "includeTools"]) == (22 if sending else 15)
    assert activation.migrate(root) == {"status": "unchanged", "clients": [], "skipped": []}
    assert (root / installation.RECEIPT).read_bytes() == before_receipt


@pytest.mark.parametrize("absent", ["default_tools_approval_mode", "supports_parallel_tool_calls"])
def test_codex_policy_fields_can_be_independently_absent(clients, monkeypatch, absent):
    root, version, paths = clients
    set_sending(root, True)
    wanted = registration.expected_entry("codex", str(version / ".venv/bin/python"), root)
    previous = with_policy("codex", activation.before_editing_entry("codex", wanted), "never")
    previous.pop(absent)
    write_entry("codex", paths["codex"], previous)
    expected = with_policy("codex", wanted, "never")
    expected.pop(absent)
    monkeypatch.setattr(activation, "running_version", lambda: version)
    assert activation.migrate(root)["clients"] == ["codex"]
    assert load_entry("codex", paths["codex"]) == expected


@pytest.mark.parametrize("preference", ["approve", "never", "absent"])
def test_normal_installer_upgrade_preserves_both_client_policies_and_absence(clients, monkeypatch, preference):
    root, old, paths = clients
    set_sending(root, True)
    expected = {}
    for client, path in paths.items():
        wanted = registration.expected_entry(client, str(old / ".venv/bin/python"), root)
        write_entry(client, path, with_policy(client, activation.before_editing_entry(client, wanted), preference))
        expected[client] = with_policy(client, wanted, preference)
    new = make_version(root, "normal-upgrade-version")
    monkeypatch.setattr(installation, "_stage_version", lambda *args: new)
    installation.install(old, root, uv="unused", python="unused", clients=list(paths), paths=paths, revision=REVISION)
    assert launchers.current_version(root) == new
    registration.configure("verify", clients=list(paths), python=str(new / ".venv/bin/python"),
                           paths=paths, install_root=root)
    for client, path in paths.items():
        assert load_entry(client, path) == expected[client]


@pytest.mark.parametrize("client", ["codex", "gemini"])
@pytest.mark.parametrize("preference", ["approve", "never", "absent"])
def test_normal_registration_can_upgrade_known_direct_python_without_resetting_policy(clients, client, preference):
    root, version, paths = clients
    python = str(version / ".venv/bin/python")
    direct = registration.expected_entry(client, python)
    write_entry(client, paths[client], with_policy(client, activation.legacy_entry(client, direct), preference))
    registration.configure("register", clients=[client], python=python, paths=paths, install_root=root)
    wanted = registration.expected_entry(client, python, root)
    assert load_entry(client, paths[client]) == with_policy(client, wanted, preference)


@pytest.mark.parametrize("client", ["codex", "gemini"])
def test_verify_accepts_current_policy_but_rejects_a_stale_catalog(clients, client):
    root, version, paths = clients
    set_sending(root, True)
    python = str(version / ".venv/bin/python")
    current = with_policy(client, registration.expected_entry(client, python, root), "approve")
    write_entry(client, paths[client], current)
    before = paths[client].read_bytes()
    assert registration.configure("verify", clients=[client], python=python, paths=paths, install_root=root) == []
    assert paths[client].read_bytes() == before
    write_entry(client, paths[client], activation.before_editing_entry(client, current))
    stale = paths[client].read_bytes()
    with pytest.raises(RuntimeError, match="exact bounded"):
        registration.configure("verify", clients=[client], python=python, paths=paths, install_root=root)
    assert paths[client].read_bytes() == stale


@pytest.mark.parametrize("client", ["codex", "gemini"])
@pytest.mark.parametrize("change", ["filtered", "disabled", "unknown", "command"])
def test_normal_registration_refuses_owner_limits_before_changing_either_client(clients, client, change):
    root, version, paths = clients
    entry = load_entry(client, paths[client])
    if change == "filtered":
        entry["enabled_tools" if client == "codex" else "includeTools"].pop(0)
    elif change == "disabled":
        entry["enabled"] = False
    elif change == "unknown":
        entry["owner_setting"] = "preserved"
    else:
        entry["command"] = "/custom/launcher"
    write_entry(client, paths[client], entry)
    before = {name: path.read_bytes() for name, path in paths.items()}
    with pytest.raises(RuntimeError, match="customized"):
        registration.configure("register", clients=list(paths), python=str(version / ".venv/bin/python"),
                               paths=paths, install_root=root, replace_legacy=True)
    assert {name: path.read_bytes() for name, path in paths.items()} == before


def test_explicit_registration_can_reconnect_an_absent_client(clients):
    root, version, paths = clients
    write_entry("codex", paths["codex"], None)
    registration.configure("register", clients=["codex"], python=str(version / ".venv/bin/python"),
                           paths=paths, install_root=root)
    assert load_entry("codex", paths["codex"]) == registration.expected_entry("codex", str(version / ".venv/bin/python"), root)


@pytest.mark.parametrize("client", ["codex", "gemini"])
@pytest.mark.parametrize("change", ["removed", "disabled", "unknown", "environment", "command", "arguments",
                                   "filtered", "extra_tool", "reordered", "timeout"])
def test_automatic_catalog_refresh_preserves_custom_and_disconnected_connections(
        clients, monkeypatch, client, change):
    root, version, paths = clients
    set_sending(root, True)
    path = paths[client]
    wanted = registration.expected_entry(client, str(version / ".venv/bin/python"), root)
    entry = with_policy(client, activation.before_editing_entry(client, wanted), "approve")
    key = "enabled_tools" if client == "codex" else "includeTools"
    if change == "removed":
        entry = None
    elif change == "disabled":
        entry["enabled"] = False
    elif change == "unknown":
        entry["owner_setting"] = "preserved"
    elif change == "environment":
        entry["env"] = {"OWNER_SETTING": "preserved"}
    elif change == "command":
        entry["command"] = "/custom/launcher"
    elif change == "arguments":
        entry["args"].append("--custom")
    elif change == "filtered":
        entry[key].pop(0)
    elif change == "extra_tool":
        entry[key].append("telegram_custom_tool")
    elif change == "reordered":
        entry[key].reverse()
    else:
        entry["tool_timeout_sec" if client == "codex" else "timeout"] = 999
    if change == "environment" and client == "codex":
        entry.pop("env")
        write_entry(client, path, entry)
        with path.open("ab") as output:
            output.write(b'\n[mcp_servers.telegram_search.env]\nOWNER_SETTING = "preserved"\n')
    else:
        write_entry(client, path, entry)
    before = path.read_bytes()
    monkeypatch.setattr(activation, "running_version", lambda: version)
    assert activation.migrate(root) == {"status": "unchanged", "clients": [], "skipped": [client]}
    assert path.read_bytes() == before
    monkeypatch.setattr(updater, "checked_revision", lambda: REVISION)
    monkeypatch.setattr(installation, "_stage_version", lambda *args: pytest.fail("must not stage a customized connection"))
    assert updater.update(root) == {"status": "registration_changed", "client": client}
    assert path.read_bytes() == before
    assert launchers.current_version(root) == version


@pytest.mark.parametrize("preference", ["approve", "never", "absent"])
def test_policy_differences_are_eligible_for_background_code_updates_and_preserved(clients, monkeypatch, preference):
    root, old, paths = clients
    set_sending(root, True)
    for client, path in paths.items():
        wanted = registration.expected_entry(client, str(old / ".venv/bin/python"), root)
        write_entry(client, path, with_policy(client, wanted, preference))
    before = {client: path.read_bytes() for client, path in paths.items()}
    downloaded_update(monkeypatch)
    new = make_version(root, "new-policy-version")
    monkeypatch.setattr(installation, "_stage_version", lambda *args: new)
    assert updater.update(root)["status"] == "updated"
    assert launchers.current_version(root) == new
    assert installation.read_receipt(root)["revision"] == REVISION
    assert {client: path.read_bytes() for client, path in paths.items()} == before


def test_policy_update_installs_new_code_then_migrates_19_to_22_with_current_snapshots(clients, monkeypatch):
    root, old, paths = clients
    set_sending(root, True)
    expected = {}
    for client, path in paths.items():
        wanted = registration.expected_entry(client, str(old / ".venv/bin/python"), root)
        write_entry(client, path, with_policy(client, activation.before_editing_entry(client, wanted), "approve"))
        expected[client] = with_policy(client, wanted, "approve")
    # The old version's activation cannot refresh this installation until the
    # updater invokes activation from the newly selected immutable version.
    monkeypatch.setattr(activation, "running_version", lambda: root / "not-current")
    downloaded_update(monkeypatch)
    new = make_version(root, "new-catalog-version")
    monkeypatch.setattr(installation, "_stage_version", lambda *args: new)
    real_run = updater.subprocess.run
    calls = []
    def activate_new(args, **kwargs):
        if args[-1] == "telegram_search_mcp.activation":
            assert args[0] == str(new / ".venv/bin/python")
            assert launchers.current_version(root) == new
            monkeypatch.setattr(activation, "running_version", lambda: new)
            calls.append(activation.migrate(root))
            return None
        return real_run(args, **kwargs)
    monkeypatch.setattr(updater.subprocess, "run", activate_new)
    assert updater.update(root)["status"] == "updated"
    assert calls == [{"status": "migrated", "clients": ["codex", "gemini"], "skipped": []}]
    for client, path in paths.items():
        assert load_entry(client, path) == expected[client]


@pytest.mark.parametrize("client", ["codex", "gemini"])
def test_owner_policy_edit_during_download_aborts_before_staging(clients, monkeypatch, client):
    root, old, paths = clients
    downloaded_update(monkeypatch)
    original = updater._download
    path = paths[client]
    edited = with_policy(client, load_entry(client, path), "approve")
    def download(*args, **kwargs):
        write_entry(client, path, edited)
        return original(*args, **kwargs)
    monkeypatch.setattr(updater, "_download", download)
    monkeypatch.setattr(installation, "_stage_version", lambda *args: pytest.fail("must not stage after a concurrent owner edit"))
    with pytest.raises(RuntimeError, match="registration changed"):
        updater.update(root)
    assert load_entry(client, path) == edited
    assert launchers.current_version(root) == old


@pytest.mark.parametrize("client", ["codex", "gemini"])
def test_owner_policy_edit_between_activation_scan_and_config_lock_is_not_overwritten(clients, monkeypatch, client):
    root, version, paths = clients
    set_sending(root, True)
    for name, path in paths.items():
        wanted = registration.expected_entry(name, str(version / ".venv/bin/python"), root)
        write_entry(name, path, with_policy(name, activation.before_editing_entry(name, wanted), "never"))
    other = "gemini" if client == "codex" else "codex"
    before_other = paths[other].read_bytes()
    edited = with_policy(client, load_entry(client, paths[client]), "approve")
    monkeypatch.setattr(activation, "running_version", lambda: version)
    real_configure = activation.configure
    def changed_before_locks(*args, **kwargs):
        write_entry(client, paths[client], edited)
        return real_configure(*args, **kwargs)
    monkeypatch.setattr(activation, "configure", changed_before_locks)
    with pytest.raises(RuntimeError, match="changed during migration"):
        activation.migrate(root)
    assert load_entry(client, paths[client]) == edited
    assert paths[other].read_bytes() == before_other
    assert not (root / activation.NOTICE).exists()


def test_policy_refresh_requires_locked_source_snapshots(clients):
    root, version, paths = clients
    with pytest.raises(ValueError, match="byte snapshots"):
        registration.configure("register", clients=["codex"], python=str(version / ".venv/bin/python"),
                               paths=paths, install_root=root, preserve_policy=True)


def test_policy_refresh_rechecks_source_when_preparing_inside_locks(clients, monkeypatch):
    root, version, paths = clients
    set_sending(root, True)
    path = paths["codex"]
    wanted = registration.expected_entry("codex", str(version / ".venv/bin/python"), root)
    previous = with_policy("codex", activation.before_editing_entry("codex", wanted), "never")
    write_entry("codex", path, previous)
    snapshot = path.read_bytes()
    real_prepare = registration._prepare
    edited = with_policy("codex", previous, "approve")
    def changed_during_prepare(*args, **kwargs):
        write_entry("codex", path, edited)
        return real_prepare(*args, **kwargs)
    monkeypatch.setattr(registration, "_prepare", changed_during_prepare)
    with pytest.raises(RuntimeError, match="changed during migration"):
        registration.configure("register", clients=["codex"], python=str(version / ".venv/bin/python"),
                               paths=paths, install_root=root, expected_sources={"codex": snapshot}, preserve_policy=True)
    assert load_entry("codex", path) == edited
