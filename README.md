# Telegram MCP · 0.8.0

Browse and search Telegram chats, download files, transcribe voice messages, and optionally
save drafts, reply, or schedule text and files with
**Codex and Gemini CLI on macOS**.
One installation and one Telegram login serve both clients at the same time.
This is an unofficial project. Gemini's web and mobile apps are not supported.

## Install with Codex or Gemini CLI

Send this to Codex or Gemini CLI on your Mac:

> Install https://github.com/prabchevski/telegram-mcp for the client I am using. Follow
> INSTALL.md, upgrade my older installation if present, preserve my
> Telegram login, and enable daily updates from main after successful checks.

**For the installing agent:** read [AGENTS.md](AGENTS.md) and
[INSTALL.md](INSTALL.md) before changing the installation.
Either assistant can prepare dependencies, migrate a compatible saved login, configure the
client, and check the installation. First-time Telegram authorization, passwords,
and any required macOS approval remain with the owner in their private Terminal.
A client restart may be needed.

Prefer a manual installation? **[Download for macOS](https://github.com/prabchevski/telegram-mcp/releases/latest/download/telegram-mcp-macos.zip)**,
extract the ZIP, and open `install-macos.command`. No manual build is needed.
See the [quick start](START_HERE.md).

## Features

| Tool | Result |
| --- | --- |
| `telegram_search_messages` | Search accessible cloud chats, with up to 20 results and a cursor for the next page |
| `telegram_get_message` | Retrieve one message by chat and message IDs |
| `telegram_get_context` | Retrieve up to five supported text or voice/video-note messages on either side of an anchor |
| `telegram_get_media` | Retrieve a photo, supported audio, PDF, or video thumbnail; previews up to 2 MiB, full media up to 12 MiB |

The default installation includes reading, explicitly requested local downloads, and Telegram speech recognition.
Text and document sending can be enabled
explicitly as described below. Editing and deletion are not supported. Secret Chats are not supported.
Protected and self-destructing media are rejected. Access to history follows your
Telegram account's permissions. Returned text and titles are marked as external,
untrusted data. Retrieved Telegram content is shared with the selected AI client.

## Chats, unread messages, history, and files

| Tool | Result |
| --- | --- |
| `telegram_list_chats` | List main/archive chats, find known chats by name or resolve an exact @username; optional unread filter |
| `telegram_get_chat_history` | Read one chat newest first, optionally by date range or unread status |
| `telegram_search_chat_messages` | Search one chat by text, sender, attachment type, forum topic, dates, or unread status |
| `telegram_download_file` | Save a document, photo, audio, or full video locally; return path, size and SHA-256 |
| `telegram_get_message_thread` | Read a message's reply thread, including accessible channel comments |
| `telegram_get_chat_draft` | Read the native Telegram draft and its version |
| `telegram_get_scheduled_messages` | List messages already scheduled in Telegram, including their scheduled time |

Examples: “Who has written to me?”, “What did we discuss yesterday in this group?”,
“Find the spreadsheets from this sender”, “Save this attachment so I can analyze it”.
None of the navigation tools marks messages read. Manually marked-unread chats are
included in chat listing, but unread history uses Telegram's last-read message ID.

Each page contains at most 20 items. Follow `next_cursor`, including after an empty
filtered page; results are not a complete history until pagination ends. A chat
listing snapshots at most 500 identifiers for 10 minutes; `coverage_limited` reports
the cap. Name search covers chats already known to TDLib across lists; an exact
@username can resolve a public chat without joining. Main/archive selects the list
only when the query is empty. History includes uncaptioned media and service messages;
text is limited to 4,000 characters per item with explicit truncation metadata.
Date ranges use timezone-qualified ISO 8601, with `date_from` inclusive and `date_to`
exclusive. Keep filters unchanged when continuing a page. `voice` includes video
notes, `mention` selects unread mentions, and `topic_id` means a forum topic ID.

Downloads default to 20 MiB and allow an explicit limit up to 100 MiB. The tool
streams a copy into the account's private `downloads/` directory under the profile,
with a unique destination, no overwrites, and owner-only permissions. Returned paths
can be opened by the local AI client. Files remain until the owner removes them;
no attachment is automatically opened or executed. Protected and self-destructing
media are rejected. The original inline preview/full-media tools retain their
2 MiB/12 MiB limits. Downloads are explicit local writes, so their MCP annotation
is not read-only.

## Voice messages and Telegram transcription

| Tool | Result |
| --- | --- |
| `telegram_list_voice_messages` | List up to 20 recent voice notes and video notes in a known chat, newest first, with pagination |
| `telegram_transcribe_voice` | Ask Telegram for the transcript of one voice note or video note and return text |

For example: “Transcribe the penultimate voice message in this chat.” The agent can
find the chat ID using `telegram_list_chats`, list voice messages, then transcribe
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

The default tool set contains 13 tools; enabling sending makes 17.
Unchanged standard 0.7 registrations migrate to the new tools while preserving sending preferences.
Existing managed 0.6.1 installations with daily updates enabled transition automatically:
the old updater installs the new package, then the next scheduled run (or an earlier
MCP start) adds the current tools to unchanged standard Codex/Gemini registrations.
Allow up to two daily checks on Apple Silicon. No reinstall or Telegram login is
needed. A macOS notification requests a Codex/Gemini restart; notification visibility
depends on macOS settings. `tgsearch updates status` also retains the restart notice.
If migration happens while a client is starting, restart that client once more so it
rereads its settings. Sending stays on/off as previously configured. Removed or
manually customized connections are never restored or overwritten; those require
an explicit configuration review. Installations without managed daily updates need
the installer once with `--upgrade --auto-update on`.
The pre-rename 0.6.0 archive also needs that one-time installer: its updater requires
the old GitHub repository identity and rejects CI from the renamed repository.
A change published only in this repository cannot reach that updater.
If 0.6.1 already installed 0.7.0 while retaining its old tool lists, 0.7.0's updater
stops with `registration_changed` before downloading another package. That stranded
installation needs the installer once as well. A normally configured 0.7.0 installation
continues updating automatically.

### Native runtime upgrade

TDLib is pinned to 1.8.67 and its exact source commit. Apple Silicon Macs use the
hash-locked `tdjson` wheel from PyPI; Intel Macs build the same pinned official TDLib
source once with Homebrew cmake, gperf and OpenSSL. Both version and commit are checked
before opening a profile. The build is reused by subsequent Intel installations.
When updating from 0.6.1 on Intel, the first attempt starts a separate pinned TDLib
build and leaves the old installation active. A later daily check retries after
the cache is ready, then registration migration completes as above. Homebrew and
Apple's command-line tools must already work. A failed build requests a macOS
notification directing the owner to the installer; diagnostics remain under the
installation's `native/prepare.log`. This Intel path is covered by simulated tests;
the full historical-updater smoke test runs on Apple Silicon.
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

Four additional tools become available:

| Tool | Result |
| --- | --- |
| `telegram_prepare_message` | Resolve an exact @username, known chat ID, or `self`; prepare text and one optional local document without sending |
| `telegram_send_message` | Send the previously reviewed draft to its pinned chat ID |
| `telegram_get_send_status` | Check the same draft without creating another message |
| `telegram_set_chat_draft` | Save or explicitly clear a native Telegram text draft for review in the Telegram app |

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
Prepared local outgoing drafts expire after 24 hours. No bulk sending, editing or
deleting delivered messages, auto-joining, or new authorization is involved.

### Replies and scheduled sending

`telegram_prepare_message` accepts `reply_to_message_id`, `topic_id`, and
`schedule_at`. Reply targets are checked against the pinned recipient and selected
forum topic. `schedule_at` must contain a timezone, e.g. `2026-10-01T10:00:00+01:00`,
and be 60 seconds to 366 days in the future. Review the returned reply ID and Unix
`scheduled_at` along with the text before dispatch. Telegram executes an accepted
schedule even when this MCP is closed. Expired schedules are rejected; they never
silently become immediate messages. Both text and the existing document attachment
are supported. Recurring schedules, rescheduling and cancellation are not exposed;
manage those in Telegram.

`scheduled` means Telegram accepted the scheduled message, not that it was delivered.
The outbox retains that acceptance record; it does not track subsequent delivery,
manual rescheduling or cancellation. Use `telegram_get_scheduled_messages` to inspect
Telegram's current queue. A missing scheduled message alone does not prove delivery.

### Native Telegram drafts

“Prepare a reply that I can review on my phone” uses `telegram_get_chat_draft` followed
by `telegram_set_chat_draft`. This changes the text draft visible in Telegram without
sending it. Pass the read result's `version` as `expected_version`; an observed change
is rejected. Telegram has no atomic compare-and-set API, so simultaneous editing on
another device can still race with the operation. Existing non-text drafts are
identified by `content_type`; replacing one must be an explicit user choice.
An empty text explicitly clears the draft. A forum topic and reply target are optional.

Generate one UUID hex `operation_id` and reuse it on retries. Its account-bound record
is saved before dispatch, so a timeout/restart never blindly reapplies a draft over
later user edits. `stored` is the recorded result of that operation; `unknown` requires
a fresh `telegram_get_chat_draft` inspection, not another operation ID. Native draft
writes use the existing opt-in sending setting. Native draft operation records remain
private under the profile's `draft-operations/` directory.

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
- [Install with AI: Codex or Gemini CLI](INSTALL.md)
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
After these checks pass on main, CI publishes each new package version to
[GitHub Releases](https://github.com/prabchevski/telegram-mcp/releases/latest), with
the verified archive, checksum, file inventory and wheel. Existing published tags
and assets stay unchanged. The download button follows the latest published release.
Developers can also [download main source](https://github.com/prabchevski/telegram-mcp/archive/refs/heads/main.zip).

## License

Code and documentation are available under the [MIT License](LICENSE).
Third-party dependencies retain their own licenses.

## Official documentation

- [Codex: MCP integration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
- [Gemini CLI: MCP servers](https://geminicli.com/docs/tools/mcp-server/)
- [Telegram: API credentials](https://core.telegram.org/api/obtaining_api_id)
- [TDLib: pinned source](https://github.com/tdlib/td/tree/d1085f9cebc5a62379991ae1652673954f229c1f)

Integration settings and native voice transcription were checked on September 16, 2026.
