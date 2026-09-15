"""Private runtime paths for the local Telegram bridge."""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR_NAME = "TelegramSearchMCPShared"


def app_root() -> Path:
    # Gemini CLI inherits variables from a trusted workspace's .env file.
    # Runtime storage must therefore never be redirected by ambient input.
    from .profile_binding import data_root
    return data_root()


def profile_root(profile: str = "default") -> Path:
    if not profile or not profile.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Invalid profile name")
    return app_root() / "profiles" / profile


def policy_path(profile: str = "default") -> Path:
    return profile_root(profile) / "policy.json"


def database_dir(profile: str = "default") -> Path:
    return profile_root(profile) / "db"


def files_dir(profile: str = "default") -> Path:
    return profile_root(profile) / "files"


def lock_path(profile: str = "default") -> Path:
    return profile_root(profile) / "tdlib.lock"


def ensure_private_dir(path: Path) -> None:
    """Create a non-symlink directory owned by the current user with mode 0700."""

    old_umask = os.umask(0o077)
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    finally:
        os.umask(old_umask)
    resolved = path.resolve(strict=True)
    if path.is_symlink() or resolved != path:
        raise RuntimeError(f"Refusing symlinked runtime directory: {path}")
    stat = path.stat()
    if stat.st_uid != os.getuid():
        raise RuntimeError(f"Runtime directory is not owned by current user: {path}")
    path.chmod(0o700)


def ensure_runtime_layout(profile: str = "default") -> None:
    ensure_private_dir(app_root())
    ensure_private_dir(app_root() / "profiles")
    ensure_private_dir(profile_root(profile))
    ensure_private_dir(database_dir(profile))
    ensure_private_dir(files_dir(profile))
