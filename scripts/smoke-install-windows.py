#!/usr/bin/env python3
"""Exercise a real Windows archive installation without Telegram authorization.

All installation, plugin and marketplace files live in a disposable directory.
Only MCP initialization/discovery and the local sending preference are exercised;
no tool is called and no Telegram profile or Credential Manager secret is opened.
Requires native Windows x64, Python 3.13, PowerShell and uv.

Use --expected-windows 10 or 11 inside an actual desktop Windows VM to reject
Windows Server and the other desktop version. --report-path writes JSON evidence
including the observed OS, archive hash, completed checks, and final outcome.
Keep reports outside the release source tree, for example in dist/acceptance/.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile


WINDOWS_IDENTITY_CHECK = r"""
$ErrorActionPreference = 'Stop'
$env:PSModulePath = [IO.Path]::Combine($PSHOME, 'Modules')
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$version = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$system = Get-CimInstance -ClassName Win32_OperatingSystem
[ordered]@{
    ProductName = $version.ProductName
    DisplayVersion = $version.DisplayVersion
    ReleaseId = $version.ReleaseId
    CurrentBuild = $version.CurrentBuild
    UBR = $version.UBR
    Caption = $system.Caption
    Version = $system.Version
    OSArchitecture = $system.OSArchitecture
    ProductType = $system.ProductType
    PowerShellVersion = $PSVersionTable.PSVersion.ToString()
} | ConvertTo-Json -Compress
"""


def system_powershell() -> Path:
    # powershell_path() imports pywin32. Keep that DLL out of this long-lived
    # verifier: uv hardlinks its cached wheels into disposable environments, and
    # Windows cannot delete their pywintypes DLL while another link is mapped.
    completed = subprocess.run(
        [sys.executable, "-I", "-X", "utf8", "-c",
         "from telegram_search_mcp.windows_install import powershell_path; print(powershell_path())"],
        check=True, capture_output=True, encoding="utf-8", timeout=30,
    )
    return Path(completed.stdout.strip())


def read_windows_identity(powershell: Path) -> dict:
    """Read host facts independently of marketing names or runner labels."""
    encoded = base64.b64encode(WINDOWS_IDENTITY_CHECK.encode("utf-16-le")).decode("ascii")
    completed = subprocess.run(
        [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        check=True, capture_output=True, timeout=30,
    )
    identity = json.loads(completed.stdout.decode("utf-8-sig"))
    if not isinstance(identity, dict):
        raise RuntimeError("Windows identity must be a JSON object")
    return identity


def classify_windows(identity: dict) -> tuple[str, str | None]:
    """Windows 11 can report ProductName='Windows 10'; use kernel build/type."""
    product_type = identity.get("ProductType")
    if type(product_type) is not int or product_type not in {1, 2, 3}:
        raise RuntimeError("Windows ProductType is missing or invalid")
    try:
        version = tuple(int(part) for part in identity["Version"].split("."))
        build = int(identity["CurrentBuild"])
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise RuntimeError("Windows version/build evidence is missing or invalid") from error
    if len(version) != 3 or build != version[2]:
        raise RuntimeError("Registry and Win32_OperatingSystem build evidence disagree")
    if product_type in {2, 3}:
        return "server", None
    if version[:2] == (10, 0) and build >= 10240:
        return "desktop", "11" if build >= 22000 else "10"
    return "desktop", None


def require_windows_version(identity: dict, expected: str | None) -> tuple[str, str | None]:
    family, desktop_version = classify_windows(identity)
    if expected is not None and (family != "desktop" or desktop_version != expected):
        observed = "Windows Server" if family == "server" else f"desktop Windows {desktop_version or 'unknown'}"
        raise RuntimeError(f"Expected desktop Windows {expected}; actual OS is {observed}")
    return family, desktop_version


def write_report(path: Path | None, report: dict) -> None:
    serialized = json.dumps(report, indent=2, ensure_ascii=True) + "\n"
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # A failed run must replace prior success evidence as well.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as temporary:
            temporary.write(serialized)
            pending = Path(temporary.name)
        try:
            os.replace(pending, path)
        finally:
            pending.unlink(missing_ok=True)
    print("ACCEPTANCE_REPORT_JSON: " + json.dumps(report, sort_keys=True, ensure_ascii=True))


MCP_DISCOVERY_CHECK = """
import asyncio
import json
from pathlib import Path
import sys
from mcp import Client
from mcp.client.stdio import stdio_client, StdioServerParameters

