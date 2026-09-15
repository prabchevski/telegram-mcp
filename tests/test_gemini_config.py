from __future__ import annotations

import json
import os
import pwd
import subprocess
import sys
from pathlib import Path

import pytest

from telegram_search_mcp.gemini_config import (
    DESCRIPTION,
    ENV_COMMAND,
    SAFE_LANG,
    SAFE_PATH,
    SERVER,
    SERVER_MODULE,
    TIMEOUT_MS,
    TOOLS,
    ConfigError,
    registration_state,
    verify_absent,
    verify_registration,
)


def executable(tmp_path: Path) -> str:
    command = tmp_path / "Telegram Search" / "tgsearch-mcp"
    command.parent.mkdir()
    command.write_text("#!/bin/sh\n", encoding="utf-8")
    command.chmod(0o700)
    return str(command)


def isolated_args(python: str) -> list[str]:
    return [
        "-i",
        f"HOME={pwd.getpwuid(os.getuid()).pw_dir}",
        f"PATH={SAFE_PATH}",
        f"LANG={SAFE_LANG}",
        python,
        "-I",
        "-m",
        SERVER_MODULE,
    ]


def safe_entry(python: str) -> dict:
    return {
        "command": ENV_COMMAND,
        "args": isolated_args(python),
        "timeout": TIMEOUT_MS,
        "trust": False,
        "description": DESCRIPTION,
        "includeTools": list(TOOLS),
    }


def test_preflight_reports_absent_without_creating_config(tmp_path) -> None:
    command = executable(tmp_path)
    config = tmp_path / "home" / ".gemini" / "settings.json"

    assert registration_state(config, expected_python=command) == "absent"
    assert not config.exists()


def test_preflight_accepts_gemini_style_comment_json(tmp_path) -> None:
    command = executable(tmp_path)
    config = tmp_path / "settings.json"
    config.write_text(
        """
        {
          // Gemini user settings remain intact.
          "general": {"vimMode": true},
          "urlExample": "https://example.test/a//b",
          "mcpServers": {
            "telegram-search": {
              "command": %s,
              "args": %s,
              "timeout": 150000,
              "trust": false,
              "description": %s,
              "includeTools": %s
            }
          }
        }
        """
        % (
            json.dumps(ENV_COMMAND),
            json.dumps(isolated_args(command)),
            json.dumps(DESCRIPTION),
            json.dumps(list(TOOLS)),
        ),
        encoding="utf-8",
    )

    assert registration_state(config, expected_python=command) == "exact"


def test_preflight_reports_refresh_for_same_safe_command(tmp_path) -> None:
    command = executable(tmp_path)
    config = tmp_path / "settings.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    SERVER: {
                        "command": ENV_COMMAND,
                        "args": isolated_args(command),
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    assert registration_state(config, expected_python=command) == "refresh"


def test_verify_requires_exact_bounded_entry(tmp_path) -> None:
    command = executable(tmp_path)
    config = tmp_path / "settings.json"
    config.write_text(
        json.dumps({"mcpServers": {SERVER: safe_entry(command)}}),
        encoding="utf-8",
    )

    verify_registration(config, expected_python=command)


def test_preflight_refuses_different_command_without_changing_file(tmp_path) -> None:
    command = executable(tmp_path)
    config = tmp_path / "settings.json"
    config.write_text(
        json.dumps({"mcpServers": {SERVER: {"command": "/tmp/other"}}}),
        encoding="utf-8",
    )
    before = config.read_bytes()

    with pytest.raises(ConfigError, match="different command"):
        registration_state(config, expected_python=command)

    assert config.read_bytes() == before


@pytest.mark.parametrize(
    "change, message",
    [
        ({"httpUrl": "https://example.test"}, "remote"),
        ({"args": ["--unsafe"]}, "isolated launcher"),
        ({"env": {"TGSEARCH_DATA_DIR": "/tmp"}}, "environment"),
        ({"cwd": "/tmp"}, "working directory"),
    ],
)
def test_preflight_refuses_conflicting_transport_fields(
    tmp_path, change, message
) -> None:
    command = executable(tmp_path)
    config = tmp_path / "settings.json"
    rendered = safe_entry(command)
    rendered.update(change)
    config.write_text(
        json.dumps({"mcpServers": {SERVER: rendered}}), encoding="utf-8"
    )

    with pytest.raises(ConfigError, match=message):
        registration_state(config, expected_python=command)


def test_preflight_refuses_duplicate_alias_for_same_command(tmp_path) -> None:
    command = executable(tmp_path)
    config = tmp_path / "settings.json"
    config.write_text(
        json.dumps({"mcpServers": {"old-alias": safe_entry(command)}}),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="another alias"):
        registration_state(config, expected_python=command)


