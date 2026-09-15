"""Apply the narrow Telegram MCP safety settings to Codex configuration."""

from __future__ import annotations

import argparse
import os
import re
import stat
import tempfile
import tomllib
from pathlib import Path

SERVER = "telegram_search"
SETTINGS = {
    "enabled_tools": (
        '["telegram_search_messages", "telegram_get_message", '
        '"telegram_get_context", "telegram_get_media"]'
    ),
    "default_tools_approval_mode": '"prompt"',
    "supports_parallel_tool_calls": "false",
    "startup_timeout_sec": "20",
    "tool_timeout_sec": "150",
}
EXPECTED_SETTINGS = {
    "enabled_tools": [
        "telegram_search_messages",
        "telegram_get_message",
        "telegram_get_context",
        "telegram_get_media",
    ],
    "default_tools_approval_mode": "prompt",
    "supports_parallel_tool_calls": False,
    "startup_timeout_sec": 20,
    "tool_timeout_sec": 150,
}


def apply_safety_settings(
    config_path: Path, *, expected_command: str | None = None
) -> None:
    """Update one existing MCP table atomically without logging config contents."""

    if config_path.is_symlink():
        raise RuntimeError("Refusing to replace a symlinked Codex configuration")
    original = config_path.read_text(encoding="utf-8")
    parsed = tomllib.loads(original)
    servers = parsed.get("mcp_servers")
    if not isinstance(servers, dict) or SERVER not in servers:
        raise RuntimeError(f"Codex MCP server {SERVER!r} is not configured")
    server = servers[SERVER]
    if not isinstance(server, dict):
        raise RuntimeError("Telegram MCP configuration is not a table")
    if expected_command is not None and server.get("command") != expected_command:
        raise RuntimeError(
            "An existing telegram_search MCP entry points to a different command"
        )

    lines = original.splitlines(keepends=True)
    header = re.compile(rf"^\s*\[mcp_servers\.{re.escape(SERVER)}\]\s*(?:#.*)?$")
    table_header = re.compile(r"^\s*\[\[?.*")
    starts = [index for index, line in enumerate(lines) if header.match(line.rstrip("\n"))]
    if len(starts) != 1:
        raise RuntimeError("Unable to locate one unambiguous Telegram MCP table")
    start = starts[0]
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if table_header.match(lines[index])
        ),
        len(lines),
    )

    for key, value in SETTINGS.items():
        assignment = re.compile(rf"^\s*{re.escape(key)}\s*=")
        matches = [
            index
            for index in range(start + 1, end)
            if assignment.match(lines[index])
        ]
        if len(matches) > 1:
            raise RuntimeError(f"Duplicate {key!r} in Telegram MCP table")
        rendered = f"{key} = {value}\n"
        if matches:
            lines[matches[0]] = rendered
        else:
            lines.insert(end, rendered)
            end += 1

    updated = "".join(lines)
    verified = tomllib.loads(updated)["mcp_servers"][SERVER]
    for key, expected in EXPECTED_SETTINGS.items():
        if verified.get(key) != expected:
            raise RuntimeError(f"Unable to verify Telegram MCP setting {key!r}")
    _atomic_replace(config_path, updated)


def _atomic_replace(path: Path, content: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=".config.toml.telegram-search-",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        temporary.replace(path)
    except BaseException:
        try:
            os.close(file_descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-command")
    args = parser.parse_args()
    config = Path.home() / ".codex" / "config.toml"
    try:
        apply_safety_settings(config, expected_command=args.expected_command)
    except Exception as exc:
        raise SystemExit(f"Codex configuration was not changed: {exc}") from exc
    print("Codex Telegram MCP safety settings applied.")


if __name__ == "__main__":
    main()
