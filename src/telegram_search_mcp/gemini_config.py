"""Fail-closed preflight and verification for Gemini CLI MCP registration."""

from __future__ import annotations

import argparse
import json
import os
import pwd
import stat
from pathlib import Path
from typing import Any, Literal

SERVER = "telegram-search"
ENV_COMMAND = "/usr/bin/env"
SAFE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
SAFE_LANG = "en_US.UTF-8"
SERVER_MODULE = "telegram_search_mcp.server"
TOOLS = (
    "telegram_search_messages",
    "telegram_get_message",
    "telegram_get_context",
    "telegram_get_media",
)
DESCRIPTION = "Local bounded read-only Telegram search (unofficial)"
TIMEOUT_MS = 150_000
RegistrationState = Literal["absent", "refresh", "exact"]


class ConfigError(RuntimeError):
    """A Gemini configuration error that is safe to display to the user."""


def default_config_path() -> Path:
    config_home = Path(os.environ.get("GEMINI_CLI_HOME", Path.home())).expanduser()
    if not config_home.is_absolute():
        raise ConfigError("GEMINI_CLI_HOME must be an absolute path")
    return config_home / ".gemini" / "settings.json"


def registration_state(
    config_path: Path, *, expected_python: str
) -> RegistrationState:
    """Inspect only the managed alias and return the safe next action."""

    python = _validated_python(expected_python)
    arguments = _expected_arguments(python)
    _validate_env_command()
    root = _load_config(config_path)
    servers = root.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise ConfigError("Gemini mcpServers is not a JSON object")

    for alias, value in servers.items():
        if alias == SERVER or not isinstance(value, dict):
            continue
        if (
            value.get("command") == ENV_COMMAND
            and value.get("args") == arguments
        ):
            raise ConfigError(
                "The Telegram MCP launcher is already registered under another alias"
            )

    existing = servers.get(SERVER)
    if existing is None:
        return "absent"
    _validate_existing_entry(existing, arguments)
    return "exact" if _is_safe_entry(existing, arguments) else "refresh"


def verify_registration(config_path: Path, *, expected_python: str) -> None:
    state = registration_state(config_path, expected_python=expected_python)
    if state != "exact":
        raise ConfigError("Gemini did not write the exact bounded Telegram MCP entry")


def verify_absent(config_path: Path) -> None:
    root = _load_config(config_path)
    servers = root.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise ConfigError("Gemini mcpServers is not a JSON object")
    if SERVER in servers:
        raise ConfigError("Gemini telegram-search entry is still configured")


def _validated_python(value: str) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ConfigError("The isolated MCP Python must be an absolute path")
    try:
        link_info = path.lstat()
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except OSError as exc:
        raise ConfigError("The isolated MCP Python does not exist") from exc
    if not (stat.S_ISREG(link_info.st_mode) or stat.S_ISLNK(link_info.st_mode)):
        raise ConfigError("The isolated MCP Python path is not a file or symlink")
    if not stat.S_ISREG(info.st_mode):
        raise ConfigError("The isolated MCP Python target is not a regular file")
    if link_info.st_uid != os.getuid() or info.st_uid not in (0, os.getuid()):
        raise ConfigError("The isolated MCP Python is not safely owned")
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise ConfigError("The isolated MCP Python is group- or world-writable")
    if not os.access(path, os.X_OK):
        raise ConfigError("The isolated MCP Python is not executable")
    return str(path.absolute())


def _validate_env_command() -> None:
    path = Path(ENV_COMMAND)
    try:
        info = path.lstat()
    except OSError as exc:
        raise ConfigError("The system environment launcher is unavailable") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) & 0o022
        or not os.access(path, os.X_OK)
    ):
        raise ConfigError("The system environment launcher is not trusted")


def _expected_arguments(python: str) -> list[str]:
    return [
        "-i",
        f"HOME={pwd.getpwuid(os.getuid()).pw_dir}",
        f"PATH={SAFE_PATH}",
        f"LANG={SAFE_LANG}",
        python,
        "-I",
        "-m",
        SERVER_MODULE,
    ]


