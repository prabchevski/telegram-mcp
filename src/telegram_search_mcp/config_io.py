"""Private, atomic client configuration edits with recoverable backups."""
from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


def validate_path(path: Path) -> None:
    if not path.is_absolute():
        raise RuntimeError("Configuration path must be absolute")
    if path.is_symlink() or path.parent.is_symlink():
        raise RuntimeError("Refusing symlinked configuration or directory")
    ancestor = path.parent
    while not ancestor.exists():
        ancestor = ancestor.parent
    info = ancestor.stat()
    if not ancestor.is_dir() or info.st_uid not in (os.getuid(), 0):
        raise RuntimeError("Configuration directory is not safely owned")
    if stat.S_IMODE(info.st_mode) & 0o022 and not info.st_mode & stat.S_ISVTX:
        raise RuntimeError("Configuration directory is group- or world-writable")
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RuntimeError("Configuration must be a regular, non-hard-linked file")
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
            raise RuntimeError("Configuration is not privately owned or is writable by others")


def read_source(path: Path) -> bytes | None:
    validate_path(path)
    return path.read_bytes() if path.exists() else None


def atomic_write(path: Path, content: bytes, *, expected: bytes | None) -> Path | None:
    """Compare with the inspected source, then keep a private byte-exact backup."""
    if read_source(path) != expected:
        raise RuntimeError("Configuration changed during registration; retry safely")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = None
    if expected is not None:
        descriptor, name = tempfile.mkstemp(prefix=path.name + ".telegram-search-backup-", dir=path.parent)
        backup = Path(name)
        with os.fdopen(descriptor, "wb") as output:
            output.write(expected)
            output.flush()
            os.fsync(output.fileno())
    descriptor, name = tempfile.mkstemp(prefix=".telegram-search-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return backup
