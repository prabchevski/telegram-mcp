"""Stable entry points resolve an immutable installed version before starting Python."""
from __future__ import annotations

import os
from pathlib import Path
import shlex
import stat
import sys

ROOT_MARKER = ".telegram-search-install-root"
VERSION_MARKER = ".telegram-search-install"
LAUNCHER_NAME = "launch-mcp.command"


def validate_root(root: Path) -> None:
    if not root.is_absolute() or root.resolve() != root:
        raise RuntimeError("Installation root must be an absolute path without symlinks")
    info = root.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise RuntimeError("Installation root is not safely owned")
    marker = root / ROOT_MARKER
    if marker.is_symlink() or not marker.is_file() or marker.read_text() != "telegram-search-mcp\n":
        raise RuntimeError("Unrecognized installation root")


def current_version(root: Path) -> Path:
    validate_root(root)
    link = root / "current"
    if not link.is_symlink():
        raise RuntimeError("The installation has no current version")
    version = link.resolve(strict=True)
    marker = version / VERSION_MARKER
    if version.parent != root or marker.is_symlink() or not marker.is_file() or marker.read_text() != "telegram-search-mcp\n":
        raise RuntimeError("current points outside a managed installation")
    return version


def launcher_text(root: Path, module: str) -> str:
    from .registration import real_home
    from .gemini_config import SAFE_LANG, SAFE_PATH

    # Resolve current before exec, so a later update cannot change sys.path under
    # an already running Python process. Never run through current/.venv/python.
    return (
        "#!/bin/sh\nset -eu\n"
        f"root={shlex.quote(str(root))}\n"
        'version="$(cd "$root/current" && pwd -P)"\n'
        '[ "$(dirname "$version")" = "$root" ] || exit 1\n'
        '[ -f "$version/.telegram-search-install" ] || exit 1\n'
        + "exec /usr/bin/env -i "
        + shlex.join([f"HOME={real_home()}", f"PATH={SAFE_PATH}", f"LANG={SAFE_LANG}"])
        + f' "$version/.venv/bin/python" -I -m {shlex.quote(module)} "$@"\n'
    )


def validate_launcher(root: Path) -> None:
    validate_root(root)
    path = root / LAUNCHER_NAME
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
        raise RuntimeError("Unsafe stable MCP launcher")
    if path.read_text() != launcher_text(root, "telegram_search_mcp.server"):
        raise RuntimeError("The stable MCP launcher was modified")


def installed_root(executable: str | None = None) -> Path | None:
    path = Path(executable or sys.executable)
    version = path.parent.parent.parent
    root = version.parent
    if path.parent.name != "bin" or path.parent.parent.name != ".venv" or not (root / ROOT_MARKER).is_file():
        return None
    validate_root(root)
    if not (version / VERSION_MARKER).is_file():
        return None
    return root


def service_python() -> str:
    root = installed_root()
    return str(current_version(root) / ".venv/bin/python") if root else sys.executable
