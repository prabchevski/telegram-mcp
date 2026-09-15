"""CLI checks must use the same owner as MCP, never a second live TDLib session."""
from argparse import Namespace
from contextlib import contextmanager

from telegram_search_mcp import cli


def test_auth_acquires_service_exclusion_before_any_profile_change(monkeypatch):
    from telegram_search_mcp import service

    events = []

    @contextmanager
    def guard(profile):
        events.append(('lock', profile))
        yield
        events.append(('unlock', profile))

    monkeypatch.setattr(service, 'profile_exclusive', guard)
    monkeypatch.setattr(cli, '_authorize_exclusive', lambda args: events.append(('auth', args.profile)) or 0)
    assert cli.command_auth(Namespace(profile='test')) == 0
    assert events == [('lock', 'test'), ('auth', 'test'), ('unlock', 'test')]


def test_doctor_connect_uses_shared_service(monkeypatch, capsys):
    from telegram_search_mcp import service_client

    calls = []

    async def check(profile, *, connect=False):
        calls.append((profile, connect))
        return {'ready': True}

    monkeypatch.setattr(cli.Policy, 'load', lambda profile: Namespace(expected_user_id=123))
    monkeypatch.setattr(cli, 'get_secret', lambda *args: 'test-secret')
    monkeypatch.setattr(cli, 'tdjson_library_candidates', lambda: [__file__])
    monkeypatch.setattr(service_client, 'service_status', check)
    monkeypatch.setattr(cli, 'TdlibSession', lambda *args: (_ for _ in ()).throw(AssertionError('Direct session forbidden')))
    assert cli.command_doctor(Namespace(profile='test', connect=True)) == 0
    assert calls == [('test', True)]
    assert 'test-secret' not in capsys.readouterr().out
