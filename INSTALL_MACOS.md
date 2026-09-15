# Installation, updates, and troubleshooting

## Requirements

- macOS with Homebrew and TDLib **1.8.0**.
- Codex app/CLI with local MCP support and/or Gemini CLI.
- Your own Telegram account and API credentials for a new login.
- Internet access for dependencies, GitHub updates, and Telegram.

Standard Homebrew paths support Apple Silicon and Intel layouts; see
[VERIFICATION.md](VERIFICATION.md) for the devices actually tested.
To let Codex handle setup, use [INSTALL_WITH_CODEX.md](INSTALL_WITH_CODEX.md).

## Download and install

[Download the source ZIP](https://github.com/prabchevski/telegram-search-mcp/archive/refs/heads/main.zip),
extract it, and open `install-macos.command`. No GitHub account or manual build is
required. You can also run a command from the extracted directory:

```sh
bash install-macos.command --clients codex
bash install-macos.command --clients gemini
bash install-macos.command --clients both
```

The interactive installer upgrades recognized older registrations, reuses a
compatible saved login, and enables daily checked-main updates by default.
It installs missing uv and TDLib through Homebrew and obtains a managed Python 3.13.
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

For Codex-assisted setup and migration:

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
owners need **one installation of 0.5 or later**, using the command above or Codex.
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

New MCP processes use the new version. Existing proxies start the latest shared
service after the previous service exits, normally after 10 idle minutes. Active
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
installed revision. A ZIP installation may show no revision until its first update.
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
Standard MCP discovery exposes exactly four tools even before authorization.

| Situation | Action |
| --- | --- |
| Setup incomplete / credentials missing | Open authorize.command in your private Terminal |
| Service running during auth | Finish requests, run service stop, then retry auth |
| Old profile in use | Close/restart its old MCP client; preserve tdlib.lock |
| Two different old accounts | Choose --migrate-profile codex or gemini |
| Queue full / timeout | Wait and retry a narrower query; check connectivity |
| Unsupported TDLib | Use Homebrew TDLib 1.8.0, not HEAD |
| MCP missing | Verify the selected registration and restart the client |
| waiting_for_ci | The current main commit has not completed successful checks |
| registration_changed | Review the owner's changed settings; updates did not restore them |
| Update failed / offline | Existing version remains; retry update when connected |
| Media does not display | Try a preview; rendering depends on the client |

## Fixed releases

[Releases](https://github.com/prabchevski/telegram-search-mcp/releases) contain tagged
source and optional checksum files. Their features are those of the selected tag;
older releases lack daily updates. Verify the matching `.sha256` before installing
a release archive. Use `--auto-update off` on 0.5+ if you want to keep a fixed version.

When asking for help, share the version and error message, not chat history,
Keychain data, databases, QR codes, login codes, or passwords.

Official references: [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli),
[Gemini CLI MCP](https://geminicli.com/docs/tools/mcp-server/),
[Telegram API credentials](https://core.telegram.org/api/obtaining_api_id).
