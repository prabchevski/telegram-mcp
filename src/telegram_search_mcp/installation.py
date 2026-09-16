"""Transactional versioned installation shared by the installer and updater."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import uuid

from .config_io import atomic_write, read_source, validate_path
from .launchers import LAUNCHER_NAME, ROOT_MARKER, VERSION_MARKER, current_version, launcher_text, validate_root
from .registration import ALIASES, NAMES, _load, _prepare, configure, default_path, launcher_arguments, real_home, recognized_entry
from .profile_binding import adopt, plan_adoption

RECEIPT = "installation.json"


def safe_environment() -> dict[str, str]:
    return {"HOME": str(real_home()), "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8"}


def default_root() -> Path:
    original = real_home() / "Applications" / "TelegramSearchMCP"
    # Version 0.2 used this entire directory as its virtual environment/source.
    # Never move it underneath a running legacy process.
    manifest = original / "pyproject.toml"
    if manifest.is_file() and not (original / ROOT_MARKER).exists():
        if tomllib.loads(manifest.read_text()).get("project", {}).get("name") == "codex-telegram-search-mcp":
            return original.with_name("TelegramSearchMCPShared")
    return original


def prepare_root(root: Path) -> None:
    if not root.is_absolute() or root.resolve() != root:
        raise RuntimeError("The installation root must be absolute and must not traverse symlinks")
    for path in (root, *root.parents):
        if path.exists():
            info = path.stat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.getuid()) or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX):
                raise RuntimeError("Unsafe installation directory ownership or permissions")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not (root / ROOT_MARKER).exists():
        if any(root.iterdir()):
            raise RuntimeError("The installation directory contains unrelated files; choose an empty directory")
        atomic_write(root / ROOT_MARKER, b"telegram-search-mcp\n", expected=None)
    validate_root(root)
    if (root / "current").exists() or (root / "current").is_symlink():
        current_version(root)


@contextlib.contextmanager
def installation_lock(root: Path):
    from .service import open_private_file
    prepare_root(root)
    descriptor = open_private_file(root / "installation.lock")
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another installation or update is in progress; retry later") from exc
        yield
    finally:
        os.close(descriptor)


def read_receipt(root: Path) -> dict:
    validate_root(root)
    source = read_source(root / RECEIPT)
    if source is None:
        return {}
    if len(source) > 16384:
        raise RuntimeError("Installation receipt is too large")
    data = json.loads(source)
    if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("clients"), dict):
        raise RuntimeError("Unsupported installation receipt")
    for client, value in data["clients"].items():
        if client not in ALIASES or not isinstance(value, str):
            raise RuntimeError("Invalid client in installation receipt")
        validate_path(Path(value))
    if data.get("revision") is not None and not re.fullmatch(r"[0-9a-f]{40}", data["revision"]):
        raise RuntimeError("Invalid installed revision")
    return data


def source_revision(source: Path) -> str | None:
    revision = source / "SOURCE_REVISION"
    if revision.exists():
        value = revision.read_text().strip()
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            raise RuntimeError("Invalid source revision")
        return value
    if (source / ".git").exists() and shutil.which("git"):
        result = subprocess.run(["git", "-C", str(source), "status", "--porcelain"], capture_output=True, text=True, timeout=15)
        if result.returncode == 0 and not result.stdout:
            return subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True, timeout=15).strip()
    return None


def _write_launcher(path: Path, content: str) -> None:
    before = read_source(path)
    if before != content.encode():
        atomic_write(path, content.encode(), expected=before)
    path.chmod(0o700)


def _stage_version(source: Path, root: Path, uv: str, python: str) -> Path:
    project = tomllib.loads((source / "pyproject.toml").read_text())["project"]
    if project.get("name") != "telegram-search-mcp" or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", project.get("version", "")):
        raise RuntimeError("Unrecognized package or version")
    version = Path(tempfile.mkdtemp(prefix=project["version"] + "-", dir=root))
    try:
        files = [source / name for name in ("pyproject.toml", "uv.lock", "README.md", "LICENSE", "install-macos.command", "uninstall-macos.command", "scripts/install.py")]
        files += list((source / "src").rglob("*.py"))
        for item in files:
            if item.is_symlink() or not item.is_file() or item.resolve() != item:
                raise RuntimeError("Installation source contains a symlink or missing file")
            destination = version / item.relative_to(source)
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copyfile(item, destination)
        subprocess.run([uv, "sync", "--project", str(version), "--frozen", "--no-dev", "--python", python],
                       check=True, env=safe_environment(), timeout=600)
        interpreter = str(version / ".venv/bin/python")
        subprocess.run([interpreter, "-I", "-m", "telegram_search_mcp.native_runtime", str(version)],
                       check=True, env=safe_environment(), timeout=4200)
        import shlex
        for name, module in (("tgsearch", "telegram_search_mcp.cli"), ("client-config", "telegram_search_mcp.registration")):
            _write_launcher(version / name, "#!/bin/sh\nexec " + shlex.join(["/usr/bin/env", *launcher_arguments(interpreter, module)]) + ' "$@"\n')
        (version / VERSION_MARKER).write_text("telegram-search-mcp\n")
        # Verify the staged package before changing any live registration/link.
        subprocess.run([interpreter, "-I", "-c", "from telegram_search_mcp.server import create_server; from telegram_search_mcp import __version__"],
                       check=True, env=safe_environment(), timeout=30)
        return version
    except BaseException:
        shutil.rmtree(version)
        raise


def install(source: Path, root: Path, *, uv: str, python: str, clients: list[str], paths: dict[str, Path],
            replace_legacy: bool = False, migrate_profile: str = "none", auto_update: str = "keep",
            revision: str | None = None, registration_snapshot: dict[str, bytes] | None = None,
            expected_receipt: bytes | None = None) -> Path:
    source = source.resolve(strict=True)
    if auto_update not in ("on", "off", "keep"):
        raise ValueError("Invalid automatic update setting")
    revision = revision or source_revision(source)
    if revision is not None and not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise RuntimeError("Invalid source revision")
    with installation_lock(root):
        previous = read_receipt(root)
        if expected_receipt is not None and read_source(root / RECEIPT) != expected_receipt:
            raise RuntimeError("Installation settings changed while downloading; retry later")
        chosen = dict(previous.get("clients", {}))
        for client in clients:
            chosen[client] = str(paths.get(client, default_path(client)))
        if replace_legacy:
            # Refresh an already configured second client, without enabling an absent one.
            for client in ALIASES:
                path = paths.get(client, default_path(client))
                config = _load(client, path, read_source(path))
                if any(recognized_entry(value, allow_legacy=True) for value in config.get(NAMES[client], {}).values()):
                    chosen[client] = str(path)
        targets = {client: Path(value) for client, value in chosen.items()}
        if registration_snapshot is not None:
            if set(registration_snapshot) != set(targets):
                raise RuntimeError("Client registrations changed while downloading")
            for client, path in targets.items():
                if read_source(path) != registration_snapshot[client]:
                    raise RuntimeError("Client registration changed while downloading; it was not restored")
        adoption = plan_adoption(migrate_profile)
        version = _stage_version(source, root, uv, python)
        # Stable launchers contain no workspace paths or arguments supplied by a model.
        _write_launcher(root / LAUNCHER_NAME, launcher_text(root, "telegram_search_mcp.server"))
        _write_launcher(root / "update.command", launcher_text(root, "telegram_search_mcp.updater"))
        _write_launcher(root / "authorize.command", launcher_text(root, "telegram_search_mcp.cli").replace('"$@"', "auth"))
        for client, path in targets.items():
            _prepare(client, path, str(version / ".venv/bin/python"), "register", replace_legacy, root)
        if adoption:
            adopt(adoption)
        before_configs = {path: read_source(path) for path in targets.values()}
        previous_link = (root / "current").readlink() if (root / "current").is_symlink() else None
        before_receipt = read_source(root / RECEIPT)
        if registration_snapshot is None:
            backups = configure("register", clients=list(targets), python=str(version / ".venv/bin/python"),
                                paths=targets, replace_legacy=replace_legacy, install_root=root)
        else:
            for client, path in targets.items():
                if read_source(path) != registration_snapshot[client]:
                    raise RuntimeError("Client registration changed during update; activation was cancelled")
            backups = []
        after_configs = {path: read_source(path) for path in targets.values()}
        project = tomllib.loads((version / "pyproject.toml").read_text())["project"]
        enabled = previous.get("auto_update", False) if auto_update == "keep" else auto_update == "on"
        receipt = {"schema": 1, "version": project["version"], "revision": revision,
                   "clients": {client: str(path) for client, path in targets.items()}, "auto_update": bool(enabled)}
        link = root / (".current-" + uuid.uuid4().hex)
        try:
            link.symlink_to(version.name, target_is_directory=True)
            os.replace(link, root / "current")
            atomic_write(root / RECEIPT, (json.dumps(receipt, indent=2) + "\n").encode(), expected=before_receipt)
        except BaseException:
            if (root / "current").is_symlink() and (root / "current").readlink() == Path(version.name):
                if previous_link is None:
                    (root / "current").unlink()
                else:
                    link.unlink(missing_ok=True)
                    link.symlink_to(previous_link, target_is_directory=True)
                    os.replace(link, root / "current")
            for path, before in reversed(list(before_configs.items())):
                after = after_configs[path]
                if before == after:
                    continue
                if read_source(path) != after:
                    raise RuntimeError("Activation failed and client settings changed concurrently; restore the private backup manually")
                if before is None:
                    path.unlink()
                else:
                    atomic_write(path, before, expected=after)
            raise
        finally:
            link.unlink(missing_ok=True)
        if auto_update != "keep":
            from .updater import configure_schedule
            configure_schedule(root, bool(enabled))
        for backup in backups:
            print(f"Private configuration backup: {backup}")
        print(f"Installed {project['version']} at {version}")
        print(f"Automatic updates: {'checked main, once a day' if enabled else 'off'}")
        print(f"Management command: {root / 'current/tgsearch'}")
        if adoption:
            print(f"Reused the local {adoption} profile and Keychain; no session data was copied.")
        return version


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--uv", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--install-dir", type=Path)
    parser.add_argument("--clients", choices=("codex", "gemini", "both", "none"), default="codex")
    parser.add_argument("--codex-config", type=Path)
    parser.add_argument("--gemini-config", type=Path)
    parser.add_argument("--replace-legacy", action="store_true")
    parser.add_argument("--migrate-profile", choices=("auto", "codex", "gemini", "none"), default="none")
    parser.add_argument("--auto-update", choices=("on", "off", "keep"), default="keep")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    clients = list(ALIASES) if args.clients == "both" else ([] if args.clients == "none" else [args.clients])
    paths = {client: path for client in ALIASES if (path := getattr(args, client + "_config")) is not None}
    version = install(args.source, args.install_dir or default_root(), uv=args.uv, python=args.python, clients=clients,
                      paths=paths, replace_legacy=args.replace_legacy, migrate_profile=args.migrate_profile, auto_update=args.auto_update)
    command = str(version / "tgsearch")
    if args.prepare_only:
        print("Authorization was skipped. Use tgsearch doctor to check a saved login, or run tgsearch auth in your private Terminal.")
    else:
        ready = subprocess.run([command, "doctor"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        if ready.returncode:
            subprocess.run([command, "auth"], check=True)
        subprocess.run([command, "doctor", "--connect"], check=True, timeout=150)
    print("Restart the selected MCP clients to load the new registration.")


if __name__ == "__main__":
    main()
