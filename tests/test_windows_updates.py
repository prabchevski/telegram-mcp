"""Windows update state and ownership checks run without a Telegram session."""
import hashlib
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from telegram_search_mcp import windows_install as installer
from telegram_search_mcp import windows_update_schedule as scheduler
from telegram_search_mcp import windows_updates as updates
from telegram_search_mcp.config_io import atomic_write, read_source
from telegram_search_mcp.launchers import ROOT_MARKER, VERSION_MARKER
from telegram_search_mcp.platform_support import ensure_private_dir, open_private_file

PREVIOUS = "b" * 40
REVISION = "a" * 40


def private_write(path, content):
    ensure_private_dir(path.parent)
    descriptor = open_private_file(path, exclusive=True)
    with os.fdopen(descriptor, "wb") as output:
        output.write(content)


@pytest.fixture
def managed(tmp_path, monkeypatch):
    root = tmp_path / "Telegram MCP"
    private_write(root / ROOT_MARKER, b"telegram-search-mcp\n")
    version = root / "version-0.10.1-abcdef012345"
    private_write(version / VERSION_MARKER, b"telegram-search-mcp\n")
    private_write(root / "current.json", json.dumps({"version": version.name}).encode())
    for name, module in (("launch-mcp.ps1", "telegram_search_mcp.server"),
                         ("tgsearch.ps1", "telegram_search_mcp.cli"),
                         ("update.ps1", "telegram_search_mcp.windows_updates")):
        private_write(root / name, installer.windows_launcher_text(root, module).encode())
    monkeypatch.setattr(updates, "current_version", lambda root: version)
    marketplace = tmp_path / ".agents/plugins/marketplace.json"
    plugin = tmp_path / "plugins/telegram-mcp-work/.mcp.json"
    manifest = tmp_path / "plugins/telegram-mcp-work/.codex-plugin/plugin.json"
    private_write(marketplace, json.dumps({"name": "personal", "plugins": [installer.marketplace_entry()]}).encode())
    private_write(plugin, b'{"mcpServers":{"telegram":{"command":"owned"}}}\n')
    private_write(manifest, b'{"name":"telegram-mcp-work","version":"0.10.1"}\n')
    uv = root / "tools/uv-0.11.15-abcdef012345/uv.exe"
    private_write(uv, b"verified private uv executable fixture")
    receipt = {"platform": "windows", "version": "0.10.1", "revision": PREVIOUS,
               "auto_update": True, "updates_configured": True, "automatic_updates": True,
               "marketplace": str(marketplace), "entry": installer.marketplace_entry(),
               "plugin_files": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (plugin, manifest)},
               "uv": {"path": str(uv), "version": "0.11.15", "sha256": hashlib.sha256(uv.read_bytes()).hexdigest()}}
    private_write(root / installer.RECEIPT, json.dumps(receipt).encode())
    monkeypatch.setattr(installer, "UV_SHA256", receipt["uv"]["sha256"])
    private_write(root / "sending.json", b'{"enabled":true}\n')
    private_write(root / "native-profile-canary", b"existing credentials and native profile")
    candidate = {"revision": REVISION, "version": "0.11.0"}
    monkeypatch.setattr(updates.update_feed, "checked_release", lambda: candidate)
    monkeypatch.setattr(updates.update_feed, "ensure_forward", lambda previous, candidate: None)
    return root, receipt, candidate


def save_receipt(root, receipt):
    path = root / installer.RECEIPT
    atomic_write(path, json.dumps(receipt).encode(), expected=read_source(path))


def tree_contents(path):
    return {str(item.relative_to(path)): item.read_bytes() for item in path.rglob("*") if item.is_file()}


def test_check_only_has_no_writes_download_or_install(managed, monkeypatch):
    root, _, _ = managed
    before = tree_contents(root.parent)
    monkeypatch.setattr(updates.update_feed, "download_release", lambda _: pytest.fail("Check must not download"))
    monkeypatch.setattr(installer, "install", lambda *_args, **_kwargs: pytest.fail("Check must not install"))
    result = updates.update(root, check_only=True)
    assert result == {"status": "available", "revision": REVISION, "version": "0.11.0"}
    assert tree_contents(root.parent) == before


