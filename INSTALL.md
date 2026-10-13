# Install from a repository link: instructions for AI agents

This is the entry point for **Codex, Gemini CLI, and ChatGPT Work agents with
local computer access** when a user provides the repository link and asks to
install or upgrade it. Perform the installation,
instead of only describing commands. That request authorizes normal dependency
setup, configuration of the selected client, and connection checks; do not ask
for the same general permission again. Do not change a personal installation
while reviewing or developing the code.

Canonical repository: https://github.com/prabchevski/telegram-mcp.
Read [AGENTS.md](AGENTS.md) first, then choose the route below.

## 1. Identify the operating system, architecture, and client

Use the current client's context and operating system. Unless the user names a
different client, configure the one they are using to request installation. Do not
ask for information already available in the environment. On macOS, select `both`
only when asked to configure both Codex and Gemini CLI.

| Environment | Package and next steps |
| --- | --- |
| macOS arm64 / x86_64, Codex app or CLI | `telegram-mcp-macos.zip`, macOS steps below, `--clients codex` |
| macOS arm64 / x86_64, Gemini CLI | `telegram-mcp-macos.zip`, macOS steps below, `--clients gemini` |
| Windows x64, Codex desktop / CLI with plugins | `telegram-mcp-windows.zip`, [INSTALL_WINDOWS.md](INSTALL_WINDOWS.md), personal plugin marketplace |
| Windows x64, ChatGPT Work with local plugins | The same Windows package and [INSTALL_WINDOWS.md](INSTALL_WINDOWS.md) |

On Windows, use native 64-bit PowerShell without WSL. Do not run the macOS installer
on Windows. This package does not configure Gemini in a browser or on a phone, or
ordinary ChatGPT web chats. Linux and native Windows ARM64 have no ready-to-use
installer. If the platform or client is unsupported, explain the specific
limitation; do not create a different connection method without an agreed request.

Check that the selected client is installed and supports this route. On Windows,
plugin access can depend on the app version and workspace policy. Configuration
instructions do not prove that the plugin is already active in the interface.
Windows checks cover Windows Server 2025 and separate x64 Windows 10 Enterprise
Evaluation 22H2 and Windows 11 Enterprise Evaluation 25H2 virtual machines. The
desktop checks passed as a standard user and verified installation, reinstallation,
and MCP discovery; they did not verify real Telegram login or the client interface.
See [VERIFICATION.md](VERIFICATION.md) for exact builds and evidence.

## 2. Download a published release and verify the archive

Use the **Assets from the latest published release**, rather than an arbitrary
branch, fork, or GitHub's standard `Source code (zip)`. The Windows installer and
plugin manifests are generated during packaging and are absent from GitHub's
ordinary source archive.

