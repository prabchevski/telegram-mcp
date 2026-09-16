# Installation, updates, and troubleshooting

## Requirements

- macOS with Homebrew; the installer supplies pinned TDLib **1.8.67**.
- Codex app/CLI with local MCP support and/or Gemini CLI.
- Your own Telegram account and API credentials for a new login.
- Internet access for dependencies, GitHub updates, and Telegram.

Standard Homebrew paths support Apple Silicon and Intel layouts; see
[VERIFICATION.md](VERIFICATION.md) for the devices actually tested.
For setup through Codex or Gemini CLI, use [INSTALL.md](INSTALL.md).

## Download and install

[Download the verified release ZIP](https://github.com/prabchevski/telegram-mcp/releases/latest/download/telegram-mcp-macos.zip),
extract it, and open `install-macos.command`. No GitHub account or manual build is
required. You can also run a command from the extracted directory:

```sh
bash install-macos.command --clients codex
bash install-macos.command --clients gemini
bash install-macos.command --clients both
```

The interactive installer upgrades recognized older registrations, reuses a
compatible saved login, and enables daily checked-main updates by default.
It installs uv through Homebrew, obtains managed Python 3.13, and prepares pinned TDLib.
Homebrew must already be installed. The account owner handles any required system
approval and first Telegram login in Terminal. Existing credentials stay local.

The installer prints the program root, normally `~/Applications/TelegramSearchMCP`.
If Codex 0.2 already occupies that directory, it uses
`~/Applications/TelegramSearchMCPShared` instead. **Use the printed root in commands
below** if yours differs. Each version has separate source and Python dependencies.
Stable launchers resolve the current version before starting Python.

Codex's registration is `telegram_search`; Gemini's is `telegram-search`. Private
backups preserve original settings. Other MCP entries are retained. Gemini JSON
formatting/comments may change, with the original kept in its backup. The
installer does not itself verify that the client app is installed or has reloaded.

## Installation without interactive authorization

For AI-assisted setup and migration (use `--clients gemini` for Gemini CLI):

```sh
bash install-macos.command --clients codex --prepare-only --upgrade --auto-update on
```

`--prepare-only` skips signing in. Without explicit flags, it leaves update settings
unchanged and does not select an old profile. `--upgrade` enables recognized legacy
registration replacement and automatic compatible profile selection. An already
configured second client is refreshed too; an absent second client is not enabled.

Check a saved login with `current/tgsearch doctor`, then `doctor --connect`. For a
new/expired login, open the root's `authorize.command` in your own Terminal. Never
paste api_hash, a QR/login link, login codes, or a 2FA password into an AI chat.

To prepare files without client registration, use `--clients none --prepare-only`.
For isolated checks, also use absolute `--install-dir`, `--codex-config`, and
`--gemini-config` paths, `--migrate-profile none`, and `--auto-update keep`.
`--skip-system-deps` requires prepare-only and an existing uv; it does not install
Homebrew packages. Do not enable a real background schedule in development tests.

## Upgrade old archives and preserve a login

Codex 0.2, Gemini 0.3, and shared 0.4 archives do not contain this updater. Their
owners need **one installation of the current release**, using the command above or Codex.
The maintainer cannot remotely change already downloaded archives.

- A configured shared 0.4 profile remains selected.
- Compatible Codex 0.2 or Gemini 0.3 profiles are reused **in place**, with their
  original Keychain service. No database or secret is copied into the new program.
- If both legacy profiles use the same account, the Codex profile is selected.
- If they use different accounts, choose explicitly with `--migrate-profile codex`
  or `--migrate-profile gemini`. Accounts are never merged.
- `--migrate-profile none` skips adopting an old profile. It does not undo an
  existing selection or switch an already configured shared account.
- A private, compatible policy and inactive TDLib lock are required for adoption.
  Finish old Telegram requests and close/restart the old MCP client if it is busy.
  Keep tdlib.lock on disk; do not copy the DB or kill unrelated processes.

Recognized old registrations, including known alternate aliases, are replaced.
Unknown custom entries are refused with an explanation. Original source manifests
must still exist for legacy launcher recognition. Review unfamiliar entries before
changing them. Old program directories are retained. Restart the clients after
this first upgrade so they stop using their old registrations.

A valid, unrevoked saved session normally needs no QR login. A revoked session or
missing Keychain credentials still requires the owner's authorization.

## Automatic updates

The selected default is **canonical main, after successful CI, once a day**.
A per-user macOS LaunchAgent checks at login and every 24 hours, with at most one
scheduled check in a 24-hour period. The Mac must be running and the user logged in.
Sleep, offline operation, GitHub limits, or failed/pending checks can delay an update.
There is no server pushing commands to other people's computers.

The updater checks the exact main commit's successful `Test and package` workflow,
downloads source pinned to that SHA from GitHub, validates archive paths/types,
installs locked dependencies into a separate version, and verifies package imports
before switching `current`. It refuses version downgrades and divergent Git history.
Failed downloads or dependency installs keep the current program. A changed or
removed client registration is left alone; update installation is cancelled.

For managed 0.6.1 installs with updates enabled, the new package also upgrades
unchanged standard Codex/Gemini tool lists on the next updater or MCP start.
This normally takes up to two daily checks on Apple Silicon. A macOS notification
asks for a client restart; `updates status` retains the same notice. Sending settings
are preserved. If migration happens during client startup, restart the client again
to reload its settings. Intel may need an extra check while pinned TDLib builds in
the background; the previous version remains active until preparation succeeds.

The pre-rename 0.6.0 archive needs a one-time installer run because it requires the
old GitHub repository identity. An installation already on 0.7.0 with the old 0.6
tool lists also needs that repair: its updater stops at `registration_changed`.
See [upgrade details](README.md#voice-messages-and-telegram-transcription).

New MCP processes use the new version. A new client gracefully replaces an idle
older shared service; otherwise the service exits after 10 idle minutes. Active
requests are not interrupted. To activate immediately, finish requests, stop this
package's service, and restart the client. Older version directories remain for
recovery and are not automatically deleted while processes might use them.

```sh
"$HOME/Applications/TelegramSearchMCP/current/tgsearch" updates status
"$HOME/Applications/TelegramSearchMCP/current/tgsearch" updates off
"$HOME/Applications/TelegramSearchMCP/current/tgsearch" updates on
"$HOME/Applications/TelegramSearchMCP/current/tgsearch" update --check
"$HOME/Applications/TelegramSearchMCP/current/tgsearch" update
```

`update --check` only reports availability; `update` installs a checked update now,
even if daily checking is disabled. `updates status` reports saved settings and
installed revision and any saved restart notice. A ZIP installation may show no
revision until its first update.
The schedule lives in `~/Library/LaunchAgents/io.github.prabchevski.telegram-search-mcp.update.<id>.plist`.
The private `updates.log` in the program root records update outcomes/errors, not
Telegram messages. If enabling reports that launchd could not start, run
`updates on` in the logged-in desktop session or log out and back in.

To restore a specific source version, disable daily updates, finish requests, stop
the service, and rerun that version's installer with `--auto-update off`. Retain a
known compatible version; automatic database-format rollback is not provided.

## Add a client and verify

Rerun the installer with the desired `--clients` value, or use:

```sh
"$HOME/Applications/TelegramSearchMCP/current/client-config" register --clients both
```

This records the added client for updates. It does not need another Telegram login.
Repeat any custom `--codex-config` / `--gemini-config` paths during registration,
verification, or removal; they do not change the command's defaults.

```sh
"$HOME/Applications/TelegramSearchMCP/current/tgsearch" --version
"$HOME/Applications/TelegramSearchMCP/current/tgsearch" doctor
"$HOME/Applications/TelegramSearchMCP/current/tgsearch" service status
"$HOME/Applications/TelegramSearchMCP/current/tgsearch" doctor --connect
"$HOME/Applications/TelegramSearchMCP/current/client-config" verify --clients codex
```

Use `gemini` or `both` when applicable. `doctor` checks local setup without printing
secret values; `doctor --connect` verifies saved authorization through the service.
`service stop` closes the shared session gracefully without logging out; the next
request starts it again. Restart/reload clients to apply a new registration.

In Codex CLI, inspect `codex mcp get telegram_search`. In Gemini CLI, use
`gemini mcp list` and `/mcp`; the working directory must be trusted by Gemini.
Standard MCP discovery exposes six tools before authorization, including voice listing
and explicitly requested speech recognition. Opt-in
sending adds three tools; see README.md, Optional text and file sending.

| Situation | Action |
| --- | --- |
| Setup incomplete / credentials missing | Open authorize.command in your private Terminal |
| Service running during auth | Finish requests, run service stop, then retry auth |
| Old profile in use | Close/restart its old MCP client; preserve tdlib.lock |
| Two different old accounts | Choose --migrate-profile codex or gemini |
| Queue full / timeout | Wait and retry a narrower query; check connectivity |
| Unsupported TDLib | Rerun the installer to restore the pinned TDLib runtime |
| MCP missing | Verify the selected registration and restart the client |
| waiting_for_ci | The current main commit has not completed successful checks |
| registration_changed | Preserve owner edits; for 0.7.0 with old 0.6 tool lists, rerun the latest installer once |
| Update failed / offline | Existing version remains; retry update when connected |
| Media does not display | Try a preview; rendering depends on the client |

## Fixed releases

[Releases](https://github.com/prabchevski/telegram-mcp/releases/latest) contain
the tested source archive, `.sha256`, inventory and Python wheel. The direct download
uses the stable asset name `telegram-mcp-macos.zip`; its version is recorded inside.
Verify its matching `.sha256` before installing. Historical tags retain their
original features; 0.4.0 lacks daily updates. Use `--auto-update off` if you want to
keep a fixed version. Release publication follows successful tests and installer
checks for each new version on main.

When asking for help, share the version and error message, not chat history,
Keychain data, databases, QR codes, login codes, or passwords.

Official references: [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli),
[Gemini CLI MCP](https://geminicli.com/docs/tools/mcp-server/),
[Telegram API credentials](https://core.telegram.org/api/obtaining_api_id).

## Voice recognition in 0.7

Telegram-native transcription and voice listing are registered by default: six tools,
or nine when sending is enabled. Stop the idle old service and restart the MCP client
after this upgrade. Apple Silicon uses a locked TDLib wheel; Intel builds pinned
source once and reuses it. Keep the adopted profile and Keychain in place. Do not
roll back the native library after it has upgraded the Telegram database.
See README.md for transcription states and Telegram account limits.
