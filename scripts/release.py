#!/usr/bin/env python3
"""Deterministic source releases with a complete, verified file inventory.

This is an accidental-data guard, not a replacement for reviewing what is sent
to GitHub. Only text source, tests, instructions, installer and build files are
accepted. No application state, native libraries or Python environments ship.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tomllib
import zipfile

PACKAGE_ROOT = "telegram-mcp-macos"
MANIFEST_NAME = "RELEASE_MANIFEST.json"
IGNORED_DIRS = frozenset(
    {".git", ".venv", ".pytest_cache", "__pycache__", "dist", ".ruff_cache", ".mypy_cache"}
)
REQUIRED_FILES = frozenset(
    {"pyproject.toml", "uv.lock", "README.md", "LICENSE", "AGENTS.md", "INSTALL_WITH_CODEX.md", "INSTALL_MACOS.md", "START_HERE.md",
     "UNINSTALL_MACOS.md", "install-macos.command", "uninstall-macos.command", "scripts/release.py",
     "scripts/install.py", "src/telegram_search_mcp/__init__.py"}
)
ROOT_FILES = REQUIRED_FILES | {
    ".gitignore", "NOTICE", "update-macos.command", "uninstall-macos.command"
}
FORBIDDEN_PARTS = frozenset(
    {"profiles", "runtime", "database", "files", "secrets", "credentials", ".ssh",
     ".env", "policy.json", "tdlib.lock", "td.binlog", "td.sqlite", ".DS_Store"}
)
FORBIDDEN_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".session", ".pem", ".key", ".log")
CONTENT_GUARDS = (
    ("absolute personal home path", re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+")),
    ("private key", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")),
    ("GitHub credential", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b")),
    ("Telegram bot credential", re.compile(r"\b[0-9]{7,12}:[A-Za-z0-9_-]{30,}\b")),
    ("literal Telegram API hash", re.compile(r"(?i)(?:api_hash|api.hash)[\"']?\s*[:=]\s*[\"'][a-f0-9]{32}[\"']")),
)


class ReleaseError(ValueError):
    """The source tree or archive is unsuitable for distribution."""


def allowed_path(name: str) -> bool:
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or ".." in path.parts or "\\" in name or name != path.as_posix():
        return False
    if name in ROOT_FILES:
        return True
    if len(path.parts) == 1 and path.suffix == ".md":
        return True
    if path.parts[:2] == ("src", "telegram_search_mcp") and path.suffix == ".py":
        return True
    if path.parts[0] == "tests" and path.suffix == ".py":
        return path.name.startswith("test_") or path.name in {"conftest.py", "__init__.py"}
    if path.parts[0] == "scripts" and path.suffix in {".sh", ".py"}:
        return True
    return path.parts[:2] == (".github", "workflows") and path.suffix in {".yml", ".yaml"}


def check_file(name: str, data: bytes) -> None:
    path = PurePosixPath(name)
    if any(part in FORBIDDEN_PARTS or part.startswith(".env.") for part in path.parts):
        raise ReleaseError(f"Forbidden runtime or credential path: {name}")
    if name.endswith(FORBIDDEN_SUFFIXES) or not allowed_path(name):
        raise ReleaseError(f"File is outside the release allowlist: {name}")
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ReleaseError(f"Only UTF-8 source files may ship: {name}") from error
    if "\x00" in content:
        raise ReleaseError(f"Binary data may not ship: {name}")
    for label, pattern in CONTENT_GUARDS:
        if pattern.search(content):
            # Do not print the matched content: it may be a secret.
            raise ReleaseError(f"Potential {label} in {name}; review and remove it")


def file_mode(name: str) -> int:
    return 0o755 if name.endswith((".command", ".sh")) else 0o644


def inventory(source: Path) -> dict[str, bytes]:
    source = source.resolve()
    payload: dict[str, bytes] = {}
    existing_manifest: bytes | None = None
    for directory, dirnames, filenames in os.walk(source, followlinks=False):
        parent = Path(directory)
        for dirname in list(dirnames):
            child = parent / dirname
            if dirname in IGNORED_DIRS:
                dirnames.remove(dirname)
            elif child.is_symlink():
                raise ReleaseError(f"Symlink directory may not ship: {child.relative_to(source)}")
        for filename in filenames:
            child = parent / filename
            name = child.relative_to(source).as_posix()
            if child.is_symlink() or not child.is_file():
                raise ReleaseError(f"Only regular files may ship: {name}")
            data = child.read_bytes()
            if name == MANIFEST_NAME:
                existing_manifest = data
                continue
            check_file(name, data)
            payload[name] = data
    missing = REQUIRED_FILES - payload.keys()
    if missing:
        raise ReleaseError(f"Required release files are missing: {', '.join(sorted(missing))}")
    project = tomllib.loads(payload["pyproject.toml"].decode())["project"]
    version = project["version"]
    if project["name"] != "telegram-search-mcp" or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ReleaseError("Expected unified telegram-search-mcp package and a numeric X.Y.Z version")
    init = payload["src/telegram_search_mcp/__init__.py"].decode()
    if not re.search(rf'__version__\s*=\s*[\"\']{re.escape(version)}[\"\']', init):
        raise ReleaseError("Package __version__ disagrees with pyproject.toml")
    if existing_manifest is not None:
        try:
            matches = json.loads(existing_manifest) == manifest_for(payload)
        except ValueError as error:
            raise ReleaseError("Invalid manifest in extracted source bundle") from error
        if not matches:
            raise ReleaseError("Extracted source bundle differs from its release manifest")
    return dict(sorted(payload.items()))


def manifest_for(payload: dict[str, bytes]) -> dict:
    project = tomllib.loads(payload["pyproject.toml"].decode())["project"]
    return {
        "format": 1,
        "package": project["name"],
        "version": project["version"],
        "archive_root": PACKAGE_ROOT,
        "files": [
            {"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "mode": f"{file_mode(name):04o}"}
            for name, data in sorted(payload.items())
        ],
    }


def manifest_bytes(payload: dict[str, bytes]) -> bytes:
    return (json.dumps(manifest_for(payload), indent=2, ensure_ascii=False) + "\n").encode()


def write_zip(destination: Path, payload: dict[str, bytes]) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(payload.items()):
            entry = zipfile.ZipInfo(f"{PACKAGE_ROOT}/{name}", date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = (stat.S_IFREG | file_mode(name)) << 16
            archive.writestr(entry, data, compresslevel=9)


def verify_archive(archive_path: Path) -> dict:
    payload: dict[str, bytes] = {}
    with zipfile.ZipFile(archive_path) as archive:
        if len(archive.infolist()) > 1000:
            raise ReleaseError("Unexpectedly large archive inventory")
        if sum(item.file_size for item in archive.infolist()) > 20 * 1024 * 1024:
            raise ReleaseError("Unexpectedly large source archive")
        for entry in archive.infolist():
            prefix = PACKAGE_ROOT + "/"
            if not entry.filename.startswith(prefix):
                raise ReleaseError("Archive has an unexpected root directory")
            name = entry.filename[len(prefix):]
            if name in payload or not name or entry.is_dir():
                raise ReleaseError("Archive has duplicate or unexpected directory entries")
            if entry.file_size > 5 * 1024 * 1024:
                raise ReleaseError(f"Oversized source file: {name}")
            mode = entry.external_attr >> 16
            if not stat.S_ISREG(mode) or stat.S_IMODE(mode) != file_mode(name):
                raise ReleaseError(f"Unexpected file type or permissions: {name}")
            data = archive.read(entry)
            if name != MANIFEST_NAME:
                check_file(name, data)
            payload[name] = data
    try:
        manifest = json.loads(payload.pop(MANIFEST_NAME))
    except (KeyError, ValueError) as error:
        raise ReleaseError("Archive lacks a valid release manifest") from error
    if REQUIRED_FILES - payload.keys():
        raise ReleaseError("Archive is missing required source files")
    expected = manifest_for(payload)
    if manifest != expected:
        raise ReleaseError("Archive content does not match its release manifest")
    return expected


def build(source: Path, output: Path) -> Path:
    payload = inventory(source)
    manifest = manifest_bytes(payload)
    version = manifest_for(payload)["version"]
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / f"{PACKAGE_ROOT}-v{version}.zip"
    temporary = archive_path.with_suffix(".zip.tmp")
    try:
        write_zip(temporary, {**payload, MANIFEST_NAME: manifest})
        verify_archive(temporary)
        temporary.replace(archive_path)
    finally:
        temporary.unlink(missing_ok=True)
    (output / f"{archive_path.stem}.manifest.json").write_bytes(manifest)
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    (output / f"{archive_path.name}.sha256").write_text(f"{digest}  {archive_path.name}\n")
    return archive_path


def verify_wheel(wheel_path: Path, source: Path) -> int:
    """Require exactly the package source plus standard wheel metadata."""
    payload = inventory(source)
    version = manifest_for(payload)["version"]
    metadata_root = f"telegram_search_mcp-{version}.dist-info/"
    source_files = {
        name.removeprefix("src/"): data
        for name, data in payload.items() if name.startswith("src/")
    }
    metadata_names = {"METADATA", "WHEEL", "RECORD", "entry_points.txt"}
    license_name = metadata_root + "licenses/LICENSE"
    found: set[str] = set()
    with zipfile.ZipFile(wheel_path) as wheel:
        for entry in wheel.infolist():
            name = entry.filename
            if name in found:
                raise ReleaseError("Wheel contains duplicate entries")
            found.add(name)
            data = wheel.read(entry)
            if name in source_files:
                if data != source_files[name]:
                    raise ReleaseError(f"Wheel differs from reviewed source: {name}")
            elif name == license_name:
                if data != payload["LICENSE"]:
                    raise ReleaseError("Wheel license differs from the repository LICENSE")
            elif name.startswith(metadata_root) and name.removeprefix(metadata_root) in metadata_names:
                check_file("README.md", data)
            else:
                raise ReleaseError(f"Unexpected wheel entry: {name}")
    if source_files.keys() - found or metadata_root + "METADATA" not in found:
        raise ReleaseError("Wheel is missing package files or metadata")
    if license_name not in found:
        raise ReleaseError("Wheel is missing LICENSE")
    return len(found)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("audit", "build"):
        sub = commands.add_parser(command)
        sub.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
        if command == "build":
            sub.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("archive", type=Path)
    wheel = commands.add_parser("verify-wheel")
    wheel.add_argument("wheel", type=Path)
    wheel.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        if args.command == "build":
            print(build(args.source, args.output))
        elif args.command == "audit":
            payload = inventory(args.source)
            print(f"Source inventory passed: {len(payload)} text files; no excluded runtime files are included.")
        elif args.command == "verify":
            manifest = verify_archive(args.archive)
            print(f"Archive verified: {manifest['package']} {manifest['version']}, {len(manifest['files'])} source files.")
        else:
            print(f"Wheel verified: {verify_wheel(args.wheel, args.source)} source and metadata files.")
    except (ReleaseError, OSError, zipfile.BadZipFile) as error:
        print(f"Release refused: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
