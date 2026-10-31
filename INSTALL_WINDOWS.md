# Telegram MCP for Codex and ChatGPT Work on Windows

You need Windows x64, 64-bit PowerShell, internet access, and Codex or ChatGPT Work with local plugin support. Codex desktop and Work use the same personal-marketplace plugin and installer. Administrator rights and WSL are not required. Native ARM64 is not supported. Daily verified updates run as the current user by default; an explicit opt-out is preserved.

Check the client's own operating-system requirements as well. OpenAI [recommends Windows 11 for its Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox#windows-version-matrix); recent, fully updated Windows 10 is supported on a best-effort basis. An installer check does not verify every Windows build or the client's interface.

Give your local agent the repository link and say: “Install Telegram MCP for Codex on this computer.” The agent performs the download, verification, and setup below. Complete Telegram login yourself in a local window; never share secrets with the AI. An ordinary cloud chat without access to your computer cannot perform a local installation.

## Installation

1. Open the [latest release](https://github.com/prabchevski/telegram-mcp/releases/latest) and download `telegram-mcp-windows.zip` and `telegram-mcp-windows.zip.sha256` from the same release. Compare `Get-FileHash .\telegram-mcp-windows.zip -Algorithm SHA256` with the checksum, then extract the archive. The included `install-windows.ps1` is in the archive's `telegram-mcp-macos` directory: this name is retained for upgrade compatibility, and the package contains both installers. GitHub's standard **Source code (zip)** does not include this PowerShell file.
2. Open PowerShell in the extracted directory containing `install-windows.ps1` and run:

   ```powershell
   powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\install-windows.ps1
   ```

   `Bypass` applies only to this process. The installer verifies the pinned uv download's SHA-256, installs Python 3.13 and dependencies from `uv.lock`, and checks TDLib's version and commit.
3. In the same local window, enter your `api_id` and `api_hash` from [my.telegram.org](https://my.telegram.org). Confirm the QR login in Telegram → Settings → Devices. Login codes and 2FA passwords are entered without echoing. Never send them to a ChatGPT chat.
4. Restart Codex or ChatGPT. In **Codex** or **Work**, open **Plugins**, choose **Personal** (or your existing personal marketplace's name), and install **Telegram MCP**. Start a new chat with the plugin enabled. Current Codex CLI versions expose the same marketplace through `/plugins`.
5. Try: “Find messages in my Telegram about Friday's meeting.”

A new installation exposes 15 tools. Sending messages and changing drafts are disabled. Retrieved messages and media are shared with the selected AI client when tools are used. Secret Chats are not supported.

This package registers a local plugin; it is not published in OpenAI's public marketplace. Workspace policies may also restrict plugin access. Automated checks cover Windows Server 2025. Separate installation, reinstallation, generated-configuration, and MCP discovery checks passed on x64 Windows 10 Enterprise Evaluation 22H2 (build 19045.2006) and Windows 11 Enterprise Evaluation 25H2 (build 26200.6584) virtual machines. Both desktop runs used a standard user account, PowerShell 5.1, and Python 3.13.13. The Codex/ChatGPT interface and real Telegram login remain unverified and must be checked on the target computer; see [VERIFICATION.md](VERIFICATION.md). Do not add a duplicate server manually in `config.toml` alongside the plugin.

The agent searches your accessible account history and subscriptions first. It
may then offer a separate public-post search, including channels you do not
follow. Before each new query, it must call
`telegram_get_public_search_quota(query)`: this reports the account's current
quota without consuming a search attempt. No fixed daily allowance is assumed.

The agent must explain the remaining free attempts, any required wait, and
whether the proposed query would use an attempt or is already free. It must ask
“May I run this search?” and wait for your answer. For example: “I found these
posts in your subscriptions. I can also search public channels you do not follow
for ‘artificial intelligence’. There are N free attempts remaining, and this
query would use one. Shall I search?” The number and conditions must come from
Telegram's current response.

The server requires the quota check's `confirmation_token` and
`user_confirmed=true`. The token is valid for five minutes and can be used once,
for that account, query, and quota snapshot. If the quota changes or the token
expires, the agent must check and ask again. The confirmation flag reports the
agent's account of your consent: the server does not observe the human
conversation itself. MCP sends the instructions to every client.
Continuing an approved query through `next_cursor` is free and requires no new
consent; rewording the query requires a fresh check and permission.

Stars payments are neither supported nor offered: search always passes
`star_count=0`, without paid retries or purchases. Limits, missing consent, and
errors are reported separately from a successful search with no matches.

## Program and data locations

- Program: `%LOCALAPPDATA%\Programs\TelegramMCP`, with a separate directory for each installed version.
- Personal marketplace: `%USERPROFILE%\.agents\plugins\marketplace.json`; plugin: `%USERPROFILE%\plugins\telegram-mcp-work`.
- Profile: `%LOCALAPPDATA%\TelegramSearchMCPShared`, private to the current user.
- `api_hash` and database encryption key: Windows Credential Manager. The Telegram session stays on this computer.

Do not copy your profile, database, secrets, or installed environment to another person. Share only the clean source archive. Each person signs into their own Telegram account.

## Verification and management

```powershell
$tg = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Programs\TelegramMCP\tgsearch.ps1'
& $tg doctor --connect
& $tg service status
& $tg service stop
```

Stop the service with the command above and let the current operation finish instead of deleting `tdlib.lock`. One shared service supports all windows and chats.

To enable sending when needed:

```powershell
& $tg sending on
# Restart Codex or ChatGPT and start a new chat: 22 tools are now available.
& $tg sending off
```

Sending requires your explicit recipient and content instructions; preparation, confirmed sending, and status checks use the same `draft_id`. Do not create another send when the status is `pending` or `unknown`.

## Updates

For an installation older than 0.11, download a new verified release and rerun
`install-windows.ps1` once. This adds a private stable uv runtime and a daily/logon
Task Scheduler task for the current user. No administrator rights or saved password
are required. Use `-AutoUpdate off` during installation to opt out.

```powershell
& $tg updates status
& $tg update --check
& $tg update
& $tg updates off
& $tg updates on
```

The updater selects a published Windows release for the exact canonical main
commit whose full CI passed. It verifies HTTPS redirects, the archive checksum,
GitHub digest, source revision and complete safe inventory before running the
candidate installer. Downgrade, divergent/unpublished revisions and owner-modified
plugin registration are rejected. `--check` only inspects availability.

Older installed versions, Telegram login and the sending preference are retained.
New processes use the new version; active shared-service work finishes normally.
Restart Codex or ChatGPT Work after an update to load new tool definitions. Status
retains the restart notice; no app or native Telegram operation is forcibly stopped.

The installer preserves other plugins and marketplace settings. If you removed or modified the Telegram registration, an update does not restore it; the result `preserved_user_changes` reports this. Changes to the owned scheduled task are also preserved; an opt-out prevents scheduled package updates even when an edited task cannot be removed safely.

## Removal

Run `updates off`, disable or remove Telegram MCP in Plugins, and run `service stop`. Remove only the `telegram-mcp-work` entry from your personal `marketplace.json` and the `%USERPROFILE%\plugins\telegram-mcp-work` directory, then remove the program directory. The saved profile and Credential Manager entries remain available for reinstallation.

To revoke access completely, terminate the corresponding session in the official Telegram app → Settings → Devices. After stopping the service, you can remove `%LOCALAPPDATA%\TelegramSearchMCPShared` and its `local.unofficial-telegram-search-mcp-shared` entries in Windows Credential Manager. Do not remove other applications' entries.

## Check the installer without signing in

```powershell
.\install-windows.ps1 -PrepareOnly -InstallDir 'C:\path\to\temporary\application' -MarketplacePath 'C:\path\to\temporary\home\.agents\plugins\marketplace.json'
```

This mode is for CI and package checks. It does not authorize Telegram. Do not use a temporary marketplace for a normal installation: Codex and ChatGPT automatically discover the current user's marketplace.

## Instructions for the installing agent

Read [INSTALL.md](INSTALL.md) first. Identify the platform and client, and check for `%LOCALAPPDATA%\Programs\TelegramMCP\windows-install.json` before installation: existing installations require preserving the user's plugin choices. Do not run the macOS installer or WSL for a native Windows client.

This PowerShell block downloads the ZIP and SHA-256 from one resolved latest release, verifies them, and extracts the package into a separate directory. It does not run the installer or request secrets:

```powershell
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$release = Invoke-RestMethod -Uri 'https://api.github.com/repos/prabchevski/telegram-mcp/releases/latest'
$assets = @($release.assets | Where-Object { $_.name -in @('telegram-mcp-windows.zip', 'telegram-mcp-windows.zip.sha256') })
if ($assets.Count -ne 2) { throw 'The release is missing its Windows archive or checksum.' }
$setup = Join-Path ([IO.Path]::GetTempPath()) ('telegram-mcp-download-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $setup | Out-Null
foreach ($asset in $assets) {
    Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile (Join-Path $setup $asset.name)
}
$archive = Join-Path $setup 'telegram-mcp-windows.zip'
$checksum = (Get-Content -LiteralPath "$archive.sha256" -Raw).Trim()
if ($checksum -notmatch '^([a-fA-F0-9]{64})\s+telegram-mcp-windows\.zip$') { throw 'Invalid checksum file.' }
$expected = $Matches[1]
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) { throw 'Archive checksum mismatch.' }
Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $setup 'extracted')
$installer = Join-Path $setup 'extracted\telegram-mcp-macos\install-windows.ps1'
if (-not (Test-Path -LiteralPath $installer)) { throw 'The release has no Windows installer.' }
Write-Output $installer
```

Run the located file in **interactive local** PowerShell:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $installer
```

If the agent's tools do not let the user enter secrets privately, prepare the package with `-PrepareOnly`, then open a local terminal for `tgsearch.ps1 auth` and `tgsearch.ps1 doctor --connect`. Never ask for the `api_hash`, code, or password in chat, or save them in commands, logs, or instruction files. `-PrepareOnly` does not mean Telegram is ready to use.

On a **first installation**, if Codex CLI is available and `codex plugin add --help` confirms the command exists, the agent can finish activating the plugin without manually editing `config.toml`:

```powershell
$marketplace = Get-Content -LiteralPath (Join-Path $env:USERPROFILE '.agents\plugins\marketplace.json') -Raw | ConvertFrom-Json
if ($marketplace.name -notmatch '^[A-Za-z0-9_-]+$') { throw 'Invalid marketplace name.' }
codex plugin add "telegram-mcp-work@$($marketplace.name)" --json
if ($LASTEXITCODE -ne 0) { throw 'Plugin activation failed; use Plugins in the app.' }
```

If that command is unavailable, use **Plugins → Personal → Telegram MCP** in the app or `/plugins` in a Codex CLI version with plugin support. Do not install an additional CLI solely for this command. The `telegram-mcp-work` identifier is retained for compatibility; Codex uses the same plugin. During upgrades, do not reactivate a plugin the owner disabled or removed, or restore an entry after `preserved_user_changes`, without a separate request.

After restarting, verify that 15 tools appear and run `doctor --connect`. Verifying installation does not require reading conversations or consuming public-search quota. If the user separately requests a trial search, start with account history. Report what is installed and verified separately from what still requires Telegram login, an app restart, or plugin activation.

## Official sources

- [Local MCP servers in ChatGPT desktop](https://learn.chatgpt.com/docs/extend/mcp)
- [Plugins and local marketplaces](https://developers.openai.com/plugins/build/plugins)
- [Install plugins in Codex CLI through /plugins](https://developers.openai.com/learn/developers-codex-plugin#install-the-plugin)
- [ChatGPT desktop on Windows](https://learn.chatgpt.com/docs/windows/windows-app)