def _validate_existing_entry(value: Any, arguments: list[str]) -> None:
    if not isinstance(value, dict):
        raise ConfigError("The existing telegram-search entry is not a JSON object")
    if value.get("command") != ENV_COMMAND:
        raise ConfigError(
            "An existing telegram-search entry points to a different command"
        )
    if any(key in value for key in ("url", "httpUrl", "serverUrl")):
        raise ConfigError("The existing telegram-search entry uses a remote transport")
    if value.get("args") != arguments:
        raise ConfigError(
            "The existing telegram-search entry lacks the exact isolated launcher"
        )
    if value.get("env") not in (None, {}):
        raise ConfigError("The existing telegram-search entry has unexpected environment")
    if value.get("cwd") is not None:
        raise ConfigError(
            "The existing telegram-search entry has an unexpected working directory"
        )


def _is_safe_entry(value: dict[str, Any], arguments: list[str]) -> bool:
    return (
        value.get("command") == ENV_COMMAND
        and value.get("args") == arguments
        and value.get("timeout") == TIMEOUT_MS
        and value.get("trust") is False
        and value.get("description") == DESCRIPTION
        and value.get("includeTools") == list(TOOLS)
        and not any(
            key in value for key in ("url", "httpUrl", "serverUrl", "cwd")
        )
        and value.get("env") in (None, {})
    )


def _load_config(path: Path) -> dict[str, Any]:
    _validate_parent(path)
    if path.is_symlink():
        raise ConfigError("Refusing a symlinked Gemini configuration")
    if not path.exists():
        return {}
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ConfigError("Gemini configuration is not a regular file")
    if info.st_uid != os.getuid():
        raise ConfigError("Gemini configuration is not owned by the current user")
    if info.st_nlink != 1:
        raise ConfigError("Refusing a hard-linked Gemini configuration")
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise ConfigError("Gemini configuration is group- or world-writable")
    try:
        source = path.read_text(encoding="utf-8")
        normalized = _strip_json_comments(source)
        parsed = json.loads(
            normalized,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ConfigError) as exc:
        raise ConfigError("Gemini configuration is not valid comment JSON") from exc
    if not isinstance(parsed, dict):
        raise ConfigError("Gemini configuration root is not a JSON object")
    return parsed


def _validate_parent(path: Path) -> None:
    parent = path.parent
    if parent.is_symlink():
        raise ConfigError("Refusing a symlinked Gemini configuration directory")
    if parent.exists():
        if not parent.is_dir():
            raise ConfigError("Gemini configuration parent is not a directory")
        if parent.stat().st_uid != os.getuid():
            raise ConfigError(
                "Gemini configuration directory is not owned by the current user"
            )


def _strip_json_comments(source: str) -> str:
    characters = list(source)
    index = 0
    in_string = False
    escaped = False
    while index < len(characters):
        character = characters[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if character == '"':
            in_string = True
            index += 1
            continue
        if character == "/" and index + 1 < len(characters):
            following = characters[index + 1]
            if following == "/":
                characters[index] = characters[index + 1] = " "
                index += 2
                while index < len(characters) and characters[index] not in "\r\n":
                    characters[index] = " "
                    index += 1
                continue
            if following == "*":
                characters[index] = characters[index + 1] = " "
                index += 2
                while index + 1 < len(characters) and not (
                    characters[index] == "*" and characters[index + 1] == "/"
                ):
                    if characters[index] not in "\r\n":
                        characters[index] = " "
                    index += 1
                if index + 1 >= len(characters):
                    raise ConfigError("Gemini configuration has an open block comment")
                characters[index] = characters[index + 1] = " "
                index += 2
                continue
        index += 1

    return "".join(characters)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError("Gemini configuration contains a duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ConfigError(f"Unsupported JSON numeric constant: {value}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode", choices=("preflight", "verify", "verify-absent")
    )
    parser.add_argument("--expected-python")
    args = parser.parse_args()
    try:
        config = default_config_path()
        if args.mode == "verify-absent":
            verify_absent(config)
            print("absent")
            return
        if not args.expected_python:
            raise ConfigError("--expected-python is required for this mode")
        if args.mode == "preflight":
            print(
                registration_state(
                    config, expected_python=args.expected_python
                )
            )
        else:
            verify_registration(config, expected_python=args.expected_python)
            print("verified")
    except Exception as exc:
        raise SystemExit(f"Gemini configuration was not changed: {exc}") from exc


if __name__ == "__main__":
    main()
