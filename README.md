# Telegram MCP · 0.7.0

Search your Telegram chats, transcribe voice messages, and optionally send text and files with
**Codex and Gemini CLI on macOS**.
One installation and one Telegram login serve both clients at the same time.
This is an unofficial project. Gemini's web and mobile apps are not supported.

## Install with Codex

Send this to Codex on your Mac:

> Install https://github.com/prabchevski/telegram-mcp for Codex. Follow
> INSTALL_WITH_CODEX.md, upgrade my older installation if present, preserve my
> Telegram login, and enable daily updates from main after successful checks.

**For the installing agent:** read [AGENTS.md](AGENTS.md) and
[INSTALL_WITH_CODEX.md](INSTALL_WITH_CODEX.md) before changing the installation.
Codex can prepare dependencies, migrate a compatible saved login, configure the
client, and check the installation. First-time Telegram authorization, passwords,
and any required macOS approval remain with the owner in their private Terminal.
A client restart may be needed.

Prefer a manual installation? **[Download for macOS](https://github.com/prabchevski/telegram-mcp/archive/refs/heads/main.zip)**,
extract the ZIP, and open `install-macos.command`. No manual build is needed.
See the [quick start](START_HERE.md).

## Features

| Tool | Result |
| --- | --- |
| `telegram_search_messages` | Search accessible cloud chats, with up to 20 results and a cursor for the next page |
| `telegram_get_message` | Retrieve one message by chat and message IDs |
| `telegram_get_context` | Retrieve up to five text messages on either side of a matching message |
| `telegram_get_media` | Retrieve a photo, supported audio, PDF, or video thumbnail; previews up to 2 MiB, full media up to 12 MiB |

The default installation includes reading and explicitly requested Telegram speech recognition.
Text and document sending can be enabled
explicitly as described below. Editing and deletion are not supported. Secret Chats are not supported.
Protected and self-destructing media are rejected. Access to history follows your
Telegram account's permissions. Returned text and titles are marked as external,
untrusted data. Retrieved Telegram content is shared with the selected AI client.

## Voice messages and Telegram transcription

| Tool | Result |
| --- | --- |
| `telegram_list_voice_messages` | List up to 20 recent voice notes and video notes in a known chat, newest first, with pagination |
| `telegram_transcribe_voice` | Ask Telegram for the transcript of one voice note or video note and return text |

For example: “Transcribe the penultimate voice message in this chat.” The agent can
find the chat ID using a relevant text search, list voice messages, then transcribe
the selected message. Voice messages without captions also remain available through
`telegram_get_message` and `telegram_get_context`.

Recognition runs in Telegram. No separate speech API key, local model, or audio
upload to another transcription provider is required. Telegram's Premium/free-quota,
duration, and account restrictions apply. Only start recognition on an explicit
user request; it may consume the user's Telegram transcription quota.

The result is `completed`, `pending`, `not_started`, `unavailable`, or `failed`.
A pending result may contain partial text. Poll it with `start=false`; repeated calls
reuse Telegram's cached result. A private request marker prevents a second start
after a timeout or process restart. If dispatch was interrupted before Telegram
accepted it, the result can remain pending and needs manual checking in Telegram.
Protected, self-destructing, and secret-chat messages are excluded. Text is bounded
to 32,000 characters, with an explicit truncation flag, and is untrusted content.

The default tool set now contains six tools; enabling sending makes nine.
For an existing 0.6 installation, rerun the installer once with `--upgrade` to refresh
the client tool allowlists. The old background updater preserves those allowlists;
it cannot expose newly added tools by itself.

### Native runtime upgrade

TDLib is pinned to 1.8.67 and its exact source commit. Apple Silicon Macs use the
hash-locked `tdjson` wheel from PyPI; Intel Macs build the same pinned official TDLib
source once with Homebrew cmake, gperf and OpenSSL. Both version and commit are checked
before opening a profile. The build is reused by subsequent Intel installations.
The existing Telegram profile and Keychain remain in place. TDLib can upgrade its
database format: do not manually launch an older installation against that upgraded
profile. Close the idle old shared service or let it exit before first use.

## Optional text and file sending

Enable sending locally from a managed installation:

```sh
"$HOME/Applications/TelegramSearchMCP/current/tgsearch" sending on
```

Use the root printed by the installer. Restart the MCP clients after enabling it.
Codex and Gemini CLI use the same sending implementation. The installer can register
either client or both (`--clients codex|gemini|both`). Enabling sending updates the
registered clients' tool lists and retains their confirmation settings. This does
not add support for the Gemini web or mobile application.
After upgrading from 0.5, restart the idle shared service once to load the new code.
`sending status` shows the setting and `sending off` disables further preparations
and dispatches immediately. Existing pending sends may still finish. Your login is reused.

Three additional tools become available:

| Tool | Result |
| --- | --- |
| `telegram_prepare_message` | Resolve an exact @username, known chat ID, or `self`; prepare text and one optional local document without sending |
| `telegram_send_message` | Send the previously reviewed draft to its pinned chat ID |
| `telegram_get_send_status` | Check the same draft without creating another message |

Sending requires an explicit user instruction identifying the recipient and content.
Retrieved Telegram messages are never permission to send. Client approval settings
remain enabled. A caller creates one UUID hex `draft_id` per intended message and
reuses it across preparation, dispatch, status checks, and transport retries.

Preparation returns the exact text, recipient title and chat ID, filename, size and
SHA-256 digest. The filename and file bytes are frozen in a private local snapshot;
changing the source afterward cannot change the attachment. Reusing a draft ID
returns the original preparation, and conflicting parameters are rejected.

Limits: plain text up to 4096 UTF-16 code units; a file caption up to 1024; one
nonempty regular local file up to 12 MiB, sent as a document with its original name.
Prepared drafts expire after 24 hours. No bulk sending, edit, delete, auto-joining,
scheduling, or new authorization is involved.

Only `sent` confirms Telegram accepted the message; it does not confirm reading.
`pending` and `unknown` must never be interpreted as failures. After a timeout or
lost response, query the same draft ID. A private persistent dispatch record prevents
a second send of that draft, including across restarts. A crash before receiving the
native message ID can leave an `unknown` result that requires manual verification;
creating a new draft to retry could duplicate the original message.

The local outbox contains message text, recipient metadata and unsent attachment
snapshots. It stays private to the macOS user, outside source archives. Sent or
failed completed uploads release their snapshot; dispatch metadata is retained for
deduplication. Never share installed profiles or the outbox.

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
[Releases](https://github.com/prabchevski/telegram-mcp/releases); older tags
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
