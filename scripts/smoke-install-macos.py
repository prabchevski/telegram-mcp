#!/usr/bin/env python3
"""Install a verified release twice into a disposable directory, without login.

Only explicit temporary client configuration files are touched. TDLib, Telegram
authorization, Keychain, user client settings and user services are not opened.
Dependencies come from the frozen lock using uv. Requires macOS and uv.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import tomllib
import zipfile


MCP_DISCOVERY_CHECK = """
import asyncio
import sys
from mcp import Client
from mcp.client.stdio import stdio_client, StdioServerParameters

async def main():
    parameters = StdioServerParameters(
        command=sys.executable,
        args=['-I', '-m', 'telegram_search_mcp.server'],
    )
    async with Client(stdio_client(parameters)) as client:
        result = await client.list_tools()
        assert {tool.name for tool in result.tools} == {
            'telegram_search_messages', 'telegram_get_message',
            'telegram_get_context', 'telegram_get_media',
        }
        assert len(result.tools) == 4
        for tool in result.tools:
            assert tool.annotations is not None
            assert tool.annotations.read_only_hint is True
            assert tool.annotations.destructive_hint is False

# Initialize/list only: never invoke a tool or ask the service to connect.
asyncio.run(main())
print('PASS: installed MCP stdio handshake and exactly four read-only tools.')
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    if platform.system() != "Darwin":
        parser.error("The actual installer smoke test requires macOS")
    uv = shutil.which("uv")
    if uv is None:
        parser.error("uv must already be installed")
    spec = importlib.util.spec_from_file_location("release", Path(__file__).with_name("release.py"))
    assert spec is not None and spec.loader is not None
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    manifest = release.verify_archive(args.archive)
    with tempfile.TemporaryDirectory(prefix="telegram-search-install-check-") as temporary:
        root = Path(temporary).resolve()
        with zipfile.ZipFile(args.archive) as archive:
            archive.extractall(root / "unpacked")
        source = root / "unpacked" / release.PACKAGE_ROOT
        install = root / "application"
        codex = root / "codex" / "config.toml"
        gemini = root / "gemini" / "settings.json"
        codex.parent.mkdir()
        gemini.parent.mkdir()
        codex.write_text('model = "preserve-smoke-setting"\n')
        gemini.write_text(json.dumps({"ui": {"theme": "preserve-smoke-setting"}}))
        command = [
            "/bin/bash", str(source / "install-macos.command"), "--prepare-only",
            "--clients", "both", "--skip-system-deps", "--install-dir", str(install),
            "--codex-config", str(codex), "--gemini-config", str(gemini),
        ]
        # Do not override HOME: the installer receives exact disposable paths.
        subprocess.run(command, check=True, timeout=300)
        first = (install / "current").resolve(strict=True)
        subprocess.run([str(first / "tgsearch"), "--help"], check=True, timeout=30)
        first_python = first / ".venv" / "bin" / "python"
        subprocess.run([str(first_python), "-I", "-c", "import sys; from telegram_search_mcp import __version__; assert __version__ == sys.argv[1]", manifest["version"]], check=True, timeout=30)
        subprocess.run(command, check=True, timeout=300)
        second = (install / "current").resolve(strict=True)
        assert first != second, "Update must create a new immutable installation"
        assert first_python.is_file(), "Update must preserve the previous executable"
        codex_data = tomllib.loads(codex.read_text())
        gemini_data = json.loads(gemini.read_text())
        assert codex_data["model"] == "preserve-smoke-setting"
        assert gemini_data["ui"]["theme"] == "preserve-smoke-setting"
        assert "telegram_search" in codex_data["mcp_servers"]
        assert "telegram-search" in gemini_data["mcpServers"]
        for data in (codex_data["mcp_servers"]["telegram_search"], gemini_data["mcpServers"]["telegram-search"]):
            assert data["command"] == "/usr/bin/env"
            assert str(second / ".venv" / "bin" / "python") in data["args"]
        subprocess.run(
            [str(second / ".venv" / "bin" / "python"), "-I", "-c", MCP_DISCOVERY_CHECK],
            check=True, timeout=30,
        )
        print("PASS: verified archive, real isolated installation, both client registrations, immutable update, CLI import, and MCP stdio discovery. No Telegram authorization or tool invocation performed.")


if __name__ == "__main__":
    main()
