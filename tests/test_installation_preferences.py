"""Installer defaults preserve an existing owner's automatic update preference."""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from telegram_search_mcp import installation

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(("previous", "arguments", "expected_mode", "expected_enabled"), [
    (None, [], "on", True),
    (False, [], "keep", False),
    (True, [], "keep", True),
    (False, ["--auto-update", "on"], "on", True),
    (True, ["--auto-update", "off"], "off", False),
    (None, ["--prepare-only"], "keep", False),
    (False, ["--prepare-only"], "keep", False),
    (True, ["--prepare-only"], "keep", True),
    (False, ["--prepare-only", "--auto-update", "on"], "on", True),
])
def test_cli_default_and_explicit_update_preferences(
    tmp_path, monkeypatch, previous, arguments, expected_mode, expected_enabled,
):
    root = tmp_path / "application"
    root.mkdir(mode=0o700)
    (root / installation.ROOT_MARKER).write_text("telegram-search-mcp\n")
    receipt_path = root / installation.RECEIPT
    if previous is not None:
        receipt_path.write_text(json.dumps({"schema": 1, "version": "0.10.1", "revision": None,
                                           "clients": {}, "auto_update": previous}))
        receipt_path.chmod(0o600)
    source = tmp_path / "source"
    source.mkdir()
    codex = tmp_path / "codex/config.toml"
    version = root / "version"
    installed = []
    commands = []
    def install(candidate, target, **kwargs):
        assert candidate == source and target == root
        assert kwargs["clients"] == ["codex"]
        assert kwargs["paths"] == {"codex": codex}
        installed.append(kwargs["auto_update"])
        enabled = (previous if previous is not None else False) if kwargs["auto_update"] == "keep" else kwargs["auto_update"] == "on"
        receipt_path.write_text(json.dumps({"schema": 1, "clients": {}, "auto_update": enabled}))
        return version
    def local_command(command, **kwargs):
        assert command[0] == str(version / "tgsearch")
        assert "auth" not in command, "Tests must never authorize Telegram"
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(installation, "install", install)
    monkeypatch.setattr(installation, "default_root", lambda: pytest.fail("Must use explicit disposable root"))
    monkeypatch.setattr(installation.subprocess, "run", local_command)
    monkeypatch.setattr(sys, "argv", [
        "install", "--source", str(source), "--install-dir", str(root), "--uv", str(tmp_path / "uv"),
        "--python", str(tmp_path / "python"), "--clients", "codex", "--codex-config", str(codex),
        "--migrate-profile", "none", *arguments,
    ])
    installation.main()
    assert installed == [expected_mode]
    assert json.loads(receipt_path.read_text())["auto_update"] is expected_enabled
    if "--prepare-only" in arguments:
        assert commands == []
    else:
        assert [command[1:] for command in commands] == [["doctor"], ["doctor", "--connect"]]


def test_invalid_existing_receipt_stops_default_upgrade_before_install(tmp_path, monkeypatch):
    root = tmp_path / "application"
    root.mkdir(mode=0o700)
    (root / installation.ROOT_MARKER).write_text("telegram-search-mcp\n")
    (root / installation.RECEIPT).write_text('{"schema":99,"clients":{}}')
    monkeypatch.setattr(installation, "install", lambda *_args, **_kwargs: pytest.fail("Invalid receipt must be preserved"))
    monkeypatch.setattr(sys, "argv", ["install", "--source", str(tmp_path), "--install-dir", str(root),
                                     "--uv", "unused", "--python", "unused"])
    with pytest.raises(RuntimeError, match="Unsupported installation receipt"):
        installation.main()


@pytest.mark.parametrize(("prepare_only", "explicit", "expected"), [
    (False, "", None),
    (False, "on", "on"),
    (False, "off", "off"),
    (False, "keep", "keep"),
    (True, "", "keep"),
    (True, "on", "on"),
    (True, "off", "off"),
])
def test_shipped_shell_forwards_only_requested_or_prepare_only_update_mode(
    tmp_path, prepare_only, explicit, expected,
):
    # Execute the shipped argument-building block without dependency setup,
    # personal client paths, launchd, authorization or real installation.
    source = (ROOT / "install-macos.command").read_text()
    prefix = '[[ "$UPGRADE" == false ]]'
    selection = prefix + source.split(prefix, 1)[1].split("exec /usr/bin/env -i ", 1)[0]
    variables = {
        "UPGRADE": "false", "PREPARE_ONLY": "true" if prepare_only else "false",
        "MIGRATE_PROFILE": "", "AUTO_UPDATE": explicit, "CLIENTS": "codex", "REPLACE_LEGACY": "false",
        "SOURCE_DIR": str(tmp_path / "source"), "UV_BIN": str(tmp_path / "uv"),
        "BOOTSTRAP_PYTHON": sys.executable, "REQUESTED_INSTALL_DIR": str(tmp_path / "application"),
        "CODEX_CONFIG": str(tmp_path / "codex/config.toml"), "GEMINI_CONFIG": "",
    }
    script = "set -euo pipefail\n" + "\n".join(key + "=" + shlex.quote(value) for key, value in variables.items())
    script += "\n" + selection
    script += '\n"$BOOTSTRAP_PYTHON" -I -c \'import json,sys; print(json.dumps(sys.argv[1:]))\' "${installer_args[@]}"\n'
    result = subprocess.run(["/bin/bash", "-c", script], check=True, capture_output=True, text=True, timeout=10)
    arguments = json.loads(result.stdout)
    if expected is None:
        assert "--auto-update" not in arguments
    else:
        assert arguments[arguments.index("--auto-update") + 1] == expected
        assert arguments.count("--auto-update") == 1
    assert ("--prepare-only" in arguments) is prepare_only