async def main():
    entry = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))['mcpServers']
    assert len(entry) == 1, 'Expected exactly one installed Telegram MCP server'
    server = next(iter(entry.values()))
    parameters = StdioServerParameters(
        command=server['command'],
        args=server.get('args', []),
        env=server.get('env'),
    )
    async with Client(stdio_client(parameters)) as client:
        result = await client.list_tools()
        expected = {
            'telegram_search_messages', 'telegram_get_message',
            'telegram_get_context', 'telegram_get_media',
            'telegram_list_voice_messages', 'telegram_transcribe_voice',
            'telegram_list_chats', 'telegram_get_chat_history',
            'telegram_search_chat_messages', 'telegram_download_file',
            'telegram_get_message_thread', 'telegram_get_chat_draft',
            'telegram_get_scheduled_messages',
            'telegram_search_public_posts',
            'telegram_get_public_search_quota',
        }
        sending = sys.argv[2] == 'sending'
        if sending:
            expected.update({
                'telegram_prepare_message', 'telegram_send_message',
                'telegram_get_send_status', 'telegram_set_chat_draft', 'telegram_prepare_message_edit', 'telegram_edit_message', 'telegram_get_edit_status',
            })
        assert {tool.name for tool in result.tools} == expected
        assert len(result.tools) == (22 if sending else 15)
        writes = {
            'telegram_prepare_message', 'telegram_send_message',
            'telegram_transcribe_voice', 'telegram_download_file',
            'telegram_set_chat_draft',
            'telegram_prepare_message_edit', 'telegram_edit_message',
            'telegram_search_public_posts',
        }
        for tool in result.tools:
            assert tool.annotations is not None
            assert tool.annotations.read_only_hint is (tool.name not in writes)
            assert tool.annotations.destructive_hint is (tool.name in {'telegram_set_chat_draft', 'telegram_edit_message'})
            if tool.name == 'telegram_search_public_posts':
                assert tool.annotations.idempotent_hint is False
                properties = tool.input_schema['properties']
                assert properties['user_confirmed']['type'] == 'boolean'
                assert properties['user_confirmed']['default'] is False
                assert 'confirmation_token' in properties
            if tool.name == 'telegram_get_public_search_quota':
                assert tool.annotations.idempotent_hint is True

# Listing tools must not start the Telegram service or open a Telegram profile.
asyncio.run(main())
print('PASS: installed Windows plugin stdio discovery and annotations; ' + sys.argv[2])
"""

NATIVE_RUNTIME_CHECK = """
import sys
from telegram_search_mcp import __version__
from telegram_search_mcp.native_runtime import bundled_candidates, verify

