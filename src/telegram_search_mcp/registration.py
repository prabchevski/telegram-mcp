"""Bounded client registration; no Telegram access or credentials are needed."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import pwd
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

from .config_io import atomic_write, read_source, validate_path
from .gemini_config import (
    DESCRIPTION, ENV_COMMAND, SAFE_LANG, SAFE_PATH, SERVER_MODULE, TIMEOUT_MS,
    TOOLS, _load_config, _validate_env_command, _validated_python,
)

ALIASES = {"codex": "telegram_search", "gemini": "telegram-search"}
NAMES = {"codex": "mcp_servers", "gemini": "mcpServers"}
LEGACY_PROJECTS = {"gemini-telegram-search-mcp", "codex-telegram-search-mcp"}
POLICY_KEYS = {
    "codex": frozenset({"default_tools_approval_mode", "supports_parallel_tool_calls"}),
    "gemini": frozenset({"trust"}),
}


def real_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def launcher_arguments(python: str, module: str = SERVER_MODULE) -> list[str]:
    return ["-i", f"HOME={real_home()}", f"PATH={SAFE_PATH}", f"LANG={SAFE_LANG}", python, "-I", "-m", module]


def expected_entry(client: str, python: str, install_root: Path | None = None, *,
                   enable_sending: bool | None = None) -> dict[str, Any]:
    args = launcher_arguments(python)
    if install_root is not None:
        from .launchers import LAUNCHER_NAME, validate_launcher
        validate_launcher(install_root)
        args = args[:4] + ["/bin/sh", str(install_root / LAUNCHER_NAME)]
    entry: dict[str, Any] = {"command": ENV_COMMAND, "args": args}
    if client == "codex":
        entry.update(enabled_tools=list(TOOLS), default_tools_approval_mode="prompt", supports_parallel_tool_calls=False, startup_timeout_sec=20, tool_timeout_sec=150)
    else:
        entry.update(timeout=TIMEOUT_MS, trust=False, description=DESCRIPTION, includeTools=list(TOOLS))
    if install_root is not None or enable_sending is not None:
        from .sending_settings import OUTGOING_TOOLS, sending_enabled
        sending = enable_sending if enable_sending is not None else sending_enabled(install_root)
        if sending:
            entry["enabled_tools" if client == "codex" else "includeTools"].extend(OUTGOING_TOOLS)
            if client == "gemini":
                entry["description"] = "Local Telegram history, free public posts search, files, voice, drafts and optional scheduled sending (unofficial)"
    return entry


def _retain_policy(client: str, existing: dict, wanted: dict) -> dict:
    result = dict(wanted)
    for key in POLICY_KEYS[client]:
        if key in existing:
            result[key] = existing[key]
        else:
            result.pop(key, None)
    return result


def policy_preserving_entry(client: str, existing: Any, wanted: dict, *,
                            compatible_entries: tuple[dict, ...] = ()) -> dict | None:
    """Refresh only a complete generated registration, retaining owner policy.

    Removing a policy field is also a preference: do not reintroduce its default.
    Every other field must match a known generated entry exactly, including the
    whole ordered tool list. Custom launchers, filters, disabled connections and
    additional settings therefore remain outside automatic migration.
    """
    if not isinstance(existing, dict):
        return None
    keys = POLICY_KEYS[client]
    bounded = {key: value for key, value in existing.items() if key not in keys}
    if not any(bounded == {key: value for key, value in entry.items() if key not in keys}
               for entry in (wanted, *compatible_entries)):
        return None
    return _retain_policy(client, existing, wanted)


def default_path(client: str) -> Path:
    if client == "codex":
        return Path(os.environ.get("CODEX_HOME", real_home() / ".codex")) / "config.toml"
    return Path(os.environ.get("GEMINI_CLI_HOME", real_home())) / ".gemini" / "settings.json"


def _load(client: str, path: Path, source: bytes | None) -> dict:
    try:
        root = tomllib.loads((source or b"").decode()) if client == "codex" else _load_config(path)
    except Exception as exc:
        raise RuntimeError(f"Invalid {client} configuration; contents were not changed") from exc
    servers = root.get(NAMES[client], {})
    if not isinstance(servers, dict):
        raise RuntimeError(f"Invalid {client} MCP server table")
    return root


def _project_name(executable: str) -> str | None:
    path = Path(executable)
    if not path.is_absolute() or path.parent.name != "bin" or path.parent.parent.name != ".venv":
        return None
    manifest = path.parent.parent.parent / "pyproject.toml"
    try:
        validate_path(manifest)
        return tomllib.loads(manifest.read_text())["project"]["name"]
    except (OSError, ValueError, KeyError, RuntimeError):
        return None


def recognized_entry(entry: Any, *, allow_legacy: bool = False) -> bool:
    if not isinstance(entry, dict) or any(key in entry for key in ("url", "httpUrl", "serverUrl", "cwd", "env", "env_vars")):
        return False
    command, args = entry.get("command"), entry.get("args", [])
    if command == ENV_COMMAND and isinstance(args, list):
        if len(args) == 6 and args[:4] == launcher_arguments("")[:4] and args[4] == "/bin/sh" and isinstance(args[5], str):
            from .launchers import LAUNCHER_NAME, validate_launcher
            launcher = Path(args[5])
            try:
                if launcher.name != LAUNCHER_NAME:
                    return False
                validate_launcher(launcher.parent)
            except (OSError, RuntimeError):
                return False
            return True
        # Only launchers generated by these packages qualify; arbitrary env scripts do not.
        if len(args) not in (7, 8) or args[-3:] != ["-I", "-m", SERVER_MODULE]:
            return False
        python = args[-4]
        if not isinstance(python, str):
            return False
        modern = args == launcher_arguments(python)
        old = args == ["-i", f"PATH={SAFE_PATH}", f"LANG={SAFE_LANG}", python, "-I", "-m", SERVER_MODULE]
        name = _project_name(python)
        return (modern and name == "telegram-search-mcp") or (allow_legacy and old and name in LEGACY_PROJECTS)
    return bool(allow_legacy and isinstance(command, str) and Path(command).name == "tgsearch-mcp" and args == [] and _project_name(command) in LEGACY_PROJECTS)


def _registration_refresh_entry(client: str, existing: Any, wanted: dict, *,
                                python: str, install_root: Path | None,
                                replace_legacy: bool) -> dict | None:
    """Normal, explicitly requested registration also retains owner policy.

    Unlike background activation, setup can intentionally switch a recognized
    launcher to the selected installation and change the sending preference.
    Its previous tool list must still be complete and generated by this package.
    """
    if not recognized_entry(existing, allow_legacy=replace_legacy):
        return None
    from .activation import managed_refresh_entry
    for sending in (False, True):
        comparison = expected_entry(client, python, install_root, enable_sending=sending)
        comparison["command"] = existing["command"]
        if "args" in existing:
            comparison["args"] = existing["args"]
        else:
            comparison.pop("args", None)
        if managed_refresh_entry(client, existing, comparison) is not None:
            return _retain_policy(client, existing, wanted)
    # Original 0.2/0.3 registrations could contain only their known executable.
    # Explicit legacy replacement still accepts that exact minimal shape, but
    # never removes an owner filter, disabled flag or additional configuration.
    if (replace_legacy and not recognized_entry(existing)
            and set(existing) <= {"command", "args"} | POLICY_KEYS[client]):
        return _retain_policy(client, existing, wanted)
    return None


def _replace_codex(source: bytes | None, entry: dict | None, alias: str = "telegram_search") -> bytes:
    text = (source or b"").decode()
    lines = text.splitlines(keepends=True)
    # Refuse unusual quoted/dotted spellings rather than risking another TOML table.
    header = re.compile(rf"^\s*\[mcp_servers\.{re.escape(alias)}\]\s*(?:#.*)?$")
    nested = re.compile(rf"^\s*\[mcp_servers\.{re.escape(alias)}\.")
    starts = [i for i, line in enumerate(lines) if header.match(line.rstrip("\r\n"))]
    parsed = tomllib.loads(text)
    present = alias in parsed.get("mcp_servers", {})
    if present and len(starts) != 1:
        raise RuntimeError("Cannot safely locate the Codex Telegram table")
    if any(nested.match(line) for line in lines):
        raise RuntimeError("Codex Telegram table has custom nested settings; remove or review them first")
    if starts:
        start = starts[0]
        end = next((i for i in range(start + 1, len(lines)) if re.match(r"^\s*\[", lines[i])), len(lines))
        del lines[start:end]
    result = "".join(lines)
    if entry is not None:
        result = result.rstrip() + f"\n\n[mcp_servers.{alias}]\n"
        for key, value in entry.items():
            result += f"{key} = {json.dumps(value, ensure_ascii=False)}\n"
    tomllib.loads(result)
    return result.encode()


def _prepare(client: str, path: Path, python: str, mode: str, replace_legacy: bool, install_root: Path | None = None,
             preserve_policy: bool = False) -> tuple[bytes | None, bytes]:
    source = read_source(path)
    root = _load(client, path, source)
    servers = root.get(NAMES[client], {})
    existing = servers.get(ALIASES[client])
    wanted = expected_entry(client, python, install_root)
    if preserve_policy:
        # Called by configure only after its snapshots have been checked inside
        # the configuration locks. Recompute from those current bytes rather
        # than carrying a replacement assembled before acquiring the locks.
        from .activation import managed_refresh_entry
        refreshed = managed_refresh_entry(client, existing, wanted)
        if refreshed is None:
            raise RuntimeError(f"{client}: registration was customized or disconnected; it was not restored")
        wanted = refreshed
    remove_aliases = []
    if ALIASES[client] in servers and not isinstance(existing, dict):
        raise RuntimeError(f"{client}: existing Telegram entry is not a configuration table")
    for alias, value in servers.items():
        if alias == ALIASES[client] or not isinstance(value, dict):
            continue
        same_launcher = value.get("command") == wanted["command"] and value.get("args") == wanted["args"]
        known = recognized_entry(value, allow_legacy=True)
        if same_launcher or known:
            legacy = known and not recognized_entry(value)
            if mode == "register" and replace_legacy and legacy:
                replacement = _registration_refresh_entry(client, value, wanted, python=python,
                                                           install_root=install_root, replace_legacy=True)
                if replacement is None or existing is not None or remove_aliases:
                    raise RuntimeError(f"{client}: old Telegram alias was customized or conflicts with another entry; no change made")
                wanted = replacement
                remove_aliases.append(alias)
            else:
                raise RuntimeError(f"{client}: Telegram launcher already exists under another alias; a recognized old alias requires --replace-legacy")
    if mode == "verify":
        if policy_preserving_entry(client, existing, wanted) is None:
            raise RuntimeError(f"{client}: exact bounded Telegram entry is not configured")
        return source, source or b""
    if mode == "register" and existing is not None and not preserve_policy:
        replacement = _registration_refresh_entry(client, existing, wanted, python=python,
                                                   install_root=install_root, replace_legacy=replace_legacy)
        if replacement is None:
            raise RuntimeError(f"{client}: existing Telegram entry is unrelated, customized or legacy; no change made. "
                               "A recognized 0.2/0.3 installation requires --replace-legacy")
        wanted = replacement
    if existing is not None and existing != wanted and not recognized_entry(existing, allow_legacy=replace_legacy and mode == "register"):
        raise RuntimeError(f"{client}: existing Telegram alias is unrelated or legacy; no change made. For a recognized 0.2/0.3 installation only, use --replace-legacy")
    if mode == "unregister" and existing is None:
        return source, source or b""
    if existing == wanted and mode == "register" and not remove_aliases:
        return source, source or b""
    if client == "codex":
        output = source
        for alias in remove_aliases:
            output = _replace_codex(output, None, alias)
        output = _replace_codex(output, wanted if mode == "register" else None)
    else:
        servers = root.setdefault(NAMES[client], {})
        for alias in remove_aliases:
            servers.pop(alias)
        if mode == "register":
            servers[ALIASES[client]] = wanted
        else:
            servers.pop(ALIASES[client], None)
        output = (json.dumps(root, ensure_ascii=False, indent=2) + "\n").encode()
    return source, output


def configure(mode: str, *, clients: list[str], python: str, paths: dict[str, Path] | None = None, replace_legacy: bool = False, install_root: Path | None = None, expected_sources: dict[str, bytes | None] | None = None,
              preserve_policy: bool = False) -> list[Path]:
    if mode not in ("register", "verify", "unregister"):
        raise ValueError("Unknown registration operation")
    if preserve_policy and (mode != "register" or install_root is None or expected_sources is None):
        raise ValueError("Policy-preserving refresh requires an installed registration and byte snapshots")
    python = _validated_python(python)
    _validate_env_command()
    paths = paths or {}
    targets = {client: paths.get(client, default_path(client)) for client in clients}
    if len(set(targets.values())) != len(targets):
        raise RuntimeError("Each client requires a different configuration file")
    backups: list[Path] = []
    with contextlib.ExitStack() as stack:
        # Cooperative locks remain on disk; never unlink a lock another installer may hold.
        for path in sorted(targets.values()):
            validate_path(path)
            if mode == "verify":
                continue
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            lock = path.parent / ".telegram-search-registration.lock"
            validate_path(lock)
            descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            handle = stack.enter_context(os.fdopen(descriptor, "a+b"))
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        if expected_sources is not None:
            if set(expected_sources) != set(targets) or any(read_source(path) != expected_sources[client] for client, path in targets.items()):
                raise RuntimeError("Client registration changed during migration; it was not restored")
        prepared = [(targets[client], *_prepare(client, targets[client], python, mode, replace_legacy, install_root,
                                                preserve_policy=preserve_policy)) for client in clients]
        if expected_sources is not None and any(before != expected_sources[client] for client, (_, before, _) in zip(clients, prepared)):
            raise RuntimeError("Client registration changed during migration; it was not restored")
        changed = []
        try:
            for path, before, after in prepared:
                if after == (before or b""):
                    continue
                backup = atomic_write(path, after, expected=before)
                changed.append((path, before, after))
                if backup:
                    backups.append(backup)
        except BaseException:
            for path, before, after in reversed(changed):
                if read_source(path) != after:
                    raise RuntimeError("Registration failed; configuration changed concurrently. Restore the private backup manually")
                if before is None:
                    path.unlink()
                else:
                    atomic_write(path, before, expected=after)
            raise
    return backups


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("register", "verify", "unregister"))
    parser.add_argument("--clients", choices=("codex", "gemini", "both", "none"), default="both")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--codex-config", type=Path)
    parser.add_argument("--gemini-config", type=Path)
    parser.add_argument("--replace-legacy", action="store_true")
    args = parser.parse_args()
    from .launchers import installed_root
    managed_root = installed_root(args.python)
    # 0.4 installations have a root marker but no stable launcher yet.
    if managed_root is not None and not (managed_root / "launch-mcp.command").is_file():
        managed_root = None
    clients = ["codex", "gemini"] if args.clients == "both" else ([] if args.clients == "none" else [args.clients])
    paths = {client: path for client in clients if (path := getattr(args, f"{client}_config")) is not None}
    try:
        backups = configure(args.mode, clients=clients, python=args.python, paths=paths, replace_legacy=args.replace_legacy, install_root=managed_root)
        if managed_root is not None:
            from .updater import forget_clients, remember_clients
            targets = {client: paths.get(client, default_path(client)) for client in clients}
            if args.mode == "unregister":
                forget_clients(managed_root, clients, targets)
            elif args.mode == "register":
                remember_clients(managed_root, targets)
    except Exception as exc:
        raise SystemExit(f"Client registration failed: {exc}") from exc
    print(f"Client configuration {args.mode}: {', '.join(clients) or 'none'}")
    for backup in backups:
        print(f"Private configuration backup: {backup}")


if __name__ == "__main__":
    main()
