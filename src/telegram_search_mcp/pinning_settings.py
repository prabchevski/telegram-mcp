"""Pinning is a separate local opt-in; sending permission does not enable it."""
from __future__ import annotations

import json
from pathlib import Path

from .config_io import atomic_write, read_source
from .launchers import installed_root, validate_root

FEATURE_FILE = "pinning.json"


def pinning_enabled(root: Path | None = None) -> bool:
    root = root if root is not None else installed_root()
    if root is None:
        return False
    validate_root(root)
    source = read_source(root / FEATURE_FILE)
    if source is None:
        return False
    if len(source) > 128:
        raise RuntimeError("Invalid pinning setting")
    data = json.loads(source)
    if data not in ({"enabled": True}, {"enabled": False}) or type(data.get("enabled")) is not bool:
        raise RuntimeError("Invalid pinning setting")
    return data["enabled"]


def set_pinning(root: Path, enabled: bool) -> None:
    """Refresh only generated registrations, retaining owner policy and sending."""
    if type(enabled) is not bool:
        raise ValueError("Pinning preference must be a boolean")
    import sys
    if sys.platform == "win32":
        from .windows_install import set_windows_pinning
        set_windows_pinning(root, enabled)
        return
    from .installation import installation_lock, read_receipt
    from .launchers import current_version
    from .registration import ALIASES, NAMES, _load, configure, expected_entry, policy_preserving_entry

    with installation_lock(root):
        python = str(current_version(root) / ".venv/bin/python")
        paths = {}
        for client, value in read_receipt(root).get("clients", {}).items():
            path = Path(value)
            existing = _load(client, path, read_source(path)).get(NAMES[client], {}).get(ALIASES[client])
            if policy_preserving_entry(client, existing, expected_entry(client, python, root)) is not None:
                paths[client] = path
        if enabled and not paths:
            raise RuntimeError("No unchanged registered clients; register the intended client first")
        path = root / FEATURE_FILE
        before = read_source(path)
        after = (json.dumps({"enabled": enabled}) + "\n").encode()
        atomic_write(path, after, expected=before)
        try:
            if paths:
                configure("register", clients=list(paths), python=python, paths=paths, install_root=root)
        except BaseException:
            if before is None:
                path.unlink()
            else:
                atomic_write(path, before, expected=after)
            raise
