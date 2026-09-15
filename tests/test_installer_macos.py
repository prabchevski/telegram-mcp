"""Exercise the shipped installer without clients, credentials or a TDLib session."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_installer_help_is_standalone():
    result = subprocess.run(["/bin/bash", str(ROOT / "install-macos.command"), "--help"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert "--prepare-only" in result.stdout
    assert "--clients" in result.stdout


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("uv"), reason="macOS and uv are required for installation smoke test")
def test_prepare_update_and_unregister_with_isolated_configs(tmp_path):
    install_root = tmp_path.resolve() / "install with spaces"
    codex = tmp_path.resolve() / "codex" / "config.toml"
    gemini = tmp_path.resolve() / "gemini" / "settings.json"
    command = ["/bin/bash", str(ROOT / "install-macos.command"), "--prepare-only", "--skip-system-deps", "--clients", "both", "--install-dir", str(install_root), "--codex-config", str(codex), "--gemini-config", str(gemini)]
    environment = dict(os.environ, TGSEARCH_INSTALL_UV=shutil.which("uv"))
    for iteration in range(2):
        result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, result.stderr
        assert "Авторизация не запускалась" in result.stdout
        current = (install_root / "current").resolve()
        assert current.parent == install_root
        assert (current / "tgsearch").stat().st_mode & 0o777 == 0o700
        if iteration == 0:
            old_version = current
        else:
            assert old_version != current
            assert (old_version / ".venv" / "bin" / "python").exists()
    # Verify only config and launcher; never run auth, doctor --connect or any Telegram tool.
    result = subprocess.run([str(current / "client-config"), "verify", "--codex-config", str(codex), "--gemini-config", str(gemini)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    result = subprocess.run(["/bin/bash", str(current / "uninstall-macos.command"), "--codex-config", str(codex), "--gemini-config", str(gemini)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "[mcp_servers.telegram_search]" not in codex.read_text()
    assert '"telegram-search"' not in gemini.read_text()
    assert old_version.exists() and current.exists()
