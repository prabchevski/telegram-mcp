"""Private local profile binding TDLib data to one Telegram account."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import ensure_runtime_layout, policy_path

POLICY_VERSION = 2
SEARCH_SCOPE = "global_cloud_chats"


class PolicyError(RuntimeError):
    pass


@dataclass(slots=True)
class Policy:
    api_id: int
    expected_user_id: int | None = None
    search_scope: str = SEARCH_SCOPE
    version: int = POLICY_VERSION

    @classmethod
    def load(cls, profile: str = "default") -> "Policy":
        path = policy_path(profile)
        if not path.exists():
            raise PolicyError("Setup is incomplete. Run `tgsearch auth` locally.")
        _assert_private_file(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if int(data.get("version", 0)) != POLICY_VERSION:
            raise PolicyError("Unsupported profile version; run `tgsearch auth` locally")
        if data.get("search_scope") != SEARCH_SCOPE:
            raise PolicyError("Unsupported Telegram search scope")
        return cls(
            version=POLICY_VERSION,
            api_id=int(data["api_id"]),
            expected_user_id=(
                int(data["expected_user_id"])
                if data.get("expected_user_id") is not None
                else None
            ),
            search_scope=SEARCH_SCOPE,
        )

    def save(self, profile: str = "default") -> None:
        ensure_runtime_layout(profile)
        _atomic_private_json(
            policy_path(profile),
            {
                "version": self.version,
                "api_id": self.api_id,
                "expected_user_id": self.expected_user_id,
                "search_scope": self.search_scope,
            },
        )

    def bind_account(self, user_id: int) -> None:
        if user_id <= 0:
            raise PolicyError("Telegram user ID must be positive")
        if self.expected_user_id is not None and self.expected_user_id != user_id:
            raise PolicyError("Authorized Telegram account does not match this profile")
        self.expected_user_id = user_id


def new_policy(api_id: int) -> Policy:
    if api_id <= 0:
        raise PolicyError("api_id must be a positive integer")
    return Policy(api_id=api_id)


def _assert_private_file(path: Path) -> None:
    if path.is_symlink():
        raise PolicyError(f"Refusing symlinked profile file: {path}")
    stat = path.stat()
    if stat.st_uid != os.getuid():
        raise PolicyError("Profile file is not owned by current user")
    if stat.st_mode & 0o077:
        raise PolicyError("Profile file permissions must be 0600")


def _atomic_private_json(path: Path, payload: dict[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=".profile-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
        path.chmod(0o600)
    finally:
        if temporary.exists():
            temporary.unlink()