def test_scheduled_opt_out_is_quiet_and_read_only(managed, monkeypatch):
    root, receipt, _ = managed
    receipt["auto_update"] = False
    save_receipt(root, receipt)
    before = tree_contents(root.parent)
    monkeypatch.setattr(updates.update_feed, "checked_release", lambda: pytest.fail("Disabled update must not contact GitHub"))
    assert updates.update(root, scheduled=True) == {"status": "disabled"}
    assert tree_contents(root.parent) == before


def test_scheduled_check_runs_once_per_24_hours_and_saves_result(managed, monkeypatch):
    root, _, _ = managed
    monkeypatch.setattr(updates.update_feed, "checked_release", lambda: None)
    assert updates.update(root, scheduled=True) == {"status": "waiting_for_checked_release"}
    assert updates.read_update_status(root)["status"] == "waiting_for_checked_release"
    previous_status = (root / updates.STATUS_FILE).read_bytes()
    monkeypatch.setattr(updates.update_feed, "checked_release", lambda: pytest.fail("Not due must not contact GitHub"))
    assert updates.update(root, scheduled=True) == {"status": "not_due"}
    assert (root / updates.STATUS_FILE).read_bytes() == previous_status


def test_manual_check_ignores_scheduled_due_time(managed):
    root, _, _ = managed
    private_write(root / updates.CHECK_FILE, json.dumps({"time": updates.time.time()}).encode())
    before = tree_contents(root)
    assert updates.update(root, scheduled=True, check_only=True)["status"] == "available"
    assert tree_contents(root) == before


def test_expired_check_is_due(managed, monkeypatch):
    root, _, _ = managed
    private_write(root / updates.CHECK_FILE, json.dumps({"time": updates.time.time() - updates.CHECK_INTERVAL - 1}).encode())
    monkeypatch.setattr(updates.update_feed, "checked_release", lambda: None)
    assert updates.update(root, scheduled=True)["status"] == "waiting_for_checked_release"


@pytest.mark.parametrize("owner_change", ["removed", "disabled", "edited", "duplicate", "plugin_removed", "plugin_edited"])
def test_registration_owner_changes_never_restored(managed, monkeypatch, owner_change):
    root, receipt, _ = managed
    marketplace = Path(receipt["marketplace"])
    data = json.loads(marketplace.read_bytes())
    if owner_change == "removed":
        data["plugins"] = []
    elif owner_change == "disabled":
        data["plugins"][0]["enabled"] = False
    elif owner_change == "edited":
        data["plugins"][0]["source"]["path"] = "./plugins/custom"
    elif owner_change == "duplicate":
        data["plugins"].append(installer.marketplace_entry())
    elif owner_change == "plugin_removed":
        Path(next(iter(receipt["plugin_files"]))).unlink()
    else:
        path = Path(next(iter(receipt["plugin_files"])))
        atomic_write(path, b"owner changed this config\n", expected=read_source(path))
    atomic_write(marketplace, json.dumps(data).encode(), expected=read_source(marketplace))
    before = tree_contents(root.parent)
    monkeypatch.setattr(updates.update_feed, "checked_release", lambda: pytest.fail("Changed plugin must not contact GitHub"))
    assert updates.update(root, check_only=True)["status"] == "registration_changed"
    assert tree_contents(root.parent) == before


def test_up_to_date_does_not_replace_anything(managed, monkeypatch):
    root, receipt, candidate = managed
    candidate.update({"revision": receipt["revision"], "version": receipt["version"]})
    before = tree_contents(root.parent)
    monkeypatch.setattr(updates.update_feed, "download_release", lambda _: pytest.fail("Current revision must not download"))
    assert updates.update(root, check_only=True)["status"] == "up_to_date"
    assert tree_contents(root.parent) == before


