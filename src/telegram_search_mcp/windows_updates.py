"""Daily checked-release updates for the native Windows shared installation."""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid

from .config_io import atomic_write, read_source
from .launchers import current_version, installed_root, validate_root
from .platform_support import acquire_file_lock, assert_private_path, ensure_private_dir, open_private_file
from . import update_feed

CHECK_INTERVAL = 86400
STATUS_FILE = "last-update-status.json"
CHECK_FILE = "last-update-check.json"
RESTART_MESSAGE = (
    "Telegram MCP was updated. Restart Codex, ChatGPT desktop or Codex CLI and start a new chat "
    "to load the new tools. Existing Telegram authorization and sending settings were preserved."
)
UnpublishedRevisionError = update_feed.UnpublishedRevisionError


def _installer():
    # No POSIX installer, registration, MCP SDK or TDLib imports are needed by
    # this module, including when it is started by Task Scheduler.
    from . import windows_install
    return windows_install


def _read_object(root: Path, filename: str) -> dict | None:
    source = read_source(root / filename)
    if source is None:
        return None
    if len(source) > 64 * 1024:
        raise RuntimeError("Invalid saved Windows update state")
    value = json.loads(source)
    if not isinstance(value, dict):
        raise RuntimeError("Invalid saved Windows update state")
    return value


def read_update_status(root: Path) -> dict | None:
    validate_root(root)
    return _read_object(root, STATUS_FILE)


def _record(root: Path, filename: str, value: dict) -> None:
    path = root / filename
    atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode(), expected=read_source(path))


def record_update_status(root: Path, result: dict) -> None:
    previous = _read_object(root, STATUS_FILE)
    saved = {**result, "checked_at": time.time()}
    # A quiet daily "up_to_date" check cannot prove that the desktop client
    # restarted. Keep the actionable restart notice visible until another
    # installation replaces it; do not repeatedly print it in scheduled runs.
    if result.get("restart_required"):
        saved["restart_notice"] = result.get("message", RESTART_MESSAGE)
    elif previous and previous.get("restart_required"):
        saved["restart_required"] = True
        saved["restart_notice"] = previous.get("restart_notice", RESTART_MESSAGE)
    _record(root, STATUS_FILE, saved)


@contextmanager
def _update_lock(root: Path):
    descriptor = open_private_file(root / "windows-update.lock")
    try:
        try:
            acquire_file_lock(descriptor)
        except BlockingIOError as exc:
            raise RuntimeError("Another Windows update check is running") from exc
        yield
    finally:
        os.close(descriptor)


def _enabled(receipt: dict) -> bool:
    value = receipt.get("auto_update", receipt.get("automatic_updates", False))
    if type(value) is not bool:
        raise RuntimeError("Invalid automatic update setting")
    return value


