import io
import json
from pathlib import Path
import stat
import subprocess
import tomllib
import zipfile

import pytest

from telegram_search_mcp import installation, launchers, registration, updater
from telegram_search_mcp import activation

REVISION = "a" * 40


def successful_run(**changes):
    return {"head_sha": REVISION, "head_branch": "main", "event": "push", "status": "completed",
            "conclusion": "success", "head_repository": {"full_name": updater.REPOSITORY},
            "path": ".github/workflows/ci.yml", **changes}


@pytest.mark.parametrize("changes", [
    {"head_sha": "b" * 40}, {"head_branch": "other"}, {"event": "pull_request"},
    {"status": "in_progress"}, {"conclusion": "failure"}, {"path": ".github/workflows/other.yml"},
    {"head_repository": {"full_name": "other/repo"}},
])
def test_only_successful_ci_for_the_exact_canonical_main_commit_is_eligible(monkeypatch, changes):
    monkeypatch.setattr(updater, "_json", lambda url: {"sha": REVISION} if url.endswith("/commits/main") else {"workflow_runs": [successful_run(**changes)]})
    assert updater.checked_revision() is None


def test_verified_main_is_eligible_without_a_github_login(monkeypatch):
    monkeypatch.setattr(updater, "_json", lambda url: {"sha": REVISION} if url.endswith("/commits/main") else {"workflow_runs": [successful_run()]})
    assert updater.checked_revision() == REVISION