def test_version_downgrade_is_refused_and_failure_durable(managed):
    root, receipt, _ = managed
    receipt["version"] = "0.12.0"
    save_receipt(root, receipt)
    with pytest.raises(RuntimeError, match="version downgrade"):
        updates.update(root, scheduled=True)
    assert updates.read_update_status(root)["status"] == "failed"
    assert installer.read_receipt(root) == receipt


def test_unpublished_local_revision_preserves_program_and_saves_explanation(managed, monkeypatch):
    root, receipt, _ = managed
    receipt["revision"] = None
    save_receipt(root, receipt)
    def unpublished(previous, candidate):
        raise updates.UnpublishedRevisionError("Local fixes are unpublished; automatic replacement stopped")
    monkeypatch.setattr(updates.update_feed, "ensure_forward", unpublished)
    with pytest.raises(updates.UnpublishedRevisionError):
        updates.update(root, scheduled=True)
    result = updates.read_update_status(root)
    assert result["status"] == "failed"
    assert "Local fixes" in result["message"]
    assert installer.read_receipt(root) == receipt


def test_success_passes_owned_snapshots_receipt_and_revision_to_installer(managed, monkeypatch):
    root, receipt, candidate = managed
    previous_receipt = (root / installer.RECEIPT).read_bytes()
    sending = (root / "sending.json").read_bytes()
    native = (root / "native-profile-canary").read_bytes()
    monkeypatch.setattr(updates.update_feed, "download_release", lambda value: b"checked release")
    monkeypatch.setattr(updates.update_feed, "validate_release_archive", lambda payload, version, **kwargs: {"README.md": b"validated source"})
    called = []
    def run(command, **kwargs):
        assert command[0] == receipt["uv"]["path"]
        assert "--frozen" in command and "--no-editable" in command and "--managed-python" in command
        assert "--prepare-only" in command
        source = Path(command[command.index("--source") + 1])
        assert (source / "README.md").read_bytes() == b"validated source"
        state_path = Path(command[command.index("--update-state-file") + 1])
        state = json.loads(state_path.read_bytes())
        assert state["schema"] == 1
        assert base64.b64decode(state["receipt"]) == previous_receipt
        snapshot = {path: base64.b64decode(content) if content is not None else None
                    for path, content in state["registration"].items()}
        for path, content in installer.registration_snapshot(receipt, Path(receipt["marketplace"])).items():
            assert snapshot[path] == content
        assert str(root / "current.json") in snapshot and str(root / "launch-mcp.ps1") in snapshot
        assert command[command.index("--revision") + 1] == REVISION
        assert command[command.index("--auto-update") + 1] == "keep"
        called.append(source)
        receipt.update({"version": candidate["version"], "revision": REVISION})
        save_receipt(root, receipt)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(updates.subprocess, "run", run)
    result = updates.update(root, scheduled=True)
    assert result["status"] == "updated" and result["restart_required"] is True
    assert len(called) == 1 and not called[0].exists()
    assert not list(root.glob("update-state-*"))
    assert (root / "sending.json").read_bytes() == sending
    assert (root / "native-profile-canary").read_bytes() == native
    assert updates.read_update_status(root)["restart_notice"] == updates.RESTART_MESSAGE


def test_receipt_change_during_download_is_not_accepted_as_new_expected_state(managed, monkeypatch):
    root, receipt, _ = managed
    previous_receipt = (root / installer.RECEIPT).read_bytes()
    def download(_candidate):
        receipt["auto_update"] = False
        save_receipt(root, receipt)
        return b"checked"
    monkeypatch.setattr(updates.update_feed, "download_release", download)
    monkeypatch.setattr(updates.update_feed, "validate_release_archive", lambda *_args, **_kwargs: {"README.md": b"checked"})
    def run(command, **kwargs):
        state = json.loads(Path(command[command.index("--update-state-file") + 1]).read_bytes())
        assert base64.b64decode(state["receipt"]) == previous_receipt
        if read_source(root / installer.RECEIPT) != base64.b64decode(state["receipt"]):
            raise RuntimeError("receipt changed during update")
        pytest.fail("Must not replace program")
    monkeypatch.setattr(updates.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="receipt changed"):
        updates.update(root)
    assert installer.read_receipt(root)["auto_update"] is False
    assert updates.read_update_status(root)["status"] == "failed"
    assert not list(root.glob("update-source-*"))


