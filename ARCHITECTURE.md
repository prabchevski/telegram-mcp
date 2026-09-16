# Architecture and access boundaries

## Shared session

```text
Codex, task 1 ── MCP stdio ─┐
Codex, task 2 ── MCP stdio ─┼── local Unix socket ── queue ── TDLib ── Telegram
Gemini CLI    ── MCP stdio ─┘                    one profile owner
```

The MCP server is a small proxy. It starts a shared background service on the
first request. Tool discovery is available before Telegram authorization.
The service handles one profile belonging to the current macOS user; other users
and profiles are isolated. A separate lock prevents simultaneous service startups.
The `tdlib.lock` file continues to protect the TDLib database.

The service accepts a fixed set of requests: the original four read operations,
voice listing, explicitly requested speech recognition, local diagnostics/shutdown,
bounded navigation/download operations, and four optional outgoing/draft operations. Sending is disabled
unless enabled locally. It does not accept arbitrary TDLib methods, Python function
names, or commands to execute through the connection. Only outgoing preparation
can accept a bounded local attachment path; downloads export into a fixed private profile directory.

## Local connection

A Unix socket lives in a short, private directory belonging to the current user.
No network port is opened. Directories use permissions 0700, and connection files
are accessible to their owner. Ownership, symlinks, and the connecting process's
credentials are checked. The protocol limits message size, parameters, and queue
length. Requests and Telegram content are not logged.

The trust boundary is the **macOS user account**. Other programs running as you
have the same local permissions. This does not isolate your data from malicious
software already running under your account. The AI client receives the Telegram
results returned by tools and handles them under its own policies.

Client launchers clear the inherited environment and use an absolute Python path
with `-I` isolation. TDLib 1.8.67 is loaded from the locked wheel on Apple Silicon
or a pinned source build on Intel; its version and source commit are verified.
Project environment variables cannot select another database through
`TGSEARCH_DATA_DIR` or another library through `TDJSON_LIBRARY`.
Secrets are not placed in MCP configuration.

## Timeouts, queue, and shutdown

By default, the queue holds up to 16 waiting requests plus one active operation.
Each request has a 120-second deadline including time spent in the queue. Client
configurations allow 150 seconds to leave room for startup. The service exits after
10 minutes without operations. The local connection is limited to 40 connections
and 18 MiB per response message; media is encoded as base64.

A client cancelling its wait does not mean the native TDLib operation has stopped.
The service waits for the active operation to finish before starting another.
Expired queued requests must not delay new reads; a full queue returns a clear
error. Clients do not retry indefinitely after a connection failure.

`tgsearch service stop` stops accepting new operations, finishes active work, and
closes the session without logging out of Telegram. The next request starts the
service again. Normal exits and termination signals release the database cleanly;
after a crash, the operating system releases the locks. Neither the installer nor
diagnostic commands delete `tdlib.lock` or terminate unrelated processes.
The service releases resources automatically when idle.

Interactive `auth` uses the same service ownership lock, preventing credential
changes while a session is active. If the service is running, finish client
requests and run `service stop` first.

## Data and compatibility

| Area | Current behavior |
| --- | --- |
| Data | `~/Library/Application Support/TelegramSearchMCPShared/profiles/default` |
| Keychain service | `local.unofficial-telegram-search-mcp-shared` |
| Credentials | `default:api_hash`, `default:database_key`; local Keychain only |
| Previous Codex/Gemini data | Explicit installer upgrade can select one compatible profile in place |
| Authorization | Your own api_id/api_hash, QR, and code/2FA in Terminal if required |
| Media | Text and structured metadata for both clients |
| Full image | Additional `_meta` containing the Codex `original` hint |
| Transfer | Preview ≤2 MiB, full ≤12 MiB; format rendering varies by client |
| TDLib | 1.8.67, commit `d1085f9cebc5a62379991ae1652673954f229c1f`; other builds are rejected |

A profile is bound to one Telegram account; an account mismatch is rejected.
A private `profile-source.json` in the shared root can select the fixed `codex` or
`gemini` legacy namespace. Paths cannot be supplied in this binding. The selected
profile and Keychain service are reused in place; no database or secret is copied.
The old native lock must be free during adoption. An existing shared profile takes
priority, and different old accounts require an explicit choice. New authorization
creates a separate session only when no reusable saved session is selected.

## Program updates

The managed installation has immutable version directories and stable launchers.
Each launcher resolves `current` to an immutable path before executing Python, so
switching the symlink cannot change a running process's import path. An existing
proxy also resolves the newest installed interpreter when starting a shared service.
New clients gracefully replace an idle older service; a busy service finishes its
work before an upgrade is retried. No process is killed to release a profile.

