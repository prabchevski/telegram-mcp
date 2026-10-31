"""Reviewed Windows release assets, stored as Python for legacy updater compatibility.

The release builder parses this literal without executing the module. Windows
ZIPs contain these exact derived files; GitHub source snapshots intentionally do
not, because the installed 0.6.1 macOS updater only accepts its original formats.
@PACKAGE_VERSION@ is replaced with the checked pyproject.toml version.
"""

WINDOWS_ASSETS = {
    'install-windows.ps1': r"""# Native Windows x64 setup. No administrator rights or WSL required.
[CmdletBinding()]
param(
    [switch]$PrepareOnly,
    [string]$InstallDir,
    [string]$MarketplacePath,
    [ValidateSet('keep', 'on', 'off')][string]$AutoUpdate = 'keep'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# Python launched by PowerShell 7 inherits its incompatible module search path.
# Use only modules shipped with this PowerShell; this changes this process only.
$env:PSModulePath = [System.IO.Path]::Combine($PSHOME, 'Modules')
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or -not [Environment]::Is64BitProcess) {
    throw 'Run this installer in 64-bit PowerShell on Windows x64.'
}
if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw 'This release supports Windows x64. Native ARM64 is not supported.' }
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('telegram-mcp-setup-' + [guid]::NewGuid().ToString('N'))
try {
    New-Item -ItemType Directory -Path $temporary | Out-Null
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl.SetOwner($user)
    foreach ($sid in @($user, [Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $temporary -AclObject $acl
    # A pinned binary and checksum, never an unverified downloaded shell script.
    $zip = Join-Path $temporary 'uv.zip'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/astral-sh/uv/releases/download/0.11.15/uv-x86_64-pc-windows-msvc.zip' -OutFile $zip
    if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant() -ne '04b98d414a9000e25e5e0e7c9f53749e66b790cdaffc582829e6f58c544ee11c') {
        throw 'uv checksum verification failed.'
    }
    Expand-Archive -LiteralPath $zip -DestinationPath (Join-Path $temporary 'uv')
    $uv = (Get-ChildItem -LiteralPath (Join-Path $temporary 'uv') -Filter 'uv.exe' -Recurse | Select-Object -First 1).FullName
    if (-not $uv) { throw 'uv.exe is missing.' }
    # Bootstrap from the same hash-locked dependencies as the final install.
    # Project/user uv configuration must not redirect the interpreter or venv.
    Get-ChildItem Env: | Where-Object { $_.Name -like 'UV_*' -or $_.Name -in @('VIRTUAL_ENV','PYTHONPATH','PYTHONHOME') } | ForEach-Object { Remove-Item ('Env:' + $_.Name) }
    $arguments = @('run', '--project', $PSScriptRoot, '--frozen', '--no-dev', '--no-editable', '--no-config', '--managed-python', '--python', '3.13', 'python', '-I', '-X', 'utf8', (Join-Path $PSScriptRoot 'scripts/install-windows.py'), '--source', $PSScriptRoot, '--uv', $uv, '--auto-update', $AutoUpdate)
    if ($PrepareOnly) { $arguments += '--prepare-only' }
    if ($InstallDir) { $arguments += @('--install-dir', $InstallDir) }
    if ($MarketplacePath) { $arguments += @('--marketplace-path', $MarketplacePath) }
    & $uv @arguments
    if ($LASTEXITCODE -ne 0) { throw "Telegram MCP setup failed (exit $LASTEXITCODE). Existing login and installed versions are preserved." }
} finally {
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Recurse -Force }
}
""",
    'plugins/telegram-mcp-work/.codex-plugin/plugin.json': r"""{
  "name": "telegram-mcp-work",
  "version": "@PACKAGE_VERSION@",
  "description": "Telegram history and media for Codex and ChatGPT Work on Windows. Public search requires a live free-quota check and explicit permission. Own account required.",
  "author": {
    "name": "prabchevski"
  },
  "mcpServers": "./.mcp.json",
  "interface": {
    "displayName": "Telegram MCP",
    "shortDescription": "Search Telegram in Codex and ChatGPT Work.",
    "longDescription": "Local Windows Telegram integration for Codex and ChatGPT Work. Search account history and subscriptions first, read messages and download media. Before each broader public query, check live free quota, explain remaining attempts and wait for explicit user permission. Never offer Stars payment. Sending is disabled until enabled locally. Requires the Windows installer and your own Telegram login.",
    "developerName": "prabchevski",
    "category": "Productivity",
    "capabilities": [
      "Read"
    ],
    "defaultPrompt": "Find messages in my Telegram chats."
  }
}
""",
    'plugins/telegram-mcp-work/.mcp.json': r"""{
  "mcpServers": {
    "telegram": {
      "command": "powershell.exe",
      "args": ["-NoLogo", "-NoProfile", "-Command", "[Console]::Error.WriteLine('Run install-windows.ps1 to configure this plugin.'); exit 1"]
    }
  }
}
""",
}