@pytest.mark.parametrize("runtime_change", ["outside_root", "changed_binary", "missing_metadata"])
def test_untrusted_path_runtime_never_executes(managed, monkeypatch, runtime_change):
    root, receipt, _ = managed
    if runtime_change == "outside_root":
        receipt["uv"]["path"] = str(root.parent / "workspace/uv.exe")
    elif runtime_change == "changed_binary":
        receipt["uv"]["sha256"] = "0" * 64
    else:
        receipt.pop("uv")
    save_receipt(root, receipt)
    monkeypatch.setattr(updates.update_feed, "download_release", lambda _: pytest.fail("No download with untrusted runtime"))
    with pytest.raises(RuntimeError, match="runtime"):
        updates.update(root)
    assert updates.read_update_status(root)["status"] == "failed"


def test_restart_notice_survives_quiet_later_checks(managed, monkeypatch):
    root, _, _ = managed
    updates.record_update_status(root, {"status": "updated", "restart_required": True, "message": updates.RESTART_MESSAGE})
    monkeypatch.setattr(updates.update_feed, "checked_release", lambda: None)
    updates.update(root)
    saved = updates.read_update_status(root)
    assert saved["status"] == "waiting_for_checked_release"
    assert saved["restart_required"] is True
    assert saved["restart_notice"] == updates.RESTART_MESSAGE


def test_explicit_off_persists_and_is_preserved_by_repeated_settings(managed, monkeypatch):
    root, _, _ = managed
    configured = []
    monkeypatch.setattr(scheduler, "configure_schedule", lambda root, enabled: configured.append(enabled))
    updates.set_enabled(root, False)
    updates.set_enabled(root, False)
    assert configured == [False, False]
    receipt = installer.read_receipt(root)
    assert receipt["auto_update"] is False and receipt["automatic_updates"] is False
    assert receipt["updates_configured"] is True


def test_preference_write_failure_never_changes_task(managed, monkeypatch):
    root, receipt, _ = managed
    before = read_source(root / installer.RECEIPT)
    monkeypatch.setattr(scheduler, "configure_schedule", lambda *_args: pytest.fail("Must save preference before changing task"))
    monkeypatch.setattr(updates, "atomic_write", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk failure")))
    with pytest.raises(OSError, match="disk failure"):
        updates.set_enabled(root, False)
    assert read_source(root / installer.RECEIPT) == before


def test_failed_enable_restores_original_receipt(managed, monkeypatch):
    root, receipt, _ = managed
    receipt["auto_update"] = False
    save_receipt(root, receipt)
    before = read_source(root / installer.RECEIPT)
    def failed(*_args):
        assert installer.read_receipt(root)["auto_update"] is True
        raise RuntimeError("Task could not be created")
    monkeypatch.setattr(scheduler, "configure_schedule", failed)
    with pytest.raises(RuntimeError, match="could not"):
        updates.set_enabled(root, True)
    assert read_source(root / installer.RECEIPT) == before


def test_opt_out_remains_effective_when_owner_task_cannot_be_removed(managed, monkeypatch):
    root, receipt, _ = managed
    def changed(*_args):
        raise RuntimeError("Owner edited the task; it was preserved")
    monkeypatch.setattr(scheduler, "configure_schedule", changed)
    with pytest.raises(RuntimeError, match="preserved"):
        updates.set_enabled(root, False)
    assert installer.read_receipt(root)["auto_update"] is False
    monkeypatch.setattr(updates.update_feed, "checked_release", lambda: pytest.fail("Opt-out must stay effective"))
    assert updates.update(root, scheduled=True) == {"status": "disabled"}


def test_enable_rollback_preserves_concurrent_owner_receipt_edit(managed, monkeypatch):
    root, receipt, _ = managed
    receipt["auto_update"] = False
    save_receipt(root, receipt)
    def changed(*_args):
        edited = installer.read_receipt(root)
        edited["owner_canary"] = "preserved"
        save_receipt(root, edited)
        raise RuntimeError("Task could not be created")
    monkeypatch.setattr(scheduler, "configure_schedule", changed)
    with pytest.raises(RuntimeError, match="could not"):
        updates.set_enabled(root, True)
    assert installer.read_receipt(root)["owner_canary"] == "preserved"


@pytest.mark.parametrize("name", ["launch-mcp.ps1", "tgsearch.ps1", "update.ps1"])
def test_changed_stable_windows_launchers_are_never_restored(managed, monkeypatch, name):
    root, _, _ = managed
    path = root / name
    atomic_write(path, b"owner customized launcher\n", expected=read_source(path))
    before = tree_contents(root.parent)
    monkeypatch.setattr(updates.update_feed, "checked_release", lambda: pytest.fail("Changed launcher must not contact GitHub"))
    assert updates.update(root, check_only=True)["status"] == "registration_changed"
    assert tree_contents(root.parent) == before


def test_candidate_bootstrap_removes_inherited_injection_variables(managed, monkeypatch):
    root, receipt, candidate = managed
    source = root / "candidate source"
    ensure_private_dir(source)
    for key in ("UV_INDEX_URL", "UV_PYTHON", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "TDJSON_LIBRARY", "TGSEARCH_DATA_DIR"):
        monkeypatch.setenv(key, "untrusted")
    observed = []
    def run(command, **kwargs):
        environment = kwargs["env"]
        assert not any(key.upper().startswith("UV_") for key in environment)
        assert all(key not in environment for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "TDJSON_LIBRARY", "TGSEARCH_DATA_DIR"))
        assert command[0] == receipt["uv"]["path"]
        observed.append(command)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(updates.subprocess, "run", run)
    updates._run_candidate_installer(root, source, Path(receipt["marketplace"]), Path(receipt["uv"]["path"]),
                                     candidate, read_source(root / installer.RECEIPT), {})
    assert len(observed) == 1
    assert not list(root.glob("update-state-*"))