A per-user macOS LaunchAgent checks once a day. The updater accepts only the exact
canonical main SHA with a successful push workflow, downloads a pinned source ZIP,
and validates paths, file types, size, and source allowlists. It stages locked
dependencies and checks imports before activation. Installation locks prevent
concurrent activation; client settings and the receipt are checked for changes
during download. Activation migrates only exact known standard 0.6 and 0.7 tool lists to
the new schema, with locked comparisons and private backups. Removed/customized
registrations and sending preferences are preserved. A restart notice is saved
locally and requested through macOS notifications. Disabling
updates removes the schedule; disconnecting the last registered client also disables it.

An installation receipt records version, revision, client config paths, and update
preference. It contains no Telegram credentials. Code updates trust the maintainer's
canonical repository and the locked dependency sources; passing CI is a functional
check, not proof against every malicious or faulty future change.

## Limitations

Full-text search depends on the history available in Telegram. Text and voice/video
notes work as context anchors; other messages without supported text may not.
Videos are returned as thumbnails;
the application does not perform bulk history exports. TDLib caches media locally
in the profile. Sending and transcription are explicit operations; transcription
may consume Telegram's free quota. Authorization, the local database, cache, and
installation settings also change as needed.

## Telegram-native transcription

The same serialized session calls `recognizeSpeech` and reads
`speech_recognition_result`. Results distinguish final, pending, not-started,
unavailable and failed states. Private account-bound markers prevent a second
start after an ambiguous timeout or restart. Text is bounded to 32,000 characters
with an explicit truncation flag. No separate speech provider or local model is
used. Telegram Premium/free-quota and duration limits apply.


## Optional outgoing operations (0.6)

Sending is disabled by default. A private `sending.json` in the managed installation
root controls discovery and native dispatch. `tgsearch sending on|off` refreshes only
unchanged registered clients, preserving unrelated settings, removed registrations
and approval prompts. Existing proxies cannot bypass a later local disable switch.

The fixed local protocol adds prepare_message, send_message and get_send_status.
No generic TDLib request transport is exposed. They use the same serialized worker
and account-bound session as reads. Requests are bounded to 32 KiB; files are local
snapshots, never JSON payloads. Attachments use TDLib 1.8.67 inputMessageDocument, plain
text uses inputMessageText, and updates distinguish SendSucceeded from SendFailed.
Schema reference: https://github.com/tdlib/td/blob/d1085f9cebc5a62379991ae1652673954f229c1f/td/generate/scheme/td_api.tl

Each UUID draft has a private durable state file under the profile outbox. Preparation
pins a numeric cloud-chat recipient and copies a bounded local attachment. Sending
persists an uncertain dispatch marker before calling sendMessage. Receiver updates
are matched by chat ID and temporary message ID, including updates that arrive before
the initial response. Repeated dispatch never invokes sendMessage again. Across a
crash without a native ID the result remains uncertain; at-most-once dispatch is not
a guarantee that every attempted message reaches Telegram. Unknown results require
inspection, never a blind resend with a new draft ID.

## Navigation, downloads, native drafts, and scheduling (0.8)

The additional operations have separate typed request/result contracts and a fixed
allowlist in the local wire protocol. Their imports stay lazy so installation can
bootstrap with the standard library before dependencies are installed. All operations
share the existing serialized account-bound session. Multi-request workflows use one
75-second native budget, within the 120-second service deadline.

Navigation never invokes read acknowledgements. Chat pagination snapshots IDs in
memory for ten minutes, caps each snapshot at 500 IDs and returns an explicit coverage
flag. Message cursors bind the account, operation and filters; results include media
metadata even when a message has no text. Message text, names and filenames remain
untrusted. History reads use bounded scans and continuation cursors.

A download checks cloud-chat access, message identity, Telegram saving permissions,
self-destruct restrictions and size before copying. Only files under the fixed TDLib
cache, owned by the current user, can be exported. Copies are streamed into unique
private destinations with no symlink traversal, overwrites or automatic execution.
The separate local-download ceiling is 100 MiB; inline transfer limits are unchanged.

Native drafts have independent durable UUID operation records. A supplied hash detects
changes since the last read; Telegram offers no atomic remote version guard. An
uncertain mutation is never retried automatically. Repeated operation IDs return the
recorded result without overwriting later changes on the user's other devices.

Outgoing preparation pins reply/topic IDs and an optional timezone-qualified schedule.
Expired schedules are rejected before dispatch. Scheduled acceptance is a distinct
terminal outbox state and must not be described as delivery. Subsequent rescheduling,
cancellation and delivery of accepted scheduled messages are managed in Telegram.
