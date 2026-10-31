"""Explicit platform preconditions for portable unit tests."""

import pytest


@pytest.fixture
def trusted_macos_env_launcher(monkeypatch):
    """Model macOS's trusted env launcher without depending on the test host.

    Registration and update tests exercise disposable client configurations, not
    the host operating system's launcher layout. Keep the actual fail-closed
    launcher checks separate in test_env_launcher_trust.py. Imports stay lazy so
    Windows-only tests do not load the POSIX registration modules unnecessarily.
    """
    from telegram_search_mcp import gemini_config, registration

    def trusted_precondition():
        return None

    monkeypatch.setattr(gemini_config, "_validate_env_command", trusted_precondition)
    # registration imports this function directly, so it has its own reference.
    monkeypatch.setattr(registration, "_validate_env_command", trusted_precondition)
