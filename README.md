# Telegram Search MCP · 0.4.0

Local search across your Telegram chats for **Codex and Gemini CLI on macOS**.
One installation and one Telegram login serve both clients at the same time.
This is an unofficial project. Gemini's web and mobile apps are not supported.

**Start with the [quick start guide](START_HERE.md).**

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
untrusted data.

## Shared session

MCP processes forward requests to one local background service. The service owns
the TDLib session and processes a shared queue one request at a time. Ending a
Codex or Gemini task does not interrupt other clients.

Version 0.4 uses a separate `TelegramSearchMCPShared` data directory and macOS
Keychain service. Earlier 0.2/0.3 installations are not switched automatically:
0.4 requires a new login to **your own** Telegram account. Existing tasks can
continue using their previous installation.

## Installation and maintenance

- [Quick start](START_HERE.md)
- [Installation, updates, and troubleshooting](INSTALL_MACOS.md)
- [Uninstallation and Telegram session revocation](UNINSTALL_MACOS.md)
- [Architecture, local connection security, and limitations](ARCHITECTURE.md)
- [Changelog](CHANGELOG.md)
- [Verification report](VERIFICATION.md)

The archive contains source code and an installer. Python, TDLib, and other
dependencies are downloaded separately, so the first installation needs internet
access. Installing a ZIP you have already received does not require GitHub access.
If the repository is private, downloading releases from GitHub requires access
granted by its owner; the owner can also share the release ZIP directly.

Each person uses their own Telegram API credentials and signs in on their own Mac.
Share only the release ZIP and its checksum. Do not share an installed copy together
with its data, Keychain entries, `policy.json`, TDLib database, or session.

## Development

```sh
uv sync --frozen --group dev
uv run --frozen pytest
bash scripts/build-macos-archive.sh
```

Tests do not require a Telegram account. End-to-end client verification requires
an installed Codex/Gemini CLI client and the account owner's local authorization.
CI runs checks and builds versioned archives from an explicit file allowlist.

## License

This project's code and documentation are available under the [MIT License](LICENSE).
Third-party dependencies retain their own licenses.

## Official documentation

- [Codex: MCP integration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
- [Gemini CLI: MCP servers](https://geminicli.com/docs/tools/mcp-server/)
- [Telegram: obtaining api_id and api_hash](https://core.telegram.org/api/obtaining_api_id)
- [Homebrew: TDLib](https://formulae.brew.sh/formula/tdlib)

Integration settings were checked on September 15, 2026.
