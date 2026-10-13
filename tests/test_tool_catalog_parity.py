"""Supported client declarations must expose the real shared MCP tool set."""
import asyncio
from argparse import Namespace
import sys
from types import ModuleType
from pathlib import Path

from mcp import Client
import pytest

from telegram_search_mcp import cli
from telegram_search_mcp.server import create_server
from telegram_search_mcp.tool_catalog import DEFAULT_TOOLS, EDIT_TOOLS, OUTGOING_TOOLS, advertised_tools
from telegram_search_mcp import sending_settings


@pytest.mark.asyncio
@pytest.mark.parametrize('sending', [False, True])
async def test_catalog_matches_actual_mcp_discovery_for_every_supported_client(sending):
    # Codex/Gemini registration and the Windows plugin all launch this same server.
    # Listing does not open a native session or read the user's profile.
    async with Client(create_server(object(), enable_sending=sending)) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
    assert names == set(advertised_tools(sending=sending))
    assert len(names) == len(advertised_tools(sending=sending))
    assert names.isdisjoint(EDIT_TOOLS) if not sending else set(EDIT_TOOLS) <= names
    assert sending_settings.OUTGOING_TOOLS is OUTGOING_TOOLS
    assert sending_settings.EDIT_TOOLS is EDIT_TOOLS


def test_cli_windows_update_dispatch_does_not_import_posix_updater(monkeypatch, capsys, tmp_path):
    from telegram_search_mcp import launchers
    root = tmp_path / 'application'
    calls = []
    module = ModuleType('telegram_search_mcp.windows_updates')
    def update(path, *, check_only=False):
        calls.append((path, check_only))
        return {'status': 'up_to_date'}
    module.update = update
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(cli.sys, 'platform', 'win32')
    monkeypatch.setattr(launchers, 'installed_root', lambda: root)
    assert cli.command_update(Namespace(check=True)) == 0
    assert calls == [(root, True)]
    assert 'up_to_date' in capsys.readouterr().out


@pytest.mark.parametrize('action', ['on', 'off', 'status'])
def test_cli_windows_update_preferences_dispatch(monkeypatch, capsys, tmp_path, action):
    from telegram_search_mcp import launchers
    root = tmp_path / 'application'
    calls = []
    module = ModuleType('telegram_search_mcp.windows_updates')
    module.set_enabled = lambda path, enabled: calls.append((path, enabled))
    module.updates_status = lambda path: {'automatic': action != 'off', 'channel': 'checked-release'}
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(cli.sys, 'platform', 'win32')
    monkeypatch.setattr(launchers, 'installed_root', lambda: root)
    assert cli.command_updates(Namespace(action=action)) == 0
    assert calls == ([] if action == 'status' else [(root, action == 'on')])
    assert 'checked-release' in capsys.readouterr().out