def test_preflight_allows_an_unrelated_env_launcher_alias(tmp_path) -> None:
    command = executable(tmp_path)
    config = tmp_path / "settings.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "other-server": {
                        "command": ENV_COMMAND,
                        "args": ["-i", "/usr/bin/true"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    assert registration_state(config, expected_python=command) == "absent"


@pytest.mark.parametrize(
    "body",
    [
        b'{"mcpServers": {}, "mcpServers": {}}',
        b'{"mcpServers": NaN}',
        b'{"mcpServers": []}',
        b'[]',
        b'not-json',
        b'{/* open comment}',
        b'{"mcpServers": {},}',
    ],
)
def test_preflight_refuses_malformed_or_ambiguous_json(tmp_path, body) -> None:
    command = executable(tmp_path)
    config = tmp_path / "settings.json"
    config.write_bytes(body)

    with pytest.raises(ConfigError):
        registration_state(config, expected_python=command)


def test_preflight_refuses_symlinked_config(tmp_path) -> None:
    command = executable(tmp_path)
    target = tmp_path / "real.json"
    target.write_text("{}", encoding="utf-8")
    config = tmp_path / "settings.json"
    config.symlink_to(target)

    with pytest.raises(ConfigError, match="symlinked"):
        registration_state(config, expected_python=command)


def test_preflight_refuses_group_writable_config(tmp_path) -> None:
    command = executable(tmp_path)
    config = tmp_path / "settings.json"
    config.write_text("{}", encoding="utf-8")
    config.chmod(0o620)

    with pytest.raises(ConfigError, match="group- or world-writable"):
        registration_state(config, expected_python=command)


def test_verify_absent_accepts_missing_alias_and_rejects_present(tmp_path) -> None:
    config = tmp_path / "settings.json"
    config.write_text('{"mcpServers": {}}', encoding="utf-8")
    verify_absent(config)
    config.write_text(
        json.dumps({"mcpServers": {SERVER: {"command": "/tmp/other"}}}),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="still configured"):
        verify_absent(config)


def test_preflight_refuses_non_executable_command(tmp_path) -> None:
    command = tmp_path / "tgsearch-mcp"
    command.write_text("#!/bin/sh\n", encoding="utf-8")
    command.chmod(0o600)

    with pytest.raises(ConfigError, match="not executable"):
        registration_state(
            tmp_path / "settings.json", expected_python=str(command)
        )


def test_preflight_accepts_owned_venv_python_symlink(tmp_path) -> None:
    target = tmp_path / "managed-python"
    target.write_text("#!/bin/sh\n", encoding="utf-8")
    target.chmod(0o700)
    link = tmp_path / "venv-python"
    link.symlink_to(target)

    assert (
        registration_state(
            tmp_path / "settings.json", expected_python=str(link)
        )
        == "absent"
    )


def test_preflight_refuses_writable_python_target(tmp_path) -> None:
    command = tmp_path / "writable-python"
    command.write_text("#!/bin/sh\n", encoding="utf-8")
    command.chmod(0o722)

    with pytest.raises(ConfigError, match="group- or world-writable"):
        registration_state(
            tmp_path / "settings.json", expected_python=str(command)
        )


def test_isolated_launcher_ignores_parent_python_environment(tmp_path) -> None:
    poison_root = tmp_path / "poison"
    poison_package = poison_root / "telegram_search_mcp"
    poison_package.mkdir(parents=True)
    (poison_package / "__init__.py").write_text("", encoding="utf-8")
    marker = tmp_path / "shadow-executed"
    (poison_package / "server.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n",
        encoding="utf-8",
    )
    inherited = dict(os.environ)
    inherited.update(
        {
            "PYTHONPATH": str(poison_root),
            "PYTHONHOME": str(poison_root),
            "DYLD_LIBRARY_PATH": str(poison_root),
            "TGSEARCH_DATA_DIR": str(tmp_path / "redirected-data"),
        }
    )

    result = subprocess.run(
        [ENV_COMMAND, *isolated_args(sys.executable)],
        input="",
        capture_output=True,
        text=True,
        env=inherited,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not marker.exists()
    assert not (tmp_path / "redirected-data").exists()


def test_no_secret_config_content_is_in_errors(tmp_path) -> None:
    command = executable(tmp_path)
    config = tmp_path / "settings.json"
    secret = "do-not-print-this-secret"
    config.write_text(f'{{"token":"{secret}",', encoding="utf-8")

    with pytest.raises(ConfigError) as captured:
        registration_state(config, expected_python=command)

    assert secret not in str(captured.value)
