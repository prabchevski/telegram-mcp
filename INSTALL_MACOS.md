# Installation, updates, and troubleshooting

## Requirements

- macOS with Homebrew and an available TDLib **1.8.0** package.
- Codex (app/CLI with local MCP support) and/or Gemini CLI.
- Your own Telegram account, api_id, and api_hash.
- Internet access for dependencies and the Telegram connection.

The package looks for TDLib in the standard Homebrew locations for Apple Silicon
and Intel. Supporting these paths does not mean every Mac model and macOS version
has been tested. See the [verification report](VERIFICATION.md) for completed checks.

## Install from an archive

Obtain the ZIP and `.sha256` file from the same trusted source. Open Terminal in
the directory containing them and verify the checksum:

```sh
shasum -a 256 -c telegram-search-mcp-macos-v0.4.0.zip.sha256
```

Extract the ZIP. Inside `telegram-search-mcp-macos`, double-click the installer or
run one of these commands:

```sh
bash install-macos.command --clients codex
bash install-macos.command --clients gemini
bash install-macos.command --clients both
```

The installer creates a separate version directory under
`~/Applications/TelegramSearchMCP`, installs pinned Python dependencies, and
registers the selected clients. Each installation has its own source copy and
Python environment. The `current` link points to the most recently installed and
registered version; Telegram authorization is checked in the next step.
The installer does not check whether the clients are installed or connected.

Codex uses the registration name `telegram_search`; Gemini uses `telegram-search`.
Launchers clear the environment, and client configurations allow exactly four
tools. Client settings contain no Telegram keys. Before changing an existing file,
a private backup is created alongside it; unrelated entries are preserved.
Gemini JSON formatting/comments may change, with the original file kept in the backup.

## Prepare without signing in

```sh
bash install-macos.command --clients both --prepare-only
```

This installs and registers the MCP without starting interactive authorization.
The account owner can then run:

```sh
"$HOME/Applications/TelegramSearchMCP/current/tgsearch" auth
"$HOME/Applications/TelegramSearchMCP/current/tgsearch" doctor --connect
```

To install the program and dependencies without changing client settings:

```sh
bash install-macos.command --clients none --prepare-only
```

For isolated installer checks, use `--install-dir`, `--codex-config`, and
`--gemini-config`. Each path must be absolute. `--install-dir` specifies the root
containing all versions, rather than an individual version directory. If you choose
a custom root, replace `$HOME/Applications/TelegramSearchMCP` in this guide with
your path. Repeat custom `--codex-config` and `--gemini-config` paths when verifying
or removing those registrations; they do not become the defaults.
`--skip-system-deps` requires `--prepare-only` and an existing uv installation.
Using Telegram still requires TDLib.

## Add a second client later

Run the installer again with the desired `--clients` value, or use the installed version:

```sh
"$HOME/Applications/TelegramSearchMCP/current/client-config" register --clients both
```

Restart the client. The second client does not need a separate Telegram login.

## Update version 0.4 and later

1. Obtain the new ZIP and verify its checksum.
2. Finish active Telegram requests and close both clients.
3. Stop the shared service:

   ```sh
   "$HOME/Applications/TelegramSearchMCP/current/tgsearch" service stop
   ```

4. Run the new archive's installer with the desired `--clients` value.
5. Restart the clients and run `doctor --connect`.

The old program directory is preserved, so running processes do not have their
Python environment overwritten. The shared profile and Keychain entries are
preserved. If the local `doctor` check succeeds, the installer skips signing in
again and runs `doctor --connect`. If the profile is not configured or the local
check fails, authorization starts. Old client processes can restart the previous
service while they remain open; restart the clients to complete the switch.

To roll back, close the clients, stop the service, and run
`client-config register --clients both` from a retained previous installation.
Then restart the clients. Manual registration does not change the `current` link;
run commands directly from the version you want to use. Profiles are not
automatically converted between different database formats.

## Migrate from Codex 0.2 or Gemini 0.3

The new version stores data in **TelegramSearchMCPShared**. Previous
TelegramSearchMCP and TelegramSearchMCPGemini data directories and their Keychain
entries remain untouched. Signing in creates a separate Telegram device session.
Do not copy a database that is in use.

1. Finish Telegram requests in the old clients.
2. Explicitly allow the installer to replace recognized legacy registrations:

   ```sh
   bash install-macos.command --clients both --replace-legacy
   ```

3. Sign in to your own Telegram account and restart the clients.

`--replace-legacy` permits replacement only for recognized Codex 0.2 / Gemini 0.3
launchers with their original project file still present. Recognized legacy entries
under other names are also replaced with one standard entry. Replacement affects
registration, not old data or processes. If an entry is not recognized, the
installer explains the conflict and preserves it. Inspect the command and backup
before removing any unfamiliar MCP entry.

## Diagnostics

```sh
"$HOME/Applications/TelegramSearchMCP/current/tgsearch" --version
"$HOME/Applications/TelegramSearchMCP/current/tgsearch" doctor
"$HOME/Applications/TelegramSearchMCP/current/tgsearch" service status
"$HOME/Applications/TelegramSearchMCP/current/tgsearch" doctor --connect
"$HOME/Applications/TelegramSearchMCP/current/client-config" verify --clients codex
```

Use `gemini` in the last command for Gemini CLI, or `both` if both clients are
registered. Verification with `both` fails if only one client is registered.

`doctor` checks the profile, credentials, and library without printing secret
values. `doctor --connect` checks authorization through the shared service.
`service status` reports the service state without opening TDLib; `service start`
starts the service without signing in to Telegram. `service stop` shuts it down
gracefully; the next request starts it again.

In Codex CLI, run `codex mcp get telegram_search`. In Gemini CLI, use
`gemini mcp list` and `/mcp`. Gemini checks local MCP servers only in a trusted
working directory; configure trust for your directory in the client.

| Message or situation | Action |
| --- | --- |
| Setup incomplete / credentials missing | Run `auth` in your own Terminal |
| Shared service is already running during `auth` | Finish requests, run `service stop`, then retry `auth` |
| Profile is in use | Keep tdlib.lock; check for running authorization or an old manual copy; run `service status` |
| Queue full / deadline / timeout | Wait for other requests and retry a narrower query; check the internet connection |
| Unsupported TDLib runtime | Check Homebrew TDLib 1.8.0; use the stable package rather than HEAD |
| MCP is missing from the client | Check registration and the selected client, then restart it |
| Media does not display | Request a preview first; PDF/audio rendering depends on the client |
| Unsupported context anchor | Select a nearby text message; a voice message may have no text |

When asking for help, share the version, error message, and service status.
Do not share chat history, Keychain data, the TDLib database, QR codes, api_hash,
login codes, or passwords.

## Getting updates

Installing a downloaded ZIP does not require GitHub authorization. If the project
is private, the owner can share new ZIP and `.sha256` files with friends who lack
repository access. The archive does not grant access to private source or updates.

Official documentation: [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli),
[Gemini CLI MCP](https://geminicli.com/docs/tools/mcp-server/),
[Telegram API credentials](https://core.telegram.org/api/obtaining_api_id).
