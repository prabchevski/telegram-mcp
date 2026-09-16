import json
import tomllib

import pytest

from test_managed_updates import managed
from telegram_search_mcp import registration
from telegram_search_mcp.sending_settings import OUTGOING_TOOLS, set_sending, sending_enabled


def test_codex_and_gemini_enable_same_tools_preserving_approval_and_other_settings(managed, tmp_path):
    from telegram_search_mcp import updater
    root, version, codex = managed
    gemini = tmp_path / "gemini/settings.json"
    gemini.parent.mkdir()
    gemini.write_text(json.dumps({"ui": {"theme": "user-theme"}, "mcpServers": {"unrelated": {"command": "other"}}}))
    python = str(version / ".venv/bin/python")
    registration.configure("register", clients=["gemini"], python=python, paths={"gemini": gemini}, install_root=root)
    updater.remember_clients(root, {"gemini": gemini})
    before = json.loads(gemini.read_text())
    set_sending(root, True)
    data = json.loads(gemini.read_text())
    entry = data["mcpServers"]["telegram-search"]
    codex_entry = tomllib.loads(codex.read_text())["mcp_servers"]["telegram_search"]
    assert entry["includeTools"] == codex_entry["enabled_tools"]
    assert len(entry["includeTools"]) == 9
    assert entry["trust"] is False
    assert codex_entry["default_tools_approval_mode"] == "prompt"
    assert data["ui"] == before["ui"]
    assert data["mcpServers"]["unrelated"] == before["mcpServers"]["unrelated"]
    registration.configure("verify", clients=["codex", "gemini"], python=python,
                           paths={"codex": codex, "gemini": gemini}, install_root=root)
    set_sending(root, False)
    assert json.loads(gemini.read_text()) == before


def test_sending_is_opt_in_and_restores_read_only_registration(managed):
    root, _, config = managed
    before = tomllib.loads(config.read_text())
    assert not sending_enabled(root)
    set_sending(root, True)
    assert sending_enabled(root)
    entry = tomllib.loads(config.read_text())["mcp_servers"]["telegram_search"]
    assert len(entry["enabled_tools"]) == 9
    assert set(OUTGOING_TOOLS) <= set(entry["enabled_tools"])
    assert entry["default_tools_approval_mode"] == "prompt"
    set_sending(root, False)
    assert not sending_enabled(root)
    assert tomllib.loads(config.read_text()) == before


def test_removed_registration_is_not_restored_and_off_still_disables(managed):
    root, _, config = managed
    set_sending(root, True)
    config.write_text('[mcp_servers.other]\ncommand="other"\n')
    before = config.read_bytes()
    set_sending(root, False)
    assert not sending_enabled(root)
    assert config.read_bytes() == before
    with pytest.raises(RuntimeError, match="No unchanged"):
        set_sending(root, True)
    assert not sending_enabled(root)


def test_registration_failure_rolls_back_feature_flag(managed, monkeypatch):
    root, _, config = managed
    before = config.read_bytes()
    def fail(*args, **kwargs):
        raise RuntimeError("simulated registration failure")
    monkeypatch.setattr(registration, "configure", fail)
    with pytest.raises(RuntimeError, match="simulated"):
        set_sending(root, True)
    assert not sending_enabled(root)
    assert config.read_bytes() == before


def test_malformed_or_symlinked_setting_fails_closed(managed, tmp_path):
    root, _, _ = managed
    path = root / "sending.json"
    path.write_text(json.dumps({"enabled": 1}))
    with pytest.raises(RuntimeError):
        sending_enabled(root)
    path.unlink()
    target = tmp_path / "other.json"
    target.write_text('{"enabled":true}')
    path.symlink_to(target)
    with pytest.raises(RuntimeError):
        sending_enabled(root)