def _trusted_uv(root: Path, receipt: dict) -> Path:
    details = receipt.get("uv")
    if (not isinstance(details, dict) or not isinstance(details.get("path"), str)
            or not isinstance(details.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", details["sha256"])):
        raise RuntimeError("The trusted update runtime is missing; run the Windows installer once")
    installer = _installer()
    if details["sha256"] != installer.UV_SHA256 or details.get("version") != installer.UV_VERSION:
        raise RuntimeError("The update runtime is not the pinned verified Windows build")
    path = Path(details["path"])
    # Never search PATH, HOME or another workspace for an updater executable.
    if not path.is_absolute() or path.name.lower() != "uv.exe" or path.parent.parent != root / "tools":
        raise RuntimeError("The update runtime is outside the managed private tools directory")
    assert_private_path(root / "tools", directory=True)
    assert_private_path(path.parent, directory=True)
    assert_private_path(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != details["sha256"]:
        raise RuntimeError("The trusted update runtime was changed; run the Windows installer once")
    return path


def set_enabled(root: Path, enabled: bool) -> None:
    if type(enabled) is not bool:
        raise TypeError("Automatic update preference must be Boolean")
    validate_root(root)
    installer = _installer()
    from .windows_update_schedule import configure_schedule
    with installer.install_lock(root):
        receipt_source = read_source(root / installer.RECEIPT)
        receipt = installer.read_receipt(root)
        if not receipt:
            raise RuntimeError("Install this version once before configuring updates")
        if enabled:
            _trusted_uv(root, receipt)
        receipt["auto_update"] = enabled
        receipt["updates_configured"] = True
        if "automatic_updates" in receipt:
            receipt["automatic_updates"] = enabled
        replacement = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode()
        atomic_write(root / installer.RECEIPT, replacement, expected=receipt_source)
        try:
            configure_schedule(root, enabled)
        except BaseException:
            # An opt-out remains effective even if an edited task cannot be
            # removed: every scheduled invocation checks this flag first.
            # Failed enablement restores only our own untouched receipt write.
            if enabled and read_source(root / installer.RECEIPT) == replacement:
                atomic_write(root / installer.RECEIPT, receipt_source, expected=replacement)
            raise


def updates_status(root: Path) -> dict:
    validate_root(root)
    receipt = _installer().read_receipt(root)
    if not receipt:
        raise RuntimeError("Install this version once before inspecting updates")
    from .windows_update_schedule import schedule_status
    last_result = read_update_status(root)
    return {"automatic": _enabled(receipt), "enabled": _enabled(receipt), "installed": True,
            "channel": "checked-release", "interval_hours": 24, "version": receipt.get("version"),
            "revision": receipt.get("revision"), "schedule": schedule_status(root),
            "notice": last_result.get("restart_notice", RESTART_MESSAGE) if last_result and last_result.get("restart_required") else None,
            "last_check": last_result, "last_result": last_result}


def _not_due(root: Path) -> bool:
    previous = _read_object(root, CHECK_FILE)
    checked_at = previous.get("time") if previous else None
    if type(checked_at) not in (int, float) or not math.isfinite(checked_at):
        return False
    return 0 <= time.time() - checked_at < CHECK_INTERVAL


def _remove_source(path: Path) -> None:
    # Downloaded source files are read-only, including the Windows read-only
    # attribute. Clear only files inside this disposable private source tree.
    def writable_remove(function, item, _exc_info):
        os.chmod(item, stat.S_IWRITE | stat.S_IREAD)
        function(item)
    shutil.rmtree(path, onerror=writable_remove)


@contextmanager
def _private_source(root: Path, files: dict[str, bytes]):
    directory = root / ("update-source-" + uuid.uuid4().hex)
    ensure_private_dir(directory)
    try:
        for name, content in files.items():
            target = directory / name
            ensure_private_dir(target.parent)
            descriptor = open_private_file(target, exclusive=True)
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            target.chmod(stat.S_IREAD)
        yield directory
    finally:
        _remove_source(directory)


def _launcher_snapshot(root: Path, installer) -> dict[str, bytes | None] | None:
    snapshot = {str(root / "current.json"): read_source(root / "current.json")}
    current_version(root)
    for name, module in (("launch-mcp.ps1", "telegram_search_mcp.server"),
                         ("tgsearch.ps1", "telegram_search_mcp.cli"),
                         ("update.ps1", "telegram_search_mcp.windows_updates")):
        path = root / name
        content = read_source(path)
        if content != installer.windows_launcher_text(root, module).encode():
            return None
        snapshot[str(path)] = content
    return snapshot


def _run_candidate_installer(root: Path, source: Path, marketplace: Path, uv: Path,
                             candidate: dict, receipt_source: bytes,
                             snapshot: dict[str, bytes | None]) -> None:
    state = {"schema": 1, "receipt": base64.b64encode(receipt_source).decode("ascii"),
             "registration": {path: base64.b64encode(content).decode("ascii") if content is not None else None
                              for path, content in snapshot.items()}}
    encoded = (json.dumps(state, ensure_ascii=False) + "\n").encode()
    if len(encoded) > 256 * 1024:
        raise RuntimeError("Windows update ownership state exceeds its size limit")
    path = root / ("update-state-" + uuid.uuid4().hex + ".json")
    descriptor = open_private_file(path, exclusive=True)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        environment = {key: value for key, value in os.environ.items()
                       if not key.upper().startswith("UV_") and key.upper() not in {
                           "VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "TDJSON_LIBRARY", "TGSEARCH_DATA_DIR",
                       }}
        # Run THIS candidate's installer, rather than installing new source
        # with an older launch/schedule schema. Its locked package environment
        # supplies new dependencies before it prepares the immutable version.
        subprocess.run([
            str(uv), "run", "--project", str(source), "--frozen", "--no-dev", "--no-editable", "--no-config",
            "--managed-python", "--python", "3.13", "python", "-I", "-X", "utf8",
            str(source / "scripts/install-windows.py"), "--source", str(source), "--uv", str(uv),
            "--install-dir", str(root), "--marketplace-path", str(marketplace), "--prepare-only",
            "--auto-update", "keep", "--revision", candidate["revision"], "--update-state-file", str(path),
        ], check=True, env=environment, stdout=subprocess.DEVNULL)
    finally:
        path.unlink(missing_ok=True)


def _perform_update(root: Path, receipt: dict, receipt_source: bytes, *, check_only: bool) -> dict:
    installer = _installer()
    marketplace_value = receipt.get("marketplace")
    if not isinstance(marketplace_value, str) or not Path(marketplace_value).is_absolute():
        raise RuntimeError("Invalid owned plugin marketplace")
    marketplace = Path(marketplace_value)
    # User removal, disabling, edits or ownership changes must be respected,
    # including when main has not changed. The updater never restores a plugin.
    if not installer._unchanged_registration(receipt, marketplace):
        return {"status": "registration_changed", "client": "codex_or_chatgpt_work"}
    snapshot = installer.registration_snapshot(receipt, marketplace)
    launchers = _launcher_snapshot(root, installer)
    if launchers is None:
        return {"status": "registration_changed", "client": "windows_launchers"}
    snapshot.update(launchers)
    candidate = update_feed.checked_release()
    if candidate is None:
        return {"status": "waiting_for_checked_release"}
    if update_feed.version_tuple(candidate["version"]) < update_feed.version_tuple(receipt.get("version")):
        raise RuntimeError("Automatic version downgrade refused")
    installed_revision = receipt.get("revision")
    if candidate["revision"] == installed_revision:
        if receipt.get("version") != candidate["version"]:
            raise RuntimeError("Installed package metadata differs from the published revision")
        return {"status": "up_to_date", "revision": installed_revision, "version": candidate["version"]}
    update_feed.ensure_forward(installed_revision, candidate["revision"])
    if check_only:
        return {"status": "available", "revision": candidate["revision"], "version": candidate["version"]}
    uv = _trusted_uv(root, receipt)
    payload = update_feed.download_release(candidate)
    files = update_feed.validate_release_archive(payload, candidate["version"], revision=candidate["revision"])
    with _private_source(root, files) as source:
        _run_candidate_installer(root, source, marketplace, uv, candidate, receipt_source, snapshot)
    installed = installer.read_receipt(root)
    if not installed or installed.get("revision") != candidate["revision"] or installed.get("version") != candidate["version"]:
        raise RuntimeError("The candidate installer did not confirm the checked release")
    return {"status": "updated", "revision": candidate["revision"], "version": candidate["version"],
            "restart_required": True, "message": RESTART_MESSAGE}


def update(root: Path, *, scheduled: bool = False, check_only: bool = False) -> dict:
    """Update the shared root; inspection never creates files or changes settings."""
    root = root.absolute()
    validate_root(root)
    installer = _installer()
    receipt_source = read_source(root / installer.RECEIPT)
    receipt = installer.read_receipt(root)
    if not receipt or receipt_source is None:
        raise RuntimeError("Install the current package once to enable managed updates")
    if scheduled and not _enabled(receipt):
        return {"status": "disabled"}
    if check_only:
        return _perform_update(root, receipt, receipt_source, check_only=True)
    with _update_lock(root):
        # Read again after acquiring our lock. The installer has a separate
        # lock; its compare-and-swap receipt and plugin snapshots guard the
        # download interval and immutable version preparation.
        receipt_source = read_source(root / installer.RECEIPT)
        receipt = installer.read_receipt(root)
        if not receipt or receipt_source is None:
            raise RuntimeError("The installation was removed during the update check")
        if scheduled and not _enabled(receipt):
            return {"status": "disabled"}
        if scheduled and _not_due(root):
            return {"status": "not_due"}
        _record(root, CHECK_FILE, {"time": time.time()})
        try:
            result = _perform_update(root, receipt, receipt_source, check_only=False)
        except Exception as exc:
            record_update_status(root, {
                "status": "failed", "error_type": type(exc).__name__,
                "message": str(exc) if isinstance(exc, UnpublishedRevisionError) else
                "Update failed. Run tgsearch update --check for details.",
            })
            raise
        record_update_status(root, result)
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-root", type=Path)
    parser.add_argument("--scheduled", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = args.install_root or installed_root()
    if root is None:
        raise SystemExit("Run the updater from an installed copy")
    try:
        result = update(root, scheduled=args.scheduled, check_only=args.check)
    except Exception as exc:
        print(f"Telegram Search update failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if not args.scheduled or result["status"] not in {"not_due", "disabled", "up_to_date", "waiting_for_checked_release"}:
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
