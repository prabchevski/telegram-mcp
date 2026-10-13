# Telegram MCP · 0.11.0

Connect **your own Telegram account** to **Codex or Gemini CLI on macOS**, **Codex on Windows x64**, or **ChatGPT Work with local plugins on Windows x64**. Search and read messages, download attachments, transcribe voice notes, and optionally prepare and send messages. One local service supports multiple chats and clients with a single Telegram login.

## Give this link to your AI agent

Paste this into your local AI client on the computer where you want to install it:

> Install https://github.com/prabchevski/telegram-mcp for the client I am using now. Read AGENTS.md and INSTALL.md, detect my operating system, download the matching archive from the latest release, and verify its SHA-256. If it is already installed, upgrade it while preserving my Telegram login and other settings. Complete the available checks; I will handle the first login in a local window without sharing secrets in chat.

The agent selects the instructions for your operating system and client. Your installation request authorizes normal setup, dependencies, and configuration of the selected client. The first connection requires your `api_id` and `api_hash` from [my.telegram.org](https://my.telegram.org): enter them, your login code, and any 2FA password **only in the local authorization window**. A compatible existing login is preserved. You may need to restart the client or enable the plugin yourself.

**Installing agents: start with [AGENTS.md](AGENTS.md) → [INSTALL.md](INSTALL.md).** Do not set up a personal installation merely to review or develop this project.

## Choose your platform

| Operating system and client | Connection | Instructions |
| --- | --- | --- |
| macOS Apple Silicon / Intel · Codex app or CLI | Local MCP through Codex configuration | [Install on macOS](INSTALL_MACOS.md) |
| macOS Apple Silicon / Intel · Gemini CLI | Local MCP through Gemini CLI configuration; Codex is not required | [Install on macOS](INSTALL_MACOS.md) |
| Windows x64 · Codex desktop / CLI with plugin support | Personal plugin marketplace → Telegram MCP | [Install on Windows](INSTALL_WINDOWS.md) |
| Windows x64 · ChatGPT Work with local plugins | Personal plugin marketplace → Telegram MCP | [Install on Windows](INSTALL_WINDOWS.md) |

Gemini in a browser or on a phone and ordinary ChatGPT web chats cannot run this local MCP. There is no native Windows ARM64 package. Windows requires no WSL, public server, or tunnel. You need an installed client that supports the listed connection method.

Manual installation: **[macOS ZIP](https://github.com/prabchevski/telegram-mcp/releases/latest/download/telegram-mcp-macos.zip)** ([SHA-256](https://github.com/prabchevski/telegram-mcp/releases/latest/download/telegram-mcp-macos.zip.sha256)) · **[Windows ZIP](https://github.com/prabchevski/telegram-mcp/releases/latest/download/telegram-mcp-windows.zip)** ([SHA-256](https://github.com/prabchevski/telegram-mcp/releases/latest/download/telegram-mcp-windows.zip.sha256)). See the [quick start](START_HERE.md). These links point to the latest published release; its **Assets** include ready-to-use installers. GitHub's standard **Source code** downloads do not replace the Windows package.

Installation enables daily verified updates on macOS and Windows by default, while preserving an explicit opt-out. Codex and Gemini CLI share the macOS installation; Codex and ChatGPT Work share the Windows plugin installation. Each user signs into their own account; the repository and releases contain no personal sessions or keys. Retrieved messages are shared with the selected AI client. This is an unofficial project.

## Usage examples

- “Find messages in my Telegram about Friday's meeting.”
- “Show my unread chats” or “What did we discuss yesterday in this group?”
- “Save the spreadsheet I received” or “Transcribe this voice message.”

The agent searches your accessible account history and subscriptions first. Before a separate public-post search, it checks the actual remaining free quota, explains any attempt it would consume, and **waits for your permission**. Stars payments are disabled. The default installation exposes 15 tools; sending is off and adds seven tools only when enabled, including edits to your own sent messages. See [public search](#free-public-channel-post-search) and [sending](#optional-text-and-file-sending).

Automated checks cover the core on macOS, Linux, and Windows Server 2025, as well as installers and MCP connections. Separate installation checks passed on x64 Windows 10 Enterprise Evaluation 22H2 (build 19045.2006) and Windows 11 Enterprise Evaluation 25H2 (build 26200.6584) virtual machines, using a standard user account. These checks verified installation, reinstallation, and MCP discovery; real Telegram login and the selected client's interface remain unverified. Intel macOS builds the pinned TDLib version; automated Mac installation checks run on Apple Silicon. See [VERIFICATION.md](VERIFICATION.md) for the exact coverage and limitations.

## Features

| Tool | Result |
| --- | --- |
| `telegram_search_messages` | Search the linked account's accessible cloud-chat history, up to 20 results per page |
| `telegram_get_public_search_quota` | Check this account's free public-search quota for a query without running a search |
| `telegram_search_public_posts` | Search public channel posts after a quota check and explicit user confirmation; free requests only |
| `telegram_get_message` | Retrieve one message by chat and message IDs |
| `telegram_get_context` | Retrieve up to five supported text or voice/video-note messages on either side of an anchor |
| `telegram_get_media` | Retrieve a photo, supported audio, PDF, or video thumbnail; previews up to 2 MiB, full media up to 12 MiB |

The default installation includes reading, explicitly requested local downloads, and Telegram speech recognition.
Text and document sending can be enabled
explicitly as described below. Own sent-message editing uses a reviewed snapshot;
deleting messages and Secret Chats are not supported.
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

## Free public channel post search

Public channel search extends beyond subscriptions and can use a limited free
attempt. Start with the account's accessible history/subscriptions using
`telegram_search_messages`, or one known chat using
`telegram_search_chat_messages`. Show those results, then offer the broader public
search if it would help. A general research request or poor results do not authorize
this expansion automatically.

Before each new public query, the agent must:

1. Call `telegram_get_public_search_quota(query)`. This only checks Telegram's
   current account limits; it does not run a search or consume a search attempt.
2. Tell the user the query and broader scope, the remaining free attempts and any
   wait, and whether this query would use an attempt or is already free/cached.
   Ask for explicit permission and **wait for the answer**. Never assume a fixed
   daily allowance; use the live account response.
3. After permission, pass the quota response's `confirmation_token` and
   `user_confirmed=true` to `telegram_search_public_posts(query, limit=20, ...)`.
   If the token expires or the quota snapshot changes, check and ask again.

For example, after searching subscriptions: “I found these results in your chats.
I can also search public posts for ‘artificial intelligence’, including channels
you do not follow. Telegram reports N free attempts remaining; this query would
use one. Shall I search?” Replace the quota statement with the actual result,
including when the query is already free or a wait is required. Do not offer a
paid alternative.

The confirmation token is single-use, expires after five minutes and is bound to
the account, query and quota snapshot. Missing confirmation never starts a public
search. The server checks the supplied token and confirmation flag; the agent is
responsible for truthfully reporting the user's conversational approval. The
server cannot independently prove that the human answered. MCP initialization
instructions and tool descriptions carry the workflow to every client, including
clients that do not read this repository's `AGENTS.md`.

Each call makes at most one `searchPublicPosts` request, always with `star_count=0`.
There is no payment argument, Stars purchase, paid retry or hidden extra page
request. Never reformulate or launch a new public query without fresh permission.
The search tool is non-read-only and non-idempotent because it may consume a free
attempt; the quota-check tool is read-only. Sending need not be enabled.

Quota fields include `remaining_free_query_count`, `next_free_query_in`,
`is_current_query_free` and `star_count` (informational price only). A limit race,
missing confirmation, unsupported TDLib or failed request has an explicit outcome,
separate from a successful zero-match result. In particular, `confirmation_required`
means approval is absent or invalid, and `quota_changed` requires a new quota check
and confirmation. Telegram's account/access rules
still apply. Public search covers Telegram's public channel index, not every
Telegram message.

Results contain bounded untrusted text/channel metadata and a public link only
when confirmed by Telegram. No joining, read-state changes, attachment downloads
or sending occur. Use `next_cursor` exactly with the same approved query; these
free continuation pages need no new confirmation. Short or empty pages can still
have a continuation. Cursors are bound to the account and search kind, expire
after ten minutes or a service restart, and can be successfully consumed once.
Do not interpret a limit, partial result or failed request as proof that a post
does not exist.

The implementation is shared across platforms; CI covers macOS, Windows Server
2025 and Linux core behavior. Packaged desktop installation is provided for macOS and Windows.

Official contracts: [searchPublicPosts](https://core.telegram.org/tdlib/docs/classtd_1_1td__api_1_1search_public_posts.html),
[publicPostSearchLimits](https://core.telegram.org/tdlib/docs/classtd_1_1td__api_1_1public_post_search_limits.html).

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

The default tool set contains 15 tools; enabling sending makes 22.
Unchanged standard 0.7/0.8/0.9.0 registrations migrate to the new tools while preserving sending preferences.
Existing managed 0.6.1 installations with daily updates enabled transition automatically:
the old updater installs the new package, then the next scheduled run (or an earlier
MCP start) adds the current tools to unchanged standard Codex/Gemini registrations.
Allow up to two daily checks on Apple Silicon. No reinstall or Telegram login is
needed. A macOS notification requests a Codex/Gemini restart; notification visibility
depends on macOS settings. `tgsearch updates status` also retains the restart notice.
If migration happens while a client is starting, restart that client once more so it
rereads its settings. Sending stays on/off as previously configured. Removed,
disabled or custom connections are never restored. Known complete tool lists can
be refreshed while retaining approval/trust and parallel-call preferences;
unknown restrictions or launcher changes require review. Installations without managed daily updates need
the installer once with `--upgrade`; the existing update preference is preserved.
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

Seven additional tools become available:

| Tool | Result |
| --- | --- |
| `telegram_prepare_message` | Resolve an exact @username, known chat ID, or `self`; prepare text and one optional local document without sending |
| `telegram_send_message` | Send the previously reviewed draft to its pinned chat ID |
| `telegram_get_send_status` | Check the same draft without creating another message |
| `telegram_set_chat_draft` | Save or explicitly clear a native Telegram text draft for review in the Telegram app |
| `telegram_prepare_message_edit` | Preview the original message and replacement, pinned to the account and exact native IDs |
| `telegram_edit_message` | Apply the prepared edit once if the original message still matches |
| `telegram_get_edit_status` | Inspect or reconcile that same edit without dispatching another edit |

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
Prepared local outgoing drafts expire after 24 hours. No bulk sending,
deleting delivered messages, auto-joining, or new authorization is involved.

### Editing your sent messages

On the owner's explicit editing instruction, prepare an edit using a fresh UUID hex
`edit_id`, the exact native `chat_id` and `message_id` from Telegram tools, and the
replacement `text`. Review the returned `original_text` and `text`, then apply the
same `edit_id`. Preparation only reads Telegram and stores a private local snapshot;
it does not change the message. Prepared edits expire after five minutes.

Text messages and supported media captions are editable. Replacement is plain text;
Markdown/HTML is not parsed and existing formatting entities are removed.
Native URL/mention detection remains active. Use replacement text without boundary
ASCII spaces/newlines or control characters; interior line breaks are supported.
Media, caption positioning and link-preview options are preserved. Telegram's edit permission
must be current and the sender must be the linked user. Messages sent as a channel or
anonymous administrator are excluded because personal authorship cannot be verified.
Pending, scheduled, self-destructing, secret-chat and unsupported messages are excluded.

The original content and edit timestamp are checked again before dispatch. A changed
message returns `conflict` without applying the edit. Telegram has no atomic conditional
edit, so a change on another device can still race the last check. Each `edit_id` is
dispatched at most once, including after a timeout or restart. `edited` confirms the
observed replacement; `unchanged` means it matched at preparation. On `unknown` or a
lost response, use `telegram_get_edit_status` with the same ID. Never create a new edit
to retry an uncertain operation or overwrite a later owner change.

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
snapshots. It stays private to the operating-system user, outside source archives. Sent or
failed completed uploads release their snapshot; dispatch metadata is retained for
deduplication. Never share installed profiles or the outbox.

## Saved logins and updates

Codex 0.2, Gemini 0.3, and shared 0.4 installations can be upgraded. Compatible
saved logins are reused locally, with no session database or secret copied. If the
two old clients use different accounts, the owner chooses one. A busy old profile
must be released by its client before migration. Old archives require one upgrade
through the installer/Codex to gain automatic updates.

New installations enable daily updates after successful GitHub checks. macOS uses
the checked main SHA; Windows uses the verified published Windows asset for that
exact SHA. Windows schedules a task for the current user without administrator
rights or a saved password. Windows installations older than 0.11 need the new
installer once to add this mechanism; later updates run automatically.
Updates keep immutable program versions and preserve the login, sending opt-in,
client approval preferences and explicit update opt-out.
New MCP processes use the new code; the shared service switches on its next start,
after active work ends and the service becomes idle. An offline or sleeping Mac
may receive an update later. Restart the selected client to load new tool discovery.
Users can inspect or turn updates off:

```sh
"$HOME/Applications/TelegramSearchMCP/current/tgsearch" updates off
```

On Windows, use the stable `tgsearch.ps1` entry point:

```powershell
& $tg updates status
& $tg update --check
& $tg updates off
& $tg updates on
```

Use the root printed by the installer if it differs. More controls and migration
steps are in [INSTALL_MACOS.md](INSTALL_MACOS.md).

## Shared session

MCP processes forward requests to one local background service. The service owns
the TDLib session and processes a shared queue one request at a time. Ending a
Codex or Gemini task does not interrupt other clients.

Each person uses their own Telegram account on their own computer. Share the repository
link or a clean source/release archive. Do not share installed copies with their
data, Keychain/Credential Manager entries, policy.json, TDLib database, or session.

## Documentation

- [Quick start](START_HERE.md)
- [Codex and ChatGPT Work on Windows](INSTALL_WINDOWS.md)
- [Install with an AI agent: platform and client routing](INSTALL.md)
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
CI tests Linux/macOS and native Windows x64 on Windows Server 2025, and builds and
installs an allowlisted source archive on a GitHub-hosted Mac. This is a test environment, not the maintainer's or users' Macs.
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

Release checks and platform-specific acceptance limits are recorded in [VERIFICATION.md](VERIFICATION.md).
