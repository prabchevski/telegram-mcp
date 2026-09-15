# Telegram Search MCP · 0.5.0

Local search across your Telegram chats for **Codex and Gemini CLI on macOS**.
One installation and one Telegram login serve both clients at the same time.
This is an unofficial project. Gemini's web and mobile apps are not supported.

## Install with Codex

Send this to Codex on your Mac:

> Install https://github.com/prabchevski/telegram-search-mcp for Codex. Follow
> INSTALL_WITH_CODEX.md, upgrade my older installation if present, preserve my
> Telegram login, and enable daily updates from main after successful checks.

**For the installing agent:** read [AGENTS.md](AGENTS.md) and
[INSTALL_WITH_CODEX.md](INSTALL_WITH_CODEX.md) before changing the installation.
Codex can prepare dependencies, migrate a compatible saved login, configure the
client, and check the installation. First-time Telegram authorization, passwords,
and any required macOS approval remain with the owner in their private Terminal.
A client restart may be needed.

Prefer a manual installation? **[Download for macOS](https://github.com/prabchevski/telegram-search-mcp/archive/refs/heads/main.zip)**,
extract the ZIP, and open `install-macos.command`. No manual build is needed.
See the [quick start](START_HERE.md).

## Features

| Tool | Result |
| --- | --- |
| `telegram_search_messages` | Search accessible cloud chats, with up to 20 results and a cursor for the next page |
| `telegram_get_message` | Retrieve one message by chat and message IDs |
| `telegram_get_context` | Retrieve up to five text messages on either side of a matching message |
| `telegram_get_media` | Retrieve a photo, supported audio, PDF, or video thumbnail; previews up to 2 MiB, full media up to 12 MiB |

The server cannot send, edit, or delete messages. Secret Chats are not supported.
Protected and self-destructing media are rejected. Access to history follows your
Telegram account's permissions. Returned text and titles are marked as external,
untrusted data. Retrieved Telegram content is shared with the selected AI client.

## Saved logins and updates

Codex 0.2, Gemini 0.3, and shared 0.4 installations can be upgraded. Compatible
saved logins are reused locally, with no session database or secret copied. If the
two old clients use different accounts, the owner chooses one. A busy old profile
must be released by its client before migration. Old archives require one upgrade
through the installer/Codex to gain automatic updates.

New interactive installs enable **daily updates from main after successful GitHub
checks**. The Mac checks GitHub locally; a commit does not remotely deploy onto
other computers. Updates keep immutable program versions and preserve the login.
New MCP processes use the new code; the shared service switches on its next start,
after active work ends and the service becomes idle. An offline or sleeping Mac
may receive an update later. Users can turn updates off:

```sh
"$HOME/Applications/TelegramSearchMCP/current/tgsearch" updates off
```

Use the root printed by the installer if it differs. More controls and migration
steps are in [INSTALL_MACOS.md](INSTALL_MACOS.md).

## Shared session

MCP processes forward requests to one local background service. The service owns
the TDLib session and processes a shared queue one request at a time. Ending a
Codex or Gemini task does not interrupt other clients.

Each person uses their own Telegram account on their own Mac. Share the repository
link or a clean source/release archive. Do not share installed copies with their
data, Keychain entries, policy.json, TDLib database, or session.

## Documentation

- [Quick start](START_HERE.md)
- [Install with Codex](INSTALL_WITH_CODEX.md)
- [Installation, updates, and troubleshooting](INSTALL_MACOS.md)
- [Uninstallation and Telegram session revocation](UNINSTALL_MACOS.md)
- [Architecture and limitations](ARCHITECTURE.md)
- [Changelog](CHANGELOG.md)
- [Verification report](VERIFICATION.md)

Source downloads are public and need no GitHub account. Python, TDLib, and other
dependencies download separately. Tagged source archives are also available under
[Releases](https://github.com/prabchevski/telegram-search-mcp/releases); older tags
retain their original features and instructions.

## Development

```sh
uv sync --frozen --group dev
uv run --frozen pytest
uv run --frozen python -I scripts/release.py audit
uv run --frozen python -I scripts/release.py build --output dist
```

Tests use isolated profiles and settings and do not require a Telegram account.
CI tests Linux/macOS and builds and installs an allowlisted source archive on a
GitHub-hosted Mac. This is a test environment, not the maintainer's or users' Macs.
Archive building supports these checks and optional versioned releases; users can
install directly from the repository.

## License

Code and documentation are available under the [MIT License](LICENSE).
Third-party dependencies retain their own licenses.

## Official documentation

- [Codex: MCP integration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
- [Gemini CLI: MCP servers](https://geminicli.com/docs/tools/mcp-server/)
- [Telegram: API credentials](https://core.telegram.org/api/obtaining_api_id)
- [Homebrew: TDLib](https://formulae.brew.sh/formula/tdlib)

Integration settings were checked on September 15, 2026.
