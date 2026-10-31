"""Test the real macOS launcher preflight independently of host file layout."""

import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="The macOS registration preflight uses POSIX APIs"
)


@pytest.fixture
def env_launcher(monkeypatch):
    from telegram_search_mcp import gemini_config

    class Launcher:
        mode = stat.S_IFREG | 0o755
        uid = 0
        executable = True
        error = None

        def lstat(self):
            if self.error is not None:
                raise self.error
            return SimpleNamespace(st_mode=self.mode, st_uid=self.uid)

    launcher = Launcher()
    original_os = gemini_config.os

    class LauncherOS:
        def __getattr__(self, name):
            return getattr(original_os, name)

        def access(self, path, mode):
            if path is launcher:
                assert mode == original_os.X_OK
                return launcher.executable
            return original_os.access(path, mode)

    def launcher_path(value):
        if value == gemini_config.ENV_COMMAND:
            return launcher
        return Path(value)

    # Only this module's env-path lookup and executable probe are modeled.
    # pathlib.Path, os.access, and checks of all other real paths stay intact.
    monkeypatch.setattr(gemini_config, "Path", launcher_path)
    monkeypatch.setattr(gemini_config, "os", LauncherOS())
    return gemini_config, launcher


def test_root_owned_regular_executable_launcher_is_trusted(env_launcher):
    config, _ = env_launcher

    config._validate_env_command()


@pytest.mark.parametrize(
    "mode, uid, executable",
    [
        pytest.param(stat.S_IFLNK | 0o777, 0, True, id="symlink"),
        pytest.param(stat.S_IFDIR | 0o755, 0, True, id="directory"),
        pytest.param(stat.S_IFREG | 0o755, 12345, True, id="non-root-owner"),
        pytest.param(stat.S_IFREG | 0o775, 0, True, id="group-writable"),
        pytest.param(stat.S_IFREG | 0o757, 0, True, id="world-writable"),
        pytest.param(stat.S_IFREG | 0o644, 0, False, id="non-executable"),
    ],
)
def test_untrusted_launcher_is_rejected(env_launcher, mode, uid, executable):
    config, launcher = env_launcher
    launcher.mode = mode
    launcher.uid = uid
    launcher.executable = executable

    with pytest.raises(config.ConfigError, match="launcher is not trusted"):
        config._validate_env_command()


def test_missing_launcher_is_rejected(env_launcher):
    config, launcher = env_launcher
    launcher.error = FileNotFoundError("synthetic missing system launcher")

    with pytest.raises(config.ConfigError, match="launcher is unavailable"):
        config._validate_env_command()


def test_untrusted_launcher_prevents_either_client_config_write(
    env_launcher, tmp_path
):
    from telegram_search_mcp import registration

    config, launcher = env_launcher
    launcher.mode = stat.S_IFLNK | 0o777
    python = tmp_path / "python"
    python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    python.chmod(0o700)
    paths = {
        "codex": tmp_path / "config.toml",
        "gemini": tmp_path / "settings.json",
    }
    paths["codex"].write_text('model = "preserved-model"\n', encoding="utf-8")
    paths["gemini"].write_text(
        '{"general":{"vimMode":true}}\n', encoding="utf-8"
    )
    before = {client: path.read_bytes() for client, path in paths.items()}
    files_before = set(tmp_path.iterdir())

    with pytest.raises(config.ConfigError, match="launcher is not trusted"):
        registration.configure(
            "register", clients=list(paths), python=str(python), paths=paths
        )

    assert {client: path.read_bytes() for client, path in paths.items()} == before
    assert set(tmp_path.iterdir()) == files_before