assert sys.platform == 'win32'
assert sys.version_info[:2] == (3, 13)
assert __version__ == sys.argv[1]
candidates = bundled_candidates()
assert candidates, 'The Windows installation must include its pinned TDLib wheel'
verify(candidates[0])
print('PASS: installed Python/package version and native TDLib version/commit')
"""


def active_version(install: Path) -> Path:
    pointer = json.loads((install / "current.json").read_text(encoding="utf-8"))
    name = pointer["version"]
    assert isinstance(name, str) and name not in {"", ".", ".."}
    assert Path(name).name == name, "Activation must point at a direct child"
    version = (install / name).resolve(strict=True)
    assert version.parent == install, "Activation must stay inside the installation"
    assert (version / ".venv" / "Scripts" / "python.exe").is_file()
    return version


def plugin_configuration(marketplace: Path, install: Path, original: dict) -> Path:
    actual = json.loads(marketplace.read_text(encoding="utf-8"))
    for key, value in original.items():
        if key != "plugins":
            assert actual[key] == value, "Installation changed unrelated marketplace metadata"
    assert original["plugins"][0] in actual["plugins"], "Installation lost an existing plugin"
    entries = [entry for entry in actual["plugins"] if entry.get("name") == "telegram-mcp-work"]
    assert len(entries) == 1, "Installation must register Telegram exactly once"
    source = entries[0]["source"]
    relative = source["path"] if isinstance(source, dict) else source
    marketplace_root = marketplace.parent.parent.parent
    plugin = (marketplace_root / relative).resolve(strict=True)
    assert plugin.is_relative_to(marketplace_root), "Smoke plugin must remain disposable"
    assert (plugin / ".codex-plugin" / "plugin.json").is_file()
    configuration = plugin / ".mcp.json"
    servers = json.loads(configuration.read_text(encoding="utf-8"))["mcpServers"]
    assert len(servers) == 1
    entry = next(iter(servers.values()))
    assert Path(entry["command"]).name.lower() in {"powershell.exe", "pwsh.exe"}
    arguments = entry["args"]
    file_argument = next(index for index, value in enumerate(arguments) if value.lower() == "-file")
    assert Path(arguments[file_argument + 1]).resolve(strict=True) == install / "launch-mcp.ps1"
    return configuration


def exercise_archive(archive_path: Path, manifest: dict, powershell: Path, checks: list[dict]) -> None:
    def passed(name: str) -> None:
        checks.append({"name": name, "status": "passed"})

    # Exercise native PowerShell argument/JSON encoding for ordinary user paths.
    with tempfile.TemporaryDirectory(prefix="telegram windows install check ") as temporary:
        root = Path(temporary).resolve()
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(root / "исходники source")
        source = root / "исходники source" / manifest["archive_root"]
        install = root / "приложение with spaces"
        marketplace_root = root / "пользователь home"
        marketplace = marketplace_root / ".agents" / "plugins" / "marketplace.json"
        marketplace.parent.mkdir(parents=True)
        original = {
            "name": "windows-install-smoke",
            "interface": {"displayName": "Preserve this marketplace title"},
            "plugins": [{
                "name": "preserved-smoke-plugin",
                "source": {"source": "local", "path": "./plugins/preserved-smoke-plugin"},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                "category": "Productivity",
            }],
        }
        preserved = marketplace_root / "plugins" / "preserved-smoke-plugin"
        preserved.mkdir(parents=True)
        (preserved / "plugin.json").write_text(json.dumps({"name": "preserved-smoke-plugin"}), encoding="utf-8")
        marketplace.write_text(json.dumps(original), encoding="utf-8")
        command = [
            str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
            str(source / "install-windows.ps1"), "-PrepareOnly",
            "-InstallDir", str(install), "-MarketplacePath", str(marketplace),
        ]
        # Explicit disposable destinations; never override the user's home or credentials.
        subprocess.run(command, check=True, timeout=600)
        passed("initial_archive_install_in_unicode_path")
        first = active_version(install)
        first_python = first / ".venv" / "Scripts" / "python.exe"
        subprocess.run([str(first_python), "-I", "-m", "telegram_search_mcp.cli", "--help"], check=True, timeout=30)
        passed("installed_cli")
        subprocess.run([str(first_python), "-I", "-c", NATIVE_RUNTIME_CHECK, manifest["version"]], check=True, timeout=30)
        passed("initial_pinned_python_package_and_tdlib")
        configuration = plugin_configuration(marketplace, install, original)
        passed("initial_marketplace_preserves_existing_plugin")
        subprocess.run([str(first_python), "-I", "-c", MCP_DISCOVERY_CHECK, str(configuration), "default"], check=True, timeout=60)
        passed("initial_mcp_discovery_15_tools")
        subprocess.run([str(first_python), "-I", "-m", "telegram_search_mcp.cli", "sending", "on"], check=True, timeout=30)
        configuration = plugin_configuration(marketplace, install, original)
        subprocess.run([str(first_python), "-I", "-c", MCP_DISCOVERY_CHECK, str(configuration), "sending"], check=True, timeout=60)
        passed("sending_mcp_discovery_19_tools")
        configuration_before = configuration.read_bytes()
        # First install inherits the runner's PowerShell 7 -> Python environment.
        # Reinstall also works with an unrelated inherited module search path.
        external_modules = root / 'external modules'
        external_modules.mkdir()
        reinstall_environment = {key: value for key, value in os.environ.items() if key.upper() != 'PSMODULEPATH'}
        reinstall_environment['PSModulePath'] = str(external_modules)
        subprocess.run(command, check=True, timeout=600, env=reinstall_environment)
        second = active_version(install)
        assert second != first, "Reinstallation must activate a new immutable version"
        assert first_python.is_file(), "Reinstallation must preserve the former interpreter"
        passed("immutable_reinstall_with_unrelated_powershell_module_path")
        second_python = second / ".venv" / "Scripts" / "python.exe"
        configuration = plugin_configuration(marketplace, install, original)
        assert configuration.read_bytes() == configuration_before, "Reinstallation changed the stable plugin launcher"
        passed("reinstall_preserves_marketplace_and_stable_launcher")
        subprocess.run([str(second_python), "-I", "-c", NATIVE_RUNTIME_CHECK, manifest["version"]], check=True, timeout=30)
        passed("reinstalled_pinned_python_package_and_tdlib")
        subprocess.run([str(second_python), "-I", "-c", MCP_DISCOVERY_CHECK, str(configuration), "sending"], check=True, timeout=60)
        passed("reinstall_preserves_sending_19_tools")
        subprocess.run([str(second_python), "-I", "-m", "telegram_search_mcp.cli", "sending", "off"], check=True, timeout=30)
        configuration = plugin_configuration(marketplace, install, original)
        subprocess.run([str(second_python), "-I", "-c", MCP_DISCOVERY_CHECK, str(configuration), "default"], check=True, timeout=60)
        passed("sending_disabled_mcp_discovery_15_tools")
        print("PASS: verified archive, isolated native Windows installation and immutable update, marketplace and sending preference preservation, pinned TDLib, CLI and installed plugin discovery. No Telegram authorization or tool invocation performed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--expected-windows", choices=("10", "11"),
                        help="Require this desktop Windows version; reject Windows Server")
    parser.add_argument("--report-path", type=Path,
                        help="Write OS, archive hash and acceptance outcome as JSON outside the source tree")
    args = parser.parse_args()
    archive_path = args.archive.resolve()
    report_path = args.report_path.resolve() if args.report_path is not None else None
    if report_path == archive_path:
        parser.error("The report must not overwrite the release archive")
    report = {
        "schema_version": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "failed",
        "expected_windows_version": args.expected_windows,
        "archive": {"filename": archive_path.name},
        "host": {"hostname": platform.node(), "system": platform.system(),
                 "machine": platform.machine(), "python_version": platform.python_version()},
        "checks": [],
        "telegram_authorization_performed": False,
        "telegram_tools_invoked": False,
        "client_ui_tested": False,
    }
    try:
        if platform.system() != "Windows" or platform.machine().lower() not in {"amd64", "x86_64"}:
            raise RuntimeError("The actual installer smoke test requires native Windows x64")
        powershell = system_powershell()
        if not powershell.is_file():
            raise RuntimeError("System Windows PowerShell 5.1 must already be installed")
        identity = read_windows_identity(powershell)
        report["windows"] = identity
        family, desktop_version = classify_windows(identity)
        report["windows_family"] = family
        report["detected_desktop_windows_version"] = desktop_version
        require_windows_version(identity, args.expected_windows)
        report["checks"].append({"name": "actual_os_identity", "status": "passed"})
        observed = "Windows Server" if family == "server" else f"desktop Windows {desktop_version or 'unknown'}"
        print(f"Actual OS: {observed}; {identity['Caption']}; build {identity['CurrentBuild']}.{identity['UBR']}", flush=True)
        if shutil.which("uv") is None:
            raise RuntimeError("uv must already be installed and available on PATH")
        spec = importlib.util.spec_from_file_location("release", Path(__file__).with_name("release.py"))
        assert spec is not None and spec.loader is not None
        release = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(release)
        manifest = release.verify_archive(archive_path)
        report["archive"].update({"sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                                 "version": manifest["version"]})
        report["checks"].append({"name": "verified_release_archive", "status": "passed"})
        exercise_archive(archive_path, manifest, powershell, report["checks"])
        report["status"] = "passed"
    except BaseException as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_report(report_path, report)


if __name__ == "__main__":
    main()
