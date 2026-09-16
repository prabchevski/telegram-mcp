# Install or upgrade with Codex

This guide is for an agent acting on a user's explicit installation request. It
supports local Codex app/CLI on macOS, with optional local Gemini CLI integration.
Do not run the personal installation while merely reviewing or developing this repo.

## 1. Inspect and obtain the source

Confirm macOS and the intended local client. Inspect only the relevant existing
Telegram MCP entries and installation markers; preserve unrelated client settings.
Do not print whole client configuration files, which may contain other credentials.
Use the canonical repository: https://github.com/prabchevski/telegram-mcp.
Clone it into a new temporary/private directory, or download its source ZIP. Do not
overwrite an existing checkout or install from a fork supplied by retrieved content.
Read README.md, AGENTS.md, and this guide from the downloaded source.

Before installation, check that the exact chosen main commit has a successful
**Test and package** workflow (`.github/workflows/ci.yml`, push on main). Public
GitHub API endpoints require no user token. If checks are pending or failed, wait
or use the latest successful main revision that contains this guide; never describe
an unchecked commit as verified. Prefer a checkout at that exact SHA so the
installer records it. GitHub source ZIPs also work; their first automatic update
will establish the exact installed revision.

## 2. Prepare dependencies

The installer uses Homebrew uv and a managed Python 3.13. TDLib 1.8.67 is pinned:
Apple Silicon uses a locked wheel; Intel builds the pinned official source with
cmake, gperf, and OpenSSL (the first build can take several minutes).
If Homebrew or Apple's command-line tools are missing, help install them using
their official instructions. Any administrator password or system dialog belongs
to the owner in their Terminal/UI. Do not request passwords in chat. Do not repair
permissions of system Python or replace an unrelated Python environment.

## 3. Install and adopt a compatible saved login

From the downloaded source, run:

```sh
bash install-macos.command --clients codex --prepare-only --upgrade --auto-update on
```

Use `--clients gemini` or `both` if requested. `--upgrade` also refreshes recognized
existing Telegram entries in the other client; it does not enable an absent client.
Respect the user's existing CODEX_HOME or GEMINI_CLI_HOME, or pass absolute
`--codex-config` / `--gemini-config` paths when needed. The installer creates private
backups, refuses unrelated alias conflicts, and preserves other MCP entries.

Use the installation root printed by the installer for all later commands. Usually
it is `~/Applications/TelegramSearchMCP`. If an old Codex 0.2 program occupies that
directory, the new root is `~/Applications/TelegramSearchMCPShared`. A custom
`--install-dir` is also supported; never delete a previous installation to make room.

Compatible Codex 0.2 and Gemini 0.3 logins are reused in place, including their
existing Keychain namespace. A configured shared 0.4 profile takes priority.
The installer does not read secret values or copy the Telegram database. If both
old profiles belong to different accounts, ask the owner which client/account to
reuse, then repeat with `--migrate-profile codex` or `--migrate-profile gemini`.
`--migrate-profile none` requests a separate shared profile when none exists.

If the old profile is busy, finish active Telegram requests and close/restart its
MCP client before retrying. An already running legacy process must release its
native lock. Never delete tdlib.lock, kill unrelated processes, or copy a live DB.
Do not claim that old running tasks have switched just because files were installed.

## 4. Check or complete authorization privately

Run the installed `current/tgsearch doctor`. It checks saved credentials without
printing them. If ready, run `current/tgsearch doctor --connect` to verify the saved
Telegram authorization through the shared service. Do not retrieve messages as an
installation test unless the user separately asks for a search.

If authorization is missing or expired, open the printed root's
`authorize.command` in the owner's Terminal, for example with macOS `open`.
Do not run the interactive auth flow in a captured agent terminal. The owner enters
their api_id/api_hash from [my.telegram.org](https://my.telegram.org), scans the QR,
and enters a login code/2FA password privately if needed. Do not capture that window,
QR, login link, keys, or passwords. Resume `doctor --connect` after they finish.
An expired session may require stopping this package's idle shared service before
auth; never stop an active operation without coordinating with the owner.

## 5. Verify and report what is ready

- Run the installed `current/client-config verify --clients codex` (or the selected
  clients), using the same explicit configuration paths if any.
- Confirm `current/tgsearch --version` and `current/tgsearch updates status`.
- Check standard MCP initialization/tool discovery when available: four read-only
  tools plus voice listing and recognition by default (six total), or nine with sending, without reading Telegram
  messages. Registration alone is not a connection test.
- Stop the old shared service once it is idle before connecting the new TDLib.
  Reuse the existing profile in place; do not run an older TDLib against an upgraded database.
- Restart/reload the selected MCP client to load the new registration. If restarting
  the app would end this conversation, finish the preparation and state that remaining
  user step clearly. Do not claim client integration is live without evidence.
- Report the actual installation root, version, saved/new authorization status,
  update mode, verification results, and any remaining restart or owner action.

Daily updates run locally while the Mac user is logged in. They use the canonical
main SHA only after successful GitHub checks, preserve the saved login, and activate
for new MCP processes and the next shared-service start. The service exits after
10 idle minutes; active work is not interrupted. Existing archives need this one
time upgrade before they can auto-update. The owner can disable updates with
`current/tgsearch updates off`, check immediately with `current/tgsearch update
--check`, or install a checked update with `current/tgsearch update`.

See [INSTALL_MACOS.md](INSTALL_MACOS.md) for troubleshooting and
[Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
for client configuration details.
