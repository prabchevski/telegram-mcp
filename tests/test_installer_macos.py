"""Exercise the shipped installer without clients, credentials or a TDLib session."""
from __future__ import annotations

import json
import os
import shlex
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
        assert "Authorization was skipped" in result.stdout
        current = (install_root / "current").resolve()
        assert current.parent == install_root
        assert (current / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
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


@pytest.mark.parametrize("download_needed", [False, True])
def test_bootstrap_never_selects_system_python_or_project_venv(tmp_path, download_needed):
    """Both discovery attempts must exclude unsafe system/project interpreters."""
    private_python = tmp_path / "managed-python"
    unsafe_python = tmp_path / "unsafe-system-python"
    unsafe_python.write_text("system interpreter must not be selected")
    unsafe_python.chmod(0o777)
    ready = tmp_path / "managed-installed"
    if not download_needed:
        ready.touch()
    log = tmp_path / "uv-calls.jsonl"
    fake_uv = tmp_path / "uv"
    fake_uv.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        f"ready = pathlib.Path({str(ready)!r})\n"
        f"with open({str(log)!r}, 'a') as output: output.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "args = sys.argv[1:]\n"
        "if args[:2] == ['python', 'find']:\n"
        "    if not {'--managed-python', '--system', '--no-project', '--no-config'}.issubset(args):\n"
        f"        print({str(unsafe_python)!r}); raise SystemExit(0)\n"
        "    if not ready.exists(): raise SystemExit(1)\n"
        f"    print({str(private_python)!r})\n"
        "elif args[:2] == ['python', 'install']:\n"
        "    if '--no-bin' not in args: raise SystemExit('Refusing global Python shims')\n"
        "    ready.touch()\n"
        "else: raise SystemExit(2)\n"
    )
    fake_uv.chmod(0o700)
    source = (ROOT / "install-macos.command").read_text()
    bootstrap = source.split("# The managed bootstrap interpreter", 1)[1].split("SAFE_USER_HOME=", 1)[0]
    bootstrap = "# The managed bootstrap interpreter" + bootstrap
    script = "set -euo pipefail\nUV_BIN=" + shlex.quote(str(fake_uv)) + "\n" + bootstrap + '\nprintf "%s\\n" "$BOOTSTRAP_PYTHON"\n'
    result = subprocess.run(["/bin/bash", "-c", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(private_python)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(calls) == (3 if download_needed else 1)
    assert unsafe_python.stat().st_mode & 0o777 == 0o777  # Global permissions were untouched.
