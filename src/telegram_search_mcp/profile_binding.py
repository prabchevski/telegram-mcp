"""Explicit, local adoption of a compatible legacy profile without copying a session."""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile

SHARED_APP = "TelegramSearchMCPShared"
SHARED_SERVICE = "local.unofficial-telegram-search-mcp-shared"
LEGACY = {
    "codex": ("TelegramSearchMCP", ("local.unofficial-telegram-search-mcp", "com.openai.codex.telegram-search-mcp")),
    "gemini": ("TelegramSearchMCPGemini", ("local.unofficial-telegram-search-mcp-gemini",)),
}


def shared_root() -> Path:
    return Path.home() / "Library" / "Application Support" / SHARED_APP


def _private(path: Path, *, directory: bool = False) -> None:
    if not path.is_absolute() or path.resolve() != path:
        raise RuntimeError("Profile paths must be absolute and must not traverse symlinks")
    info = path.lstat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("Profile data must be private to the current user")
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise RuntimeError("Expected a private profile directory")
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise RuntimeError("Expected a private regular profile file")


def read_binding() -> str | None:
    path = shared_root() / "profile-source.json"
    if not path.exists() and not path.is_symlink():
        return None
    _private(path)
    if path.stat().st_size > 1024:
        raise RuntimeError("Invalid profile binding")
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or set(data) != {"schema", "source"} or data["schema"] != 1 or data["source"] not in LEGACY:
        raise RuntimeError("Unsupported profile binding")
    return data["source"]


def data_root() -> Path:
    source = read_binding()
    if source is None:
        return shared_root()
    root = shared_root().parent / LEGACY[source][0]
    _private(root, directory=True)
    return root


def credential_services() -> tuple[str, ...]:
    source = read_binding()
    return LEGACY[source][1] if source else (SHARED_SERVICE,)


def inspect_legacy(source: str) -> dict | None:
    root = shared_root().parent / LEGACY[source][0]
    profile = root / "profiles" / "default"
    policy = profile / "policy.json"
    if not policy.exists() and not policy.is_symlink():
        return None
    for directory in (root, root / "profiles", profile, profile / "db"):
        _private(directory, directory=True)
    _private(policy)
    if policy.stat().st_size > 8192:
        raise RuntimeError("Legacy profile policy is too large")
    data = json.loads(policy.read_text())
    if (data.get("version") != 2 or data.get("search_scope") != "global_cloud_chats"
            or type(data.get("api_id")) is not int or data["api_id"] <= 0
            or type(data.get("expected_user_id")) is not int or data["expected_user_id"] <= 0):
        raise RuntimeError(f"The {source} profile is incomplete or incompatible; sign in separately")
    return {"source": source, "user_id": data["expected_user_id"], "profile": profile}


def plan_adoption(selection: str = "auto") -> str | None:
    if selection == "none":
        return None
    if selection not in {"auto", *LEGACY}:
        raise ValueError("Choose auto, codex, gemini, or none for profile migration")
    bound = read_binding()
    if bound:
        if selection not in ("auto", bound):
            raise RuntimeError("A legacy profile is already selected; automatic account switching is refused")
        return None
    shared_policy = shared_root() / "profiles" / "default" / "policy.json"
    if shared_policy.exists() or shared_policy.is_symlink():
        # Never switch away from a profile the owner already started configuring.
        if selection != "auto":
            raise RuntimeError("A shared profile already exists; it will not be replaced")
        return None
    sources = list(LEGACY) if selection == "auto" else [selection]
    candidates = [candidate for source in sources if (candidate := inspect_legacy(source)) is not None]
    if len({item["user_id"] for item in candidates}) > 1:
        raise RuntimeError("Both Codex and Gemini profiles exist. Choose --migrate-profile codex or gemini; accounts will not be merged")
    if not candidates:
        if selection != "auto":
            raise RuntimeError(f"No authorized {selection} profile was found")
        return None
    return candidates[0]["source"]


def adopt(source: str) -> None:
    """Select one fixed namespace while its existing database is not in use."""
    from .paths import ensure_private_dir
    from .service import open_private_file, profile_exclusive

    ensure_private_dir(shared_root())
    # Serialize selection and exclude the still-unbound shared service/auth flow.
    fd = open_private_file(shared_root() / "profile-migration.lock")
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another profile migration is in progress; retry later") from exc
        if read_binding() == source:
            return
        with profile_exclusive():
            if plan_adoption(source) is None:
                return
            candidate = inspect_legacy(source)
            assert candidate is not None
            lock = candidate["profile"] / "tdlib.lock"
            legacy_fd = open_private_file(lock)
            try:
                try:
                    fcntl.flock(legacy_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise RuntimeError("The old Telegram profile is in use. Restart/close its MCP clients and retry; no session was copied or stopped") from exc
                # All runtime lookups subsequently use this exact fixed namespace.
                # Credentials stay in its Keychain service and never pass through us.
                target = shared_root() / "profile-source.json"
                if target.exists() or target.is_symlink():
                    raise RuntimeError("Profile binding changed during migration")
                descriptor, name = tempfile.mkstemp(prefix=".profile-source-", dir=shared_root())
                try:
                    with os.fdopen(descriptor, "w") as output:
                        json.dump({"schema": 1, "source": source}, output)
                        output.write("\n")
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(name, target)
                finally:
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(name)
            finally:
                os.close(legacy_fd)
    finally:
        os.close(fd)