1. Read the [latest release](https://github.com/prabchevski/telegram-mcp/releases/latest)
   or its public API at `https://api.github.com/repos/prabchevski/telegram-mcp/releases/latest`.
   No GitHub account or user token is needed.
2. Retain the returned `tag_name` and use the `browser_download_url` for the required
   ZIP and its `.sha256` **from the same response and release**. Once selected, do
   not download them through two independent `/latest` requests: a release could
   change between requests. Do not use a draft or prerelease unless explicitly asked.
3. Save both files in a new local working directory. Do not overwrite another
   checkout or an existing installed version. Before extracting, compare the
   archive's SHA-256 with its entry in the matching `.sha256` file.
4. If the checksum differs, stop execution, download that same release again, and
   recheck it. Do not bypass verification. After a successful check, extract into
   a separate directory and read the included instructions.

On macOS, run this from the directory containing both files:

```sh
shasum -a 256 -c telegram-mcp-macos.zip.sha256
```

On Windows, run this from the directory containing both files:

```powershell
$expectedHash = ((Get-Content -LiteralPath '.\telegram-mcp-windows.zip.sha256' -Raw).Trim() -split '\s+')[0]
$actualHash = (Get-FileHash -LiteralPath '.\telegram-mcp-windows.zip' -Algorithm SHA256).Hash
if ($actualHash -ne $expectedHash) { throw 'Telegram MCP archive checksum mismatch' }
```

Releases are published only after the **Test and package** workflow succeeds,
including tests, archive-content checks, and installer checks. The checksum
confirms that the downloaded file matches the published archive. Installation
does not require cloning and rebuilding `main` or running tests on the user's account.

## 3. Preserve existing settings

Inspect only the client entries and installation markers related to Telegram MCP.
Do not print entire configuration files: they may contain other credentials or
settings. Respect the user's configuration directories. Preserve other MCP
servers, plugins, and settings.

Reuse compatible saved profiles **in place**. Never copy databases, sessions, or
keys between computers or users. If two old installations use different accounts,
ask the owner to choose; the accounts cannot be merged. Preserve manual registration
changes and report the specific conflict; do not delete settings to make the
installer succeed.

If a profile is busy, let active Telegram requests finish and release it by stopping
the service or old client normally. Never delete `tdlib.lock`, copy a live database,
or terminate unrelated processes.

## 4. Install for the selected platform

### macOS: Codex or Gemini CLI

The installer requires Homebrew and working Apple command-line tools. It installs
uv, Python 3.13, and the dependencies in `uv.lock`. TDLib 1.8.67 is pinned by version
and commit: Apple Silicon uses a verified wheel; Intel builds the official source
once with cmake, gperf, and OpenSSL, which can take several minutes.
If Homebrew or Apple's tools are missing, help install them using their official
instructions. System passwords and dialogs stay in the owner's local interface.
Do not change system Python permissions or unrelated Python environments.

From the extracted directory, run one command:

```sh
# Codex
bash install-macos.command --clients codex --prepare-only --upgrade --auto-update on
```

```sh
# Gemini CLI
bash install-macos.command --clients gemini --prepare-only --upgrade --auto-update on
```

Use `--clients both` only when both clients are requested. If the user requested
a fixed version or disabled automatic updates, replace `--auto-update on` with
`--auto-update off`.

`--prepare-only` leaves the first login for the owner's local window.
`--upgrade` also updates recognized existing Telegram registrations in the other
client, but does not enable a client that was not configured. Respect `CODEX_HOME`
and `GEMINI_CLI_HOME`, or pass absolute `--codex-config` / `--gemini-config` paths.
The installer retains private backups and preserves other MCP entries.

Use **the installation root printed by the installer**. It is usually
`~/Applications/TelegramSearchMCP`; an old Codex 0.2 installation uses
`~/Applications/TelegramSearchMCPShared`. Do not delete the previous installation.
Compatible Codex 0.2 and Gemini 0.3 profiles retain their Keychain namespace; an
already configured shared 0.4 profile takes precedence. If old accounts differ,
ask the owner to choose and retry with `--migrate-profile codex` or
`--migrate-profile gemini`. `--migrate-profile none` declines an old profile when
no shared profile exists yet. See [macOS migration and upgrades](INSTALL_MACOS.md).

### Windows: Codex or ChatGPT Work

Follow [INSTALL_WINDOWS.md](INSTALL_WINDOWS.md) using the verified Windows archive.
Use its preparation mode without login, then open a separate local authorization
window. Both apps use the **personal plugin marketplace**; do not manually add a
duplicate server registration in Codex TOML.
The installer prepares uv, Python, pinned TDLib, and the plugin. Codex CLI versions
with plugin support provide `/plugins`; follow the guide to check whether automatic
activation is available, and preserve the owner's choices during upgrades.

The default installation path is `%LOCALAPPDATA%\Programs\TelegramMCP`; its stable
entry points are `tgsearch.ps1` and `launch-mcp.ps1`. Retain the path printed by the
installer if it differs. After preparation, enable the plugin in the app.
Do not claim that a marketplace entry already means the plugin is active in a chat.

## 5. Check an existing login or open local authorization

First run `tgsearch doctor`, then, when preparation is complete,
`tgsearch doctor --connect` through the installed entry point. These commands
check setup and authorization without printing secrets. Do not read conversations,
run public searches, transcribe messages, or send anything to verify installation.

If login is missing or expired, open `authorize.command` on macOS, or run
`tgsearch.ps1 auth` as described in the Windows guide, **in a separate local window
for the owner**. On macOS, `open` can launch `authorize.command`.
Do not run interactive authorization in an agent terminal with recorded output
or capture the authorization window. The owner enters their own `api_id` and
`api_hash` from [my.telegram.org](https://my.telegram.org), scans the QR code, and
enters a login code or 2FA password if needed. Never ask for secrets, QR codes,
or login links in the AI chat.

Complete everything that does not require login before reporting the remaining
owner action. After authorization, repeat `doctor --connect`. If continuing
requires an app restart, explain what has been checked and what must be verified
in a new chat. Do not report missing authorization as success.

## 6. Verify MCP and report the result

- Confirm the installed version with `tgsearch --version` and check service status.
- On macOS, run `current/client-config verify --clients codex` (or `gemini` /
  `both`) using the same custom configuration paths, if applicable.
  Check `current/tgsearch updates status`.
- On Windows, check the personal marketplace entry and installed plugin. If the
  app needs a restart, complete everything else and leave one clear instruction:
  restart, enable Telegram MCP, and open a new chat.
- Perform MCP initialization and `tools/list` when available: **15 tools** by
  default, **22** if sending was already enabled. This does not require reading
  messages. Writing a registration file alone does not verify an MCP connection.
- Check `doctor --connect` after login. Do not run old TDLib against a database
  already upgraded by a newer version. Active service operations must finish
  before switching versions; a new client can replace an idle old service normally.
- Report the operating system and client, version/release, actual installation
  path, preserved or new login, checks completed, update mode, and remaining action.
  Distinguish “prepared”, “login verified”, and “tools available in the client”.

Briefly explain the search workflow: search history and subscriptions first; if
broader public search would help, check the live free quota, explain any attempt
it would consume, and obtain explicit permission for the new query. Checking the
quota uses no search attempt; never offer Stars payments. MCP itself sends these
instructions to each client.

By default, macOS checks `main` daily after successful CI. Windows schedules a
daily/logon current-user task and installs only a verified published Windows
release at the exact successful CI SHA. Updates preserve login, separate installed
versions, sending and client preferences; active Telegram work finishes normally.
Pre-0.11 Windows installations need the new installer once. Explicit update
opt-outs are retained. Use `updates status`, `update --check` and `updates off`
through the installed platform entry point. See [INSTALL_MACOS.md](INSTALL_MACOS.md) and
[INSTALL_WINDOWS.md](INSTALL_WINDOWS.md) for older versions and recovery of
modified registrations.
