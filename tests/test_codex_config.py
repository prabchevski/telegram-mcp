from __future__ import annotations

import tomllib

import pytest

from telegram_search_mcp.codex_config import apply_safety_settings


def test_apply_safety_settings_updates_only_telegram_table(tmp_path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        'model = "example"\n\n'
        '[mcp_servers.other]\ncommand = "keep-me"\n\n'
        '[mcp_servers.telegram_search]\ncommand = "/tmp/tgsearch-mcp"\n\n'
        '[memories]\nuse_memories = true\n',
        encoding="utf-8",
    )

    apply_safety_settings(config, expected_command="/tmp/tgsearch-mcp")

    result = tomllib.loads(config.read_text(encoding="utf-8"))
    assert result["model"] == "example"
    assert result["mcp_servers"]["other"] == {"command": "keep-me"}
    telegram = result["mcp_servers"]["telegram_search"]
    assert telegram["command"] == "/tmp/tgsearch-mcp"
    assert telegram["enabled_tools"] == [
        "telegram_search_messages",
        "telegram_get_message",
        "telegram_get_context",
        "telegram_get_media",
    ]
    assert telegram["default_tools_approval_mode"] == "prompt"
    assert telegram["supports_parallel_tool_calls"] is False
    assert telegram["startup_timeout_sec"] == 20
    assert telegram["tool_timeout_sec"] == 150
    assert result["memories"] == {"use_memories": True}


def test_apply_safety_settings_refuses_missing_server(tmp_path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[mcp_servers.other]\ncommand = "keep-me"\n', encoding="utf-8")

    with pytest.raises(RuntimeError, match="not configured"):
        apply_safety_settings(config)


def test_apply_safety_settings_refuses_different_command(tmp_path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp_servers.telegram_search]\ncommand = "/tmp/something-else"\n',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="different command"):
        apply_safety_settings(config, expected_command="/tmp/tgsearch-mcp")