def source_zip(extra=None):
    files = {name: b"test source\n" for name in (
        "LICENSE", "pyproject.toml", "uv.lock", "README.md", "install-macos.command", "uninstall-macos.command",
        "src/telegram_search_mcp/updater.py", "src/telegram_search_mcp/server.py")}
    files.update(extra or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, value in files.items():
            entry = zipfile.ZipInfo("telegram-mcp-" + REVISION + "/" + name)
            entry.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(entry, value)
    return buffer.getvalue()


def test_verified_archive_extracts_to_private_directory_and_records_revision(tmp_path):
    target = updater.extract_source(source_zip(), REVISION, tmp_path / "source")
    assert (target / "SOURCE_REVISION").read_text().strip() == REVISION
    assert target.stat().st_mode & 0o777 == 0o700
    assert (target / "LICENSE").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("name,contents", [
    ("../outside.py", b"bad"), (".env", b"secret"), ("profiles/private.py", b"session"),
    ("README.md", b"binary\x00data"), ("src/telegram_search_mcp/extra.py", b"\xff"),
])
def test_unsafe_update_is_rejected_before_writing_any_file(tmp_path, name, contents):
    with pytest.raises((RuntimeError, UnicodeDecodeError)):
        updater.extract_source(source_zip({name: contents}), REVISION, tmp_path / "source")
    assert not (tmp_path / "source").exists()


@pytest.fixture
def managed(tmp_path):
    root = tmp_path.resolve() / "application"
    installation.prepare_root(root)
    old = make_version(root, "old")
    (root / "current").symlink_to(old.name)
    installation._write_launcher(root / launchers.LAUNCHER_NAME, launchers.launcher_text(root, "telegram_search_mcp.server"))
    config = tmp_path.resolve() / "codex/config.toml"
    registration.configure("register", clients=["codex"], python=str(old / ".venv/bin/python"), paths={"codex": config}, install_root=root)
    receipt = {"schema": 1, "version": "0.5.0", "revision": None, "clients": {"codex": str(config)}, "auto_update": True}
    installation.atomic_write(root / installation.RECEIPT, json.dumps(receipt).encode(), expected=None)
    return root, old, config


def make_version(root, name):
    version = root / name
    python = version / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text('#!/bin/sh\nprintf "%s\\n" "$0"\n')
    python.chmod(0o700)
    (version / launchers.VERSION_MARKER).write_text("telegram-search-mcp\n")
    (version / "pyproject.toml").write_text('[project]\nname="telegram-search-mcp"\nversion="0.5.0"\n')
    return version


def test_stable_launcher_resolves_each_version_before_starting(managed):
    root, old, config = managed
    command = ["/bin/sh", str(root / launchers.LAUNCHER_NAME)]
    assert subprocess.check_output(command, text=True).strip() == str(old / ".venv/bin/python")
    new = make_version(root, "new")
    (root / "current").unlink()
    (root / "current").symlink_to(new.name)
    assert subprocess.check_output(command, text=True).strip() == str(new / ".venv/bin/python")
    assert old.is_dir()


def test_next_service_uses_latest_version_even_from_an_old_proxy(managed, monkeypatch):
    root, old, _ = managed
    new = make_version(root, "new")
    (root / "current").unlink()
    (root / "current").symlink_to(new.name)
    monkeypatch.setattr(launchers, "installed_root", lambda: root)
    assert launchers.service_python() == str(new / ".venv/bin/python")


def test_failed_activation_restores_previous_version_and_config(managed, monkeypatch):
    root, old, config = managed
    # Start with a 0.4-style direct interpreter registration so activation really
    # changes settings before the simulated receipt failure.
    registration.configure("register", clients=["codex"], python=str(old / ".venv/bin/python"), paths={"codex": config})
    before = config.read_bytes()
    receipt_before = (root / installation.RECEIPT).read_bytes()
    new = make_version(root, "new")
    monkeypatch.setattr(installation, "_stage_version", lambda *args: new)
    real_write = installation.atomic_write
    def fail_receipt(path, *args, **kwargs):
        if path.name == installation.RECEIPT:
            raise OSError("simulated receipt disk failure")
        return real_write(path, *args, **kwargs)
    monkeypatch.setattr(installation, "atomic_write", fail_receipt)
    with pytest.raises(OSError, match="disk failure"):
        installation.install(old, root, uv="unused", python="unused", clients=["codex"], paths={"codex": config}, revision=REVISION)
    assert launchers.current_version(root) == old
    assert config.read_bytes() == before
    assert (root / installation.RECEIPT).read_bytes() == receipt_before


def test_automatic_update_does_not_restore_a_removed_registration(managed, monkeypatch):
    root, old, config = managed
    config.write_text("# intentionally disconnected\n")
    monkeypatch.setattr(updater, "checked_revision", lambda: REVISION)
    assert updater.update(root)["status"] == "registration_changed"
    assert config.read_text() == "# intentionally disconnected\n"
    assert launchers.current_version(root) == old


def test_changed_receipt_aborts_before_staging_or_registering(managed, monkeypatch):
    root, old, config = managed
    monkeypatch.setattr(installation, "_stage_version", lambda *args: pytest.fail("must not stage"))
    with pytest.raises(RuntimeError, match="settings changed"):
        installation.install(old, root, uv="unused", python="unused", clients=["codex"], paths={"codex": config}, expected_receipt=b"stale")
    assert launchers.current_version(root) == old


def test_disabled_updater_does_not_contact_github(managed, monkeypatch):
    root, _, _ = managed
    monkeypatch.setattr(updater, "configure_schedule", lambda *args: None)
    updater.set_enabled(root, False)
    monkeypatch.setattr(updater, "checked_revision", lambda: pytest.fail("network request while disabled"))
    assert updater.update(root, scheduled=True) == {"status": "disabled"}


def test_daily_check_is_throttled_after_a_network_failure(managed, monkeypatch):
    root, old, _ = managed
    monkeypatch.setattr(updater, "checked_revision", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    with pytest.raises(RuntimeError, match="offline"):
        updater.update(root, scheduled=True)
    assert updater.update(root, scheduled=True) == {"status": "not_due"}
    assert launchers.current_version(root) == old


def test_schedule_is_daily_and_contains_no_telegram_credentials(managed):
    root, _, _ = managed
    definition = updater.schedule_definition(root)
    assert definition["StartInterval"] == 86400
    assert definition["ProgramArguments"] == ["/bin/sh", str(root / "update.command"), "--scheduled", "--install-root", str(root)]
    assert "api_hash" not in json.dumps(definition)


def test_uninstalling_last_client_disables_updates(managed, monkeypatch):
    root, _, _ = managed
    calls = []
    monkeypatch.setattr(updater, "configure_schedule", lambda path, enabled: calls.append((path, enabled)))
    updater.forget_clients(root, ["codex"])
    receipt = installation.read_receipt(root)
    assert receipt["clients"] == {} and receipt["auto_update"] is False
    assert calls == [(root, False)]


def test_legacy_program_directory_is_preserved(managed, monkeypatch):
    root, _, _ = managed
    monkeypatch.setattr(installation, "real_home", lambda: root)
    old = root / "Applications/TelegramSearchMCP"
    old.mkdir(parents=True)
    (old / "pyproject.toml").write_text('[project]\nname="codex-telegram-search-mcp"\n')
    assert installation.default_root() == old.with_name("TelegramSearchMCPShared")
    assert old.is_dir()


def downloaded_update(monkeypatch):
    payload = source_zip({"pyproject.toml": b'[project]\nname="telegram-search-mcp"\nversion="0.5.0"\n'})
    monkeypatch.setattr(updater, "checked_revision", lambda: REVISION)
    monkeypatch.setattr(updater, "find_uv", lambda: "/test/uv")
    monkeypatch.setattr(updater, "_download", lambda *args, **kwargs: payload)


def test_background_update_activates_without_rewriting_client_settings(managed, monkeypatch):
    root, old, config = managed
    before = config.read_bytes()
    downloaded_update(monkeypatch)
    new = make_version(root, "new")
    monkeypatch.setattr(installation, "_stage_version", lambda *args: new)
    monkeypatch.setattr(installation, "configure", lambda *args, **kwargs: pytest.fail("background update must not rewrite settings"))
    monkeypatch.setattr(updater, "configure_schedule", lambda *args: pytest.fail("must not reload its own schedule"))
    assert updater.update(root, scheduled=True)["status"] == "updated"
    assert launchers.current_version(root) == new
    assert installation.read_receipt(root)["revision"] == REVISION
    assert config.read_bytes() == before
    assert (old / ".venv/bin/python").is_file()


def test_failed_downloaded_install_keeps_old_version_and_settings(managed, monkeypatch):
    root, old, config = managed
    before = config.read_bytes()
    downloaded_update(monkeypatch)
    monkeypatch.setattr(installation, "_stage_version", lambda *args: (_ for _ in ()).throw(RuntimeError("dependency install failed")))
    with pytest.raises(RuntimeError, match="dependency install failed"):
        updater.update(root)
    assert launchers.current_version(root) == old
    assert installation.read_receipt(root)["revision"] is None
    assert config.read_bytes() == before


def test_user_edit_during_download_cancels_update(managed, monkeypatch):
    root, old, config = managed
    downloaded_update(monkeypatch)
    original = updater._download
    def download(*args, **kwargs):
        config.write_text("# disconnected during download\n")
        return original(*args, **kwargs)
    monkeypatch.setattr(updater, "_download", download)
    monkeypatch.setattr(installation, "_stage_version", lambda *args: pytest.fail("must not stage"))
    with pytest.raises(RuntimeError, match="registration changed"):
        updater.update(root)
    assert config.read_text() == "# disconnected during download\n"
    assert launchers.current_version(root) == old


def test_invalid_source_revision_fails_before_changing_registration(managed, monkeypatch):
    root, old, config = managed
    before = config.read_bytes()
    (old / "SOURCE_REVISION").write_text("invalid")
    monkeypatch.setattr(installation, "_stage_version", lambda *args: pytest.fail("must not stage"))
    with pytest.raises(RuntimeError, match="Invalid source revision"):
        installation.install(old, root, uv="unused", python="unused", clients=["codex"], paths={"codex": config})
    assert config.read_bytes() == before


def test_schedule_can_enable_recover_and_disable_without_touching_other_jobs(managed, monkeypatch):
    root, _, _ = managed
    monkeypatch.setattr(updater, "real_home", lambda: root)
    monkeypatch.setattr(updater.sys, "platform", "darwin")
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1 if args[1] == "print" else 0)
    monkeypatch.setattr(updater.subprocess, "run", run)
    updater.configure_schedule(root, True)
    agents = root / "Library/LaunchAgents"
    unrelated = agents / "unrelated.plist"
    unrelated.write_text("other job")
    updater.configure_schedule(root, True)
    assert [args[1] for args in calls] == ["bootstrap", "print", "bootstrap"]
    updater.configure_schedule(root, False)
    assert calls[-1][1] == "bootout"
    assert list(agents.glob("*.plist")) == [unrelated]
    assert unrelated.read_text() == "other job"


def test_receipt_tracks_second_client_and_preserves_custom_path_on_unrelated_removal(managed, monkeypatch):
    root, _, config = managed
    gemini = root / "gemini/settings.json"
    updater.remember_clients(root, {"gemini": gemini})
    updater.forget_clients(root, ["codex"], {"codex": root / "elsewhere/config.toml"})
    assert installation.read_receipt(root)["clients"] == {"codex": str(config), "gemini": str(gemini)}


def write_entry(client, path, entry):
    if client == "codex":
        path.write_bytes(registration._replace_codex(b'model = "preserved"\n', entry))
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"ui": {"theme": "preserved"}, "mcpServers": {registration.ALIASES[client]: entry}}))


