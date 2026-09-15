from __future__ import annotations

import json
import os
import pwd
import subprocess
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from telegram_search_mcp import registration
from telegram_search_mcp.registration import configure, expected_entry, launcher_arguments


def managed_python(tmp_path: Path, version: str = "0.4.0", name: str = "telegram-search-mcp") -> str:
    install = tmp_path / version
    executable = install / ".venv" / "bin" / "python"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    (install / "pyproject.toml").write_text(f'[project]\nname = "{name}"\nversion = "{version}"\n')
    return str(executable)


def paths(tmp_path: Path) -> dict[str, Path]:
    return {"codex": tmp_path / "codex" / "config.toml", "gemini": tmp_path / "gemini" / "settings.json"}


def read_config(client: str, path: Path) -> dict:
    return tomllib.loads(path.read_text()) if client == "codex" else json.loads(path.read_text())


def test_both_clients_receive_four_tools_and_same_isolated_launcher(tmp_path, monkeypatch):
    python = managed_python(tmp_path)
    target = paths(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "poison-home"))
    assert configure("register", clients=list(target), python=python, paths=target) == []
    for client, path in target.items():
        value = read_config(client, path)[registration.NAMES[client]][registration.ALIASES[client]]
        assert value == expected_entry(client, python)
        assert value["command"] == "/usr/bin/env"
        assert f"HOME={pwd.getpwuid(os.getuid()).pw_dir}" in value["args"]
        assert "TGSEARCH" not in " ".join(value["args"])
        assert path.stat().st_mode & 0o777 == 0o600
    configure("verify", clients=list(target), python=python, paths=target)


def test_existing_other_settings_preserved_and_backed_up_exactly(tmp_path):
    python = managed_python(tmp_path)
    target = paths(tmp_path)
    target["codex"].parent.mkdir()
    target["gemini"].parent.mkdir()
    codex = b'# user comment\nmodel = "chosen"\n[mcp_servers.other]\ncommand = "other"\n'
    gemini = b'{ // comment retained in backup\n "general": {"vimMode":true}, "mcpServers":{"other":{"command":"other"}}}\n'
    target["codex"].write_bytes(codex)
    target["gemini"].write_bytes(gemini)
    backups = configure("register", clients=list(target), python=python, paths=target)
    assert {p.read_bytes() for p in backups} == {codex, gemini}
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in backups)
    assert target["codex"].read_text().startswith(codex.decode().rstrip())
    assert read_config("gemini", target["gemini"])["general"] == {"vimMode": True}
    assert configure("register", clients=list(target), python=python, paths=target) == []


def test_both_preflight_fails_before_either_config_write(tmp_path):
    python = managed_python(tmp_path)
    target = paths(tmp_path)
    target["gemini"].parent.mkdir()
    original = b'{"mcpServers":{"telegram-search":{"command":"unrelated"}}}'
    target["gemini"].write_bytes(original)
    with pytest.raises(RuntimeError, match="unrelated"):
        configure("register", clients=list(target), python=python, paths=target, replace_legacy=True)
    assert not target["codex"].exists()
    assert target["gemini"].read_bytes() == original


def test_second_write_failure_rolls_back_first(tmp_path, monkeypatch):
    python = managed_python(tmp_path)
    target = paths(tmp_path)
    real_write = registration.atomic_write
    def fail_gemini(path, *args, **kwargs):
        if path == target["gemini"]:
            raise OSError("simulated disk failure")
        return real_write(path, *args, **kwargs)
    monkeypatch.setattr(registration, "atomic_write", fail_gemini)
    with pytest.raises(OSError, match="disk failure"):
        configure("register", clients=list(target), python=python, paths=target)
    assert not any(p.exists() for p in target.values())


def test_updates_use_new_version_and_keep_old_binary(tmp_path):
    old, new = managed_python(tmp_path), managed_python(tmp_path, "0.5.0")
    target = paths(tmp_path)
    configure("register", clients=list(target), python=old, paths=target)
    configure("register", clients=list(target), python=new, paths=target)
    configure("verify", clients=list(target), python=new, paths=target)
    assert Path(old).exists()
    assert len(list(tmp_path.rglob("*telegram-search-backup-*"))) == 2


