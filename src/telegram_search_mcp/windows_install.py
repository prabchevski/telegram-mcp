"""Native Windows installation and local Codex/ChatGPT Work plugin registration.

Program versions are immutable. The source archive never contains a profile;
credentials and the TDLib session are created only by the owner's local auth.
"""
from __future__ import annotations

import argparse
import base64
import binascii
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
import uuid

from .config_io import atomic_write, read_source
from .launchers import ROOT_MARKER, VERSION_MARKER, current_version, validate_root
from .platform_support import (
    acquire_file_lock, assert_private_path, ensure_private_dir, local_data_dir,
    open_owned_readonly, open_private_file, trusted_home,
)

PLUGIN_NAME = "telegram-mcp-work"
RECEIPT = "windows-install.json"
UV_VERSION = "0.11.15"
# uv.exe extracted from the pinned, checksum-verified Windows release ZIP.
UV_SHA256 = "d4ffe0b73cbb1fa3d11242567d55c6e9058c4e885fae9272764409583a4e8640"


def _json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _write_marker(path: Path) -> None:
    fd = open_private_file(path, exclusive=True)
    with os.fdopen(fd, "wb") as output:
        output.write(b"telegram-search-mcp\n")
        output.flush()
        os.fsync(output.fileno())


def powershell_path() -> Path:
    import win32api
    return Path(win32api.GetWindowsDirectory()) / "System32/WindowsPowerShell/v1.0/powershell.exe"


def windows_launcher_text(root: Path, module: str) -> str:
    if module not in {"telegram_search_mcp.server", "telegram_search_mcp.cli", "telegram_search_mcp.windows_updates"}:
        raise ValueError("Unsupported launcher module")
    # The root is derived from this script's location. No interpolation of a
    # user path into PowerShell code and no execution through a mutable symlink.
    return '''$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# An app may inherit PowerShell 7's incompatible module search path.
$env:PSModulePath = [System.IO.Path]::Combine($PSHOME, 'Modules')
# Keep native pipes UTF-8 when Windows PowerShell captures their output.
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$root = $PSScriptRoot
$pointer = Get-Content -LiteralPath (Join-Path $root 'current.json') -Raw | ConvertFrom-Json
$name = [string]$pointer.version
if ($name -notmatch '^version-[0-9]+\\.[0-9]+\\.[0-9]+-[0-9a-f]{12}$') { throw 'Invalid installed version' }
$version = Join-Path $root $name
if ((Get-Item -LiteralPath $version).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing redirected installation' }
if ((Get-Content -LiteralPath (Join-Path $version '.telegram-search-install') -Raw) -ne "telegram-search-mcp`n") { throw 'Invalid installation marker' }
$python = Join-Path $version '.venv\\Scripts\\python.exe'
# Remove workspace injection variables before starting native libraries.
foreach ($key in @('PYTHONPATH','PYTHONHOME','TDJSON_LIBRARY','TGSEARCH_DATA_DIR')) { Remove-Item "Env:$key" -ErrorAction SilentlyContinue }
& $python -I -X utf8 -m ''' + module + ''' @args
exit $LASTEXITCODE
'''


@contextmanager
def install_lock(root: Path):
    fd = open_private_file(root / "windows-install.lock")
    try:
        try:
            acquire_file_lock(fd)
        except BlockingIOError as exc:
            raise RuntimeError("Another Windows installation/settings update is running") from exc
        yield
    finally:
        os.close(fd)