@pytest.mark.parametrize("sending", [False, True])
def test_06_activation_migrates_both_clients_once_preserving_login_and_sending(managed, monkeypatch, sending):
    root, version, codex = managed
    from telegram_search_mcp.sending_settings import set_sending
    if sending:
        set_sending(root, True)
    gemini = root / "gemini/settings.json"
    updater.remember_clients(root, {"gemini": gemini})
    wanted = {}
    for client, path in {"codex": codex, "gemini": gemini}.items():
        wanted[client] = registration.expected_entry(client, str(version / ".venv/bin/python"), root)
        write_entry(client, path, activation.legacy_entry(client, wanted[client]))
    # Stand-ins outside the installation: migration must never even open these.
    profile = root.parent / "saved-profile"
    profile.write_bytes(b"existing login must be preserved")
    receipt = (root / installation.RECEIPT).read_bytes()
    monkeypatch.setattr(activation, "running_version", lambda: version)
    calls = []
    monkeypatch.setattr(activation, "notify_restart", lambda: calls.append(True) or True)
    assert activation.migrate(root, notify=True)["clients"] == ["codex", "gemini"]
    assert activation.migrate(root, notify=True)["status"] == "unchanged"
    assert len(calls) == 1
    assert profile.read_bytes() == b"existing login must be preserved"
    assert (root / installation.RECEIPT).read_bytes() == receipt
    assert tomllib.loads(codex.read_text())["model"] == "preserved"
    assert json.loads(gemini.read_text())["ui"] == {"theme": "preserved"}
    for client, path in {"codex": codex, "gemini": gemini}.items():
        actual = registration._load(client, path, path.read_bytes())[registration.NAMES[client]][registration.ALIASES[client]]
        assert actual == wanted[client]
        assert len(actual["enabled_tools" if client == "codex" else "includeTools"]) == (9 if sending else 6)
        assert list(path.parent.glob(path.name + ".telegram-search-backup-*"))


