"""Complete an old updater's activation without restoring user-edited settings.

0.6 installs new code but deliberately leaves registrations untouched. This code
runs from the activated version on the next scheduled update or MCP start; it
never runs during imports/staging and never opens a Telegram profile.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from .config_io import atomic_write, read_source
from .installation import installation_lock, read_receipt
from .launchers import current_version, installed_root
from .registration import ALIASES, NAMES, _load, configure, expected_entry

NOTICE = "update-notice.json"
VOICE_TOOLS = {"telegram_list_voice_messages", "telegram_transcribe_voice"}
RESTART_MESSAGE = "Telegram MCP updated. Restart Codex / Gemini CLI to enable voice transcription. Your Telegram login is preserved."


def running_version() -> Path:
    return Path(__file__).resolve().parents[2]


def legacy_entry(client: str, wanted: dict) -> dict:
    """Exact 0.6 generated entry, including the original sending preference."""
    result = dict(wanted)
    key = "enabled_tools" if client == "codex" else "includeTools"
    result[key] = [tool for tool in wanted[key] if tool not in VOICE_TOOLS]
    if client == "gemini":
        result["description"] = (
            "Local Telegram search and optional text/document sending (unofficial)"
            if "telegram_send_message" in result[key]
            else "Local bounded read-only Telegram search (unofficial)"
        )
    return result


def read_notice(root: Path) -> dict | None:
    source = read_source(root / NOTICE)
    return json.loads(source) if source is not None else None


def notify_restart() -> bool:
    if sys.platform != "darwin":
        return False
    # Fixed strings only; no retrieved content, user input or config paths.
    try:
        result = subprocess.run([
            "/usr/bin/osascript", "-e",
            'display notification "Restart Codex / Gemini CLI to enable voice transcription. Telegram login is preserved." with title "Telegram MCP updated"',
        ], capture_output=True, timeout=5, check=False)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def migrate(root: Path, *, notify: bool = False) -> dict:
    with installation_lock(root):
        version = current_version(root)
        if version != running_version():
            return {"status": "inactive_version"}
        receipt = read_receipt(root)
        targets, snapshots, skipped = {}, {}, []
        for client, value in receipt.get("clients", {}).items():
            path = Path(value)
            source = read_source(path)
            entry = _load(client, path, source).get(NAMES[client], {}).get(ALIASES[client])
            wanted = expected_entry(client, str(version / ".venv/bin/python"), root)
            if entry == wanted:
                continue
            if entry != legacy_entry(client, wanted):
                skipped.append(client)
                continue
            targets[client], snapshots[client] = path, source
        if targets:
            # Re-check snapshots INSIDE the client configuration locks as well.
            configure("register", clients=list(targets), python=str(version / ".venv/bin/python"),
                      paths=targets, install_root=root, expected_sources=snapshots)
            before = read_source(root / NOTICE)
            notice = {"version": receipt["version"], "clients": list(targets),
                      "message": RESTART_MESSAGE, "notification_requested": False}
            atomic_write(root / NOTICE, (json.dumps(notice, indent=2) + "\n").encode(), expected=before)
        notice = read_notice(root)
        if notify and notice and not notice.get("notification_requested") and notify_restart():
            before = read_source(root / NOTICE)
            notice["notification_requested"] = True
            atomic_write(root / NOTICE, (json.dumps(notice, indent=2) + "\n").encode(), expected=before)
        return {"status": "migrated" if targets else "unchanged", "clients": list(targets), "skipped": skipped}


def on_mcp_start() -> None:
    root = installed_root()
    if root is None:
        return
    try:
        result = migrate(root, notify=True)
        if result["status"] == "migrated":
            print(RESTART_MESSAGE, file=sys.stderr)
    except (OSError, RuntimeError, ValueError):
        # An active installation or edited config must not break existing reads.
        print("Telegram MCP: client settings could not be refreshed; see tgsearch updates status or rerun the installer.", file=sys.stderr)


if __name__ == "__main__":
    root = installed_root()
    if root is None:
        raise SystemExit("Run activation from an installed copy")
    print(json.dumps(migrate(root, notify=True)))