def test_status_includes_schedule_durable_result_and_restart_notice(managed, monkeypatch):
    root, _, _ = managed
    updates.record_update_status(root, {"status": "updated", "restart_required": True, "message": updates.RESTART_MESSAGE})
    monkeypatch.setattr(scheduler, "schedule_status", lambda root: {"enabled": True, "owned": True})
    status = updates.updates_status(root)
    assert status["automatic"] is True and status["channel"] == "checked-release"
    assert status["interval_hours"] == 24 and status["schedule"]["owned"] is True
    assert status["last_check"]["status"] == "updated"
    assert status["notice"] == updates.RESTART_MESSAGE


def test_scheduled_current_and_not_due_main_are_silent(managed, monkeypatch, capsys):
    root, _, _ = managed
    monkeypatch.setattr(sys, "argv", ["windows_updates", "--scheduled", "--install-root", str(root)])
    monkeypatch.setattr(updates, "update", lambda *args, **kwargs: {"status": "up_to_date"})
    updates.main()
    assert capsys.readouterr().out == ""


def test_updater_imports_without_posix_or_mcp_bootstrap():
    source = Path(__file__).resolve().parents[1] / "src"
    script = '''
import builtins
import sys
# On POSIX these standard-library modules legitimately load pwd/fcntl.
# Import them first so the guard tests the project's eager dependencies.
import pathlib
import subprocess
import shutil
sys.path.insert(0, sys.argv[1])
original = builtins.__import__
forbidden = {'pwd', 'fcntl', 'pydantic', 'mcp', 'win32api', 'win32com', 'pythoncom'}
def checked(name, *args, **kwargs):
    assert name.split('.')[0] not in forbidden, 'Eager platform or SDK import: ' + name
    return original(name, *args, **kwargs)
builtins.__import__ = checked
from telegram_search_mcp import windows_updates, update_feed
assert windows_updates.CHECK_INTERVAL == 86400
'''
    subprocess.run([sys.executable, "-I", "-c", script, str(source)], check=True, capture_output=True, timeout=30)