@pytest.mark.parametrize("change", ["removed", "tools", "approval", "disabled", "command"])
def test_06_activation_never_restores_owner_edits(managed, monkeypatch, change):
    root, version, config = managed
    wanted = registration.expected_entry("codex", str(version / ".venv/bin/python"), root)
    entry = activation.legacy_entry("codex", wanted)
    if change == "removed":
        entry = None
    elif change == "tools":
        entry["enabled_tools"].pop()
    elif change == "approval":
        entry["default_tools_approval_mode"] = "never"
    elif change == "disabled":
        entry["enabled"] = False
    else:
        entry["command"] = "/custom/launcher"
    write_entry("codex", config, entry)
    before = config.read_bytes()
    monkeypatch.setattr(activation, "running_version", lambda: version)
    assert activation.migrate(root) == {"status": "unchanged", "clients": [], "skipped": ["codex"]}
    assert config.read_bytes() == before
    assert not (root / activation.NOTICE).exists()


def test_staged_or_old_version_cannot_migrate_live_clients(managed, monkeypatch):
    root, _, config = managed
    monkeypatch.setattr(activation, "running_version", lambda: root / "not-current")
    before = config.read_bytes()
    assert activation.migrate(root)["status"] == "inactive_version"
    assert config.read_bytes() == before