@pytest.mark.parametrize("client,legacy_name", [("codex", "codex-telegram-search-mcp"), ("gemini", "gemini-telegram-search-mcp")])
def test_legacy_update_requires_opt_in_and_recognized_manifest(tmp_path, client, legacy_name):
    old = managed_python(tmp_path, "legacy", legacy_name)
    new = managed_python(tmp_path)
    path = paths(tmp_path)[client]
    path.parent.mkdir()
    if client == "codex":
        old = str(Path(old).with_name("tgsearch-mcp"))
        Path(old).write_text("#!/bin/sh\n")
        entry = {"command": old}
        path.write_text('[mcp_servers.telegram_search]\ncommand = ' + json.dumps(old) + '\n')
    else:
        entry = {"command": "/usr/bin/env", "args": ["-i", f"PATH={registration.SAFE_PATH}", f"LANG={registration.SAFE_LANG}", old, "-I", "-m", registration.SERVER_MODULE]}
        path.write_text(json.dumps({"mcpServers": {"telegram-search": entry}}))
    original = path.read_bytes()
    with pytest.raises(RuntimeError, match="--replace-legacy"):
        configure("register", clients=[client], python=new, paths={client: path})
    assert path.read_bytes() == original
    configure("register", clients=[client], python=new, paths={client: path}, replace_legacy=True)
    configure("verify", clients=[client], python=new, paths={client: path})


def test_uninstall_preserves_other_entries_and_rejects_unrelated(tmp_path):
    python = managed_python(tmp_path)
    target = paths(tmp_path)
    configure("register", clients=list(target), python=python, paths=target)
    configure("unregister", clients=list(target), python=python, paths=target)
    for client, path in target.items():
        assert registration.ALIASES[client] not in read_config(client, path).get(registration.NAMES[client], {})
    target["codex"].write_text('[mcp_servers.telegram_search]\ncommand = "different"\n')
    with pytest.raises(RuntimeError, match="unrelated"):
        configure("unregister", clients=["codex"], python=python, paths=target)


def test_parallel_installers_leave_valid_configuration(tmp_path):
    python = managed_python(tmp_path)
    target = paths(tmp_path)
    def register(_):
        configure("register", clients=list(target), python=python, paths=target)
    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(register, range(12)))
    configure("verify", clients=list(target), python=python, paths=target)


def test_codex_quoted_or_nested_table_rejected_without_change(tmp_path):
    python = managed_python(tmp_path)
    path = paths(tmp_path)["codex"]
    configure("register", clients=["codex"], python=python, paths={"codex": path})
    path.write_text(path.read_text().replace("[mcp_servers.telegram_search]", '[mcp_servers."telegram_search"]'))
    with pytest.raises(RuntimeError, match="locate"):
        configure("unregister", clients=["codex"], python=python, paths={"codex": path})


def test_config_symlink_and_shared_file_are_rejected(tmp_path):
    python = managed_python(tmp_path)
    original = tmp_path / "original"
    original.write_text("{}")
    linked = tmp_path / "settings.json"
    linked.symlink_to(original)
    with pytest.raises(RuntimeError, match="symlink"):
        configure("register", clients=["gemini"], python=python, paths={"gemini": linked})
    with pytest.raises(RuntimeError, match="different"):
        configure("register", clients=["codex", "gemini"], python=python, paths={"codex": original, "gemini": original})


def test_real_isolation_clears_python_and_home_injection(tmp_path):
    poison = tmp_path / "poison"
    poison.mkdir()
    code = "import os,pwd; assert os.environ['HOME']==pwd.getpwuid(os.getuid()).pw_dir; assert 'PYTHONPATH' not in os.environ; assert 'TGSEARCH_DATA_DIR' not in os.environ; print('isolated')"
    args = launcher_arguments(sys.executable)
    args[-3:] = ["-I", "-c", code]
    env = dict(os.environ, HOME=str(poison), PYTHONPATH=str(poison), PYTHONHOME=str(poison), TGSEARCH_DATA_DIR=str(poison))
    result = subprocess.run(["/usr/bin/env", *args], env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "isolated"


@pytest.mark.parametrize("client", ["codex", "gemini"])
def test_legacy_alias_is_detected_and_migrated_only_with_opt_in(tmp_path, client):
    old = managed_python(tmp_path, "legacy", "codex-telegram-search-mcp")
    old = str(Path(old).with_name("tgsearch-mcp"))
    new = managed_python(tmp_path)
    config = paths(tmp_path)[client]
    config.parent.mkdir()
    if client == "codex":
        config.write_text('[mcp_servers.old_telegram]\ncommand = ' + json.dumps(old) + '\n')
    else:
        config.write_text(json.dumps({"mcpServers": {"old_telegram": {"command": old}}}))
    with pytest.raises(RuntimeError, match="another alias"):
        configure("register", clients=[client], python=new, paths={client: config})
    configure("register", clients=[client], python=new, paths={client: config}, replace_legacy=True)
    assert "old_telegram" not in read_config(client, config)[registration.NAMES[client]]
    configure("verify", clients=[client], python=new, paths={client: config})


def test_null_entry_is_not_treated_as_absent(tmp_path):
    python = managed_python(tmp_path)
    config = tmp_path / "settings.json"
    config.write_text('{"mcpServers": {"telegram-search": null}}')
    with pytest.raises(RuntimeError, match="not a configuration table"):
        configure("register", clients=["gemini"], python=python, paths={"gemini": config})