def marketplace_entry() -> dict:
    return {"name": PLUGIN_NAME,
            "source": {"source": "local", "path": "./plugins/" + PLUGIN_NAME},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": "Productivity"}


def merge_marketplace(before: bytes | None, *, owned_entry: dict | None = None) -> bytes:
    data = json.loads(before) if before is not None else {
        "name": "personal", "interface": {"displayName": "Personal"}, "plugins": []}
    if (not isinstance(data, dict) or not isinstance(data.get("name"), str)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", data["name"])
            or not isinstance(data.get("plugins"), list)):
        raise RuntimeError("Invalid local plugin marketplace; repair it before installing")
    matches = [item for item in data["plugins"] if isinstance(item, dict) and item.get("name") == PLUGIN_NAME]
    if matches:
        if len(matches) != 1 or owned_entry is None or matches[0] != owned_entry:
            raise RuntimeError("Telegram plugin entry already exists or was edited; it was not overwritten")
        return before
    data["plugins"].append(marketplace_entry())
    return _json(data)


def _plugin_files(source: Path, root: Path, marketplace: Path) -> dict[Path, bytes]:
    if marketplace.name != "marketplace.json" or marketplace.parent.name != "plugins" or marketplace.parent.parent.name != ".agents":
        raise ValueError("Marketplace path must end in .agents/plugins/marketplace.json")
    directory = marketplace.parents[2] / "plugins" / PLUGIN_NAME
    release = _release_module(source)
    # Raw Git snapshots intentionally omit generated JSON/PowerShell files so
    # deployed macOS 0.6.1 updaters can still accept the source tree unchanged.
    assets = release.windows_assets(release.inventory(source))
    manifest = assets["plugins/" + PLUGIN_NAME + "/.codex-plugin/plugin.json"]
    config = {"mcpServers": {"telegram": {
        "command": str(powershell_path()),
        "args": ["-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(root / "launch-mcp.ps1")],
    }}}
    return {directory / ".codex-plugin/plugin.json": manifest,
            directory / ".mcp.json": _json(config)}


def _unchanged_registration(receipt: dict, marketplace: Path) -> bool:
    return _registration_matches(receipt, marketplace, registration_snapshot(receipt, marketplace))


def _registration_matches(receipt: dict, marketplace: Path, snapshot: dict[str, bytes | None]) -> bool:
    if receipt.get("marketplace") != str(marketplace):
        return False
    before = snapshot.get(str(marketplace))
    if before is None:
        return False
    try:
        data = json.loads(before)
    except ValueError:
        return False
    if not isinstance(data, dict) or not isinstance(data.get("plugins"), list):
        return False
    matches = [entry for entry in data["plugins"]
               if isinstance(entry, dict) and entry.get("name") == PLUGIN_NAME]
    if len(matches) != 1 or matches[0] != receipt.get("entry"):
        return False
    for name, digest in receipt.get("plugin_files", {}).items():
        content = snapshot.get(name)
        if content is None or hashlib.sha256(content).hexdigest() != digest:
            return False
    return bool(receipt.get("plugin_files"))


def registration_snapshot(receipt: dict, marketplace: Path) -> dict[str, bytes | None]:
    return _capture_registration(receipt, marketplace)


def _capture_registration(receipt: dict, marketplace: Path) -> dict[str, bytes | None]:
    paths = {marketplace, *(Path(name) for name in receipt.get("plugin_files", {}))}
    return {str(path): read_source(path) for path in paths}


def read_receipt(root: Path) -> dict | None:
    content = read_source(root / RECEIPT)
    if content is None:
        return None
    receipt = json.loads(content)
    if (not isinstance(receipt, dict) or receipt.get("platform") != "windows"
            or not isinstance(receipt.get("version"), str)
            or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", receipt["version"])
            or not isinstance(receipt.get("marketplace"), str)
            or not isinstance(receipt.get("plugin_files"), dict)
            or any(not isinstance(path, str) or not isinstance(digest, str)
                   or not re.fullmatch(r"[0-9a-f]{64}", digest)
                   for path, digest in receipt["plugin_files"].items())):
        raise RuntimeError("Invalid Windows installation receipt")
    for key in ("auto_update", "automatic_updates", "updates_configured"):
        if key in receipt and type(receipt[key]) is not bool:
            raise RuntimeError("Invalid Windows update preference")
    return receipt


def _update_state(path: Path, root: Path, marketplace: Path) -> tuple[bytes, dict[str, bytes | None]]:
    """Read only bounded CAS data for these explicit installation destinations."""
    validate_root(root)
    if path.parent != root or not re.fullmatch(r"update-state-[0-9a-f]{32}\.json", path.name):
        raise RuntimeError("Update state must be a private temporary file inside this installation")
    descriptor = open_owned_readonly(path, private=True)
    with os.fdopen(descriptor, "rb") as stream:
        content = stream.read(256 * 1024 + 1)
    if len(content) > 256 * 1024:
        raise RuntimeError("Windows update state exceeds the size limit")
    state = json.loads(content)
    if (not isinstance(state, dict) or set(state) != {"schema", "receipt", "registration"}
            or type(state["schema"]) is not int or state["schema"] != 1
            or not isinstance(state["receipt"], str) or not isinstance(state["registration"], dict)):
        raise RuntimeError("Invalid Windows update state")
    def decode(value):
        if not isinstance(value, str):
            raise RuntimeError("Invalid encoded Windows update state")
        try:
            return base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise RuntimeError("Invalid encoded Windows update state") from exc
    receipt_source = decode(state["receipt"])
    if len(receipt_source) > 64 * 1024 or receipt_source != read_source(root / RECEIPT):
        raise RuntimeError("Windows installation receipt changed before candidate setup")
    receipt = read_receipt(root)
    if receipt is None or receipt.get("marketplace") != str(marketplace):
        raise RuntimeError("Windows update state belongs to another marketplace")
    if (marketplace.name != "marketplace.json" or marketplace.parent.name != "plugins"
            or marketplace.parent.parent.name != ".agents"):
        raise RuntimeError("Windows update marketplace must be the explicit local plugin marketplace")
    plugin = marketplace.parents[2] / "plugins" / PLUGIN_NAME
    known_plugin = {plugin / ".mcp.json", plugin / ".codex-plugin/plugin.json"}
    recorded_plugin = {Path(name) for name in receipt["plugin_files"]}
    if not recorded_plugin or not recorded_plugin <= known_plugin:
        raise RuntimeError("The recorded Windows plugin paths are not owned installation files")
    allowed = {marketplace, *recorded_plugin,
               *(root / name for name in ("current.json", "launch-mcp.ps1", "tgsearch.ps1", "update.ps1"))}
    if set(state["registration"]) != {str(item) for item in allowed}:
        raise RuntimeError("Windows update state must contain only the exact owned registration files")
    registration = {name: decode(value) if value is not None else None
                    for name, value in state["registration"].items()}
    if any(value is not None and len(value) > 64 * 1024 for value in registration.values()):
        raise RuntimeError("Windows update registration snapshot exceeds the size limit")
    return receipt_source, registration


def _source_identity(source: Path, revision: str | None) -> tuple[str, str | None]:
    metadata = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
    version = metadata.get("project", {}).get("version")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise RuntimeError("Source package version is invalid")
    source_revision = source / "SOURCE_REVISION"
    if revision is None and source_revision.exists():
        revision = source_revision.read_text(encoding="utf-8").strip()
    if revision is not None and not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise RuntimeError("Source revision must be an exact Git SHA")
    return version, revision


def _open_bootstrap_uv(path: Path) -> int:
    """Read a pinned public executable, including elevated archive extraction.

    Windows may assign Administrators ownership to fresh Expand-Archive files.
    This exception applies only to the executable checked against UV_SHA256;
    installed tools and all profile/configuration files retain private ownership.
    """
    if sys.platform != "win32":
        return open_owned_readonly(path)
    import msvcrt
    from .platform_support import _check_windows_handle, _windows_open
    handle = _windows_open(path, writable=False)
    try:
        _check_windows_handle(handle, directory=False, private=False, allow_system_owner=True)
        descriptor = msvcrt.open_osfhandle(int(handle), os.O_RDONLY | os.O_BINARY)
        handle.Detach()
        return descriptor
    except BaseException:
        handle.Close()
        raise


def _stable_uv(root: Path, uv: Path) -> dict:
    # The bootstrap verifies its ZIP; verify the extracted executable again so
    # later updates can safely run after that temporary bootstrap is deleted.
    fd = _open_bootstrap_uv(uv)
    with os.fdopen(fd, "rb") as stream:
        content = stream.read(80 * 1024 * 1024 + 1)
    if len(content) > 80 * 1024 * 1024 or hashlib.sha256(content).hexdigest() != UV_SHA256:
        raise RuntimeError("The Windows uv executable does not match the pinned verified build")
    directory = root / "tools" / f"uv-{UV_VERSION}-{UV_SHA256[:12]}"
    ensure_private_dir(directory)
    target = directory / "uv.exe"
    before = read_source(target)
    if before is not None and before != content:
        raise RuntimeError("The installed uv executable was edited; it was preserved")
    if before is None:
        atomic_write(target, content, expected=None)
    assert_private_path(target)
    return {"path": str(target), "sha256": UV_SHA256, "version": UV_VERSION}


def _configure_schedule(root: Path, enabled: bool) -> dict:
    # The new current environment and its pywin32 dependency are ready first.
    from .windows_update_schedule import configure_schedule
    return configure_schedule(root, enabled)


def _release_module(source: Path):
    spec = importlib.util.spec_from_file_location("telegram_release", source / "scripts/release.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Release inventory helper is missing")
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    return release


def _copy_source(source: Path, destination: Path) -> None:
    release = _release_module(source)
    # Complete allowlist/data audit before copying any program file.
    payload = release.inventory(source)
    for name, content in payload.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def install(source: Path, root: Path, marketplace: Path, uv: Path, *, auto_update: str = "keep",
            revision: str | None = None, expected_receipt: bytes | None = None,
            registration_snapshot: dict[str, bytes | None] | None = None) -> dict:
    if sys.platform != "win32":
        raise RuntimeError("This installer requires native Windows x64")
    import platform
    if platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("This release requires Windows x64; native ARM64 is not supported")
    if auto_update not in {"keep", "on", "off"}:
        raise ValueError("auto_update must be keep, on or off")
    source_version, source_revision = _source_identity(source, revision)
    # Do not resolve away reparse points before validating them.
    root = root.absolute()
    if root.exists():
        validate_root(root)
    else:
        ensure_private_dir(root)
        _write_marker(root / ROOT_MARKER)
    with install_lock(root):
        receipt_before = read_source(root / RECEIPT)
        if expected_receipt is not None and receipt_before != expected_receipt:
            raise RuntimeError("Windows installation receipt changed during update; retry safely")
        previous = read_receipt(root) or {}
        if registration_snapshot is not None and any(
                read_source(Path(path)) != content for path, content in registration_snapshot.items()):
            raise RuntimeError("Windows plugin registration changed during update; it was preserved")
        if auto_update == "keep":
            automatic_updates = (previous.get("auto_update", previous.get("automatic_updates", True))
                                 if previous.get("updates_configured") else True)
        else:
            automatic_updates = auto_update == "on"
        owned_snapshot = _capture_registration(previous, marketplace) if previous else {str(marketplace): read_source(marketplace)}
        preserve_registration = bool(previous) and not _registration_matches(previous, marketplace, owned_snapshot)
        before_market = owned_snapshot[str(marketplace)]
        plugin_files = {} if preserve_registration else _plugin_files(source, root, marketplace)
        after_market = before_market if preserve_registration else merge_marketplace(
            before_market, owned_entry=previous.get("entry"))
        if not previous:
            for target in plugin_files:
                if target.exists():
                    raise RuntimeError("A Telegram plugin directory already exists; existing files were preserved")
        writes = {
            root / "launch-mcp.ps1": windows_launcher_text(root, "telegram_search_mcp.server").encode(),
            root / "tgsearch.ps1": windows_launcher_text(root, "telegram_search_mcp.cli").encode(),
            root / "update.ps1": windows_launcher_text(root, "telegram_search_mcp.windows_updates").encode(),
            **plugin_files,
        }
        if not preserve_registration:
            writes[marketplace] = after_market
        # Capture before slow dependency work; late owner edits must not become
        # the installer's new expected state and be overwritten.
        snapshots = {path: owned_snapshot[str(path)] if str(path) in owned_snapshot else read_source(path)
                     for path in writes}
        if not previous and any(snapshots[path] is not None for path in plugin_files):
            raise RuntimeError("A Telegram plugin directory already exists; existing files were preserved")
        snapshots[root / RECEIPT] = receipt_before
        snapshots[root / "current.json"] = read_source(root / "current.json")
        uv_receipt = _stable_uv(root, uv)
        version = root / f"version-{source_version}-{uuid.uuid4().hex[:12]}"
        ensure_private_dir(version)
        _copy_source(source, version)
        _write_marker(version / VERSION_MARKER)
        # uv verifies the pinned dependency hashes. -I is used for every runtime
        # entry point; the source tree is installed as a non-editable wheel.
        environment = {k: v for k, v in os.environ.items()
                       if not k.upper().startswith("UV_") and k.upper() not in {"VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"}}
        subprocess.run([uv_receipt["path"], "sync", "--frozen", "--no-dev", "--no-editable", "--no-config", "--managed-python",
                        "--python", "3.13", "--project", str(version)], check=True, env=environment)
        python = version / ".venv/Scripts/python.exe"
        subprocess.run([str(python), "-I", "-X", "utf8", "-c",
                        "from telegram_search_mcp.native_runtime import bundled_candidates,verify; "
                        "verify(bundled_candidates()[0]); from telegram_search_mcp.server import create_server"], check=True)
        subprocess.run([str(python), "-I", "-X", "utf8", "-m", "telegram_search_mcp.cli", "--help"], check=True, stdout=subprocess.DEVNULL)
        receipt = {**previous, "platform": "windows", "version": source_version, "revision": source_revision,
                   "auto_update": automatic_updates, "automatic_updates": automatic_updates,
                   "updates_configured": True, "uv": uv_receipt,
                   "marketplace": str(marketplace), "entry": marketplace_entry(),
                   "plugin_files": {str(p): hashlib.sha256(b).hexdigest() for p, b in plugin_files.items()}}
        if preserve_registration:
            receipt.update({key: previous.get(key) for key in ("marketplace", "entry", "plugin_files")})
        writes[root / RECEIPT] = _json(receipt)
        writes[root / "current.json"] = _json({"version": version.name})
        changed = []
        schedule_attempted = False
        try:
            if registration_snapshot is not None and any(
                    read_source(Path(path)) != content for path, content in registration_snapshot.items()):
                raise RuntimeError("Windows plugin registration changed during update; it was preserved")
            if any(read_source(path) != snapshots[path] for path in writes):
                raise RuntimeError("Configuration changed during Windows installation; it was preserved")
            for path, content in writes.items():
                if content != snapshots[path]:
                    atomic_write(path, content, expected=snapshots[path])
                    changed.append(path)
            schedule_attempted = True
            schedule = _configure_schedule(root, automatic_updates)
        except BaseException:
            for path in reversed(changed):
                if read_source(path) != writes[path]:
                    continue  # Preserve any owner's concurrent edit, including on rollback.
                if snapshots[path] is None:
                    path.unlink(missing_ok=True)
                else:
                    restored = snapshots[path]
                    if path == root / RECEIPT and schedule_attempted and auto_update == "off":
                        # An edited task may prevent removal. Keep the owner's
                        # explicit software opt-out even while rolling program
                        # activation back; the retained task must remain inert.
                        opted_out = json.loads(restored)
                        opted_out.update(auto_update=False, automatic_updates=False, updates_configured=True)
                        restored = _json(opted_out)
                    atomic_write(path, restored, expected=writes[path])
            raise
        return {"root": str(root), "python": str(python), "version": source_version,
                "revision": source_revision, "auto_update": automatic_updates, "schedule": schedule,
                "plugin_registration": "preserved_user_changes" if preserve_registration else "available_in_personal_marketplace"}


def set_windows_sending(root: Path, enabled: bool) -> None:
    validate_root(root)
    with install_lock(root):
        path = root / "sending.json"
        atomic_write(path, _json({"enabled": bool(enabled)}), expected=read_source(path))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--uv", type=Path, required=True)
    parser.add_argument("--install-dir", type=Path)
    parser.add_argument("--marketplace-path", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--auto-update", choices=("keep", "on", "off"), default="keep")
    parser.add_argument("--revision")
    parser.add_argument("--update-state-file", type=Path)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Run install-windows.ps1 on Windows")
    root = args.install_dir or local_data_dir() / "Programs/TelegramMCP"
    marketplace = args.marketplace_path or trusted_home() / ".agents/plugins/marketplace.json"
    state_kwargs = {}
    if args.update_state_file is not None:
        if not args.prepare_only or args.install_dir is None or args.marketplace_path is None or args.revision is None or args.auto_update != "keep":
            parser.error("Candidate updates require --prepare-only, explicit destinations/revision, and --auto-update keep")
        expected_receipt, expected_registration = _update_state(
            args.update_state_file.absolute(), root.absolute(), marketplace.absolute())
        state_kwargs = {"expected_receipt": expected_receipt, "registration_snapshot": expected_registration}
    result = install(args.source.resolve(), root, marketplace.absolute(), args.uv.resolve(),
                     auto_update=args.auto_update, revision=args.revision, **state_kwargs)
    print(json.dumps(result, indent=2))
    if not args.prepare_only:
        ready = subprocess.run([result["python"], "-I", "-X", "utf8", "-m", "telegram_search_mcp.cli", "doctor"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if ready.returncode != 0:
            subprocess.run([result["python"], "-I", "-X", "utf8", "-m", "telegram_search_mcp.cli", "auth"], check=True)
        subprocess.run([result["python"], "-I", "-X", "utf8", "-m", "telegram_search_mcp.cli", "doctor", "--connect"], check=True)
    print("Restart your Codex or ChatGPT desktop app. Open Plugins, choose your personal marketplace, "
          "and install Telegram MCP. Start a new chat in Codex or Work. "
          "For Codex CLI, open /plugins after restarting it.")


if __name__ == "__main__":
    main()