def test_concurrent_owner_edit_during_activation_is_not_overwritten(managed, monkeypatch):
    root, version, config = managed
    wanted = registration.expected_entry("codex", str(version / ".venv/bin/python"), root)
    write_entry("codex", config, activation.legacy_entry("codex", wanted))
    monkeypatch.setattr(activation, "running_version", lambda: version)
    real_configure = activation.configure
    def edited(*args, **kwargs):
        config.write_text("# disconnected by owner\n")
        return real_configure(*args, **kwargs)
    monkeypatch.setattr(activation, "configure", edited)
    with pytest.raises(RuntimeError, match="changed during migration"):
        activation.migrate(root)
    assert config.read_text() == "# disconnected by owner\n"


def test_activation_runs_before_throttle_and_does_not_need_another_release(managed, monkeypatch):
    root, version, config = managed
    wanted = registration.expected_entry("codex", str(version / ".venv/bin/python"), root)
    write_entry("codex", config, activation.legacy_entry("codex", wanted))
    monkeypatch.setattr(activation, "running_version", lambda: version)
    monkeypatch.setattr(activation, "notify_restart", lambda: False)
    (root / "last-update-check.json").write_text(json.dumps({"time": updater.time.time()}))
    monkeypatch.setattr(updater, "checked_revision", lambda: pytest.fail("not due"))
    assert updater.update(root, scheduled=True)["status"] == "not_due"
    assert tomllib.loads(config.read_text())["mcp_servers"]["telegram_search"] == wanted
    assert activation.read_notice(root)["notification_requested"] is False


def test_notification_failure_does_not_revert_successful_migration(managed, monkeypatch):
    root, version, config = managed
    wanted = registration.expected_entry("codex", str(version / ".venv/bin/python"), root)
    write_entry("codex", config, activation.legacy_entry("codex", wanted))
    monkeypatch.setattr(activation, "running_version", lambda: version)
    monkeypatch.setattr(activation, "notify_restart", lambda: False)
    assert activation.migrate(root, notify=True)["status"] == "migrated"
    assert activation.read_notice(root)["notification_requested"] is False
    monkeypatch.setattr(activation, "notify_restart", lambda: True)
    activation.migrate(root, notify=True)
    assert activation.read_notice(root)["notification_requested"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("running,busy,stopped", [("0.6.1", False, True), ("0.6.1", True, False), ("99.0.0", False, False)])
async def test_service_upgrade_drains_without_downgrading_or_interrupting_work(managed, monkeypatch, running, busy, stopped):
    from telegram_search_mcp import service_client
    from telegram_search_mcp.wire import ServiceBusyError
    state = {"version": running, "busy": busy, "queued": 0}
    calls = []
    async def status(_):
        return state
    async def stop(*args, **kwargs):
        calls.append("graceful-stop")
    monkeypatch.setattr(service_client, "_status", status)
    monkeypatch.setattr(service_client, "stop_service", stop)
    if busy:
        with pytest.raises(ServiceBusyError):
            await service_client._compatible_service("default", None)
    else:
        assert await service_client._compatible_service("default", None) == (None if stopped else state)
    assert bool(calls) is stopped
