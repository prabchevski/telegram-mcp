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

The service accepts a fixed set of requests: four read-only operations and local
diagnostics/shutdown. It does not accept arbitrary TDLib methods, Python function
names, media paths, or commands to execute through the connection.

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
with `-I` isolation. TDLib paths are fixed for Homebrew on Apple Silicon/Intel.
Project environment variables cannot select another database through
`TGSEARCH_DATA_DIR` or another library through `TDJSON_LIBRARY`.
Secrets are not placed in MCP configuration.

## Timeouts, queue, and shutdown

By default, the queue holds up to 16 waiting requests plus one active operation.
Each request has a 120-second deadline including time spent in the queue. Client
configurations allow 150 seconds to leave room for startup. The service exits after
10 minutes without read requests. The local connection is limited to 40 connections
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

| Area | Version 0.5 behavior |
| --- | --- |
| Data | `~/Library/Application Support/TelegramSearchMCPShared/profiles/default` |
| Keychain service | `local.unofficial-telegram-search-mcp-shared` |
| Credentials | `default:api_hash`, `default:database_key`; local Keychain only |
| Previous Codex/Gemini data | Explicit installer upgrade can select one compatible profile in place |
| Authorization | Your own api_id/api_hash, QR, and code/2FA in Terminal if required |
| Media | Text and structured metadata for both clients |
| Full image | Additional `_meta` containing the Codex `original` hint |
| Transfer | Preview ≤2 MiB, full ≤12 MiB; format rendering varies by client |
| TDLib | Version and schema 1.8.0; unknown versions are rejected |

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
The existing service finishes its active work and exits after its normal idle delay.

A per-user macOS LaunchAgent checks once a day. The updater accepts only the exact
canonical main SHA with a successful push workflow, downloads a pinned source ZIP,
and validates paths, file types, size, and source allowlists. It stages locked
dependencies and checks imports before activation. Installation locks prevent
concurrent activation; client settings and the receipt are checked for changes
during download. Background updates do not rewrite client settings. Disabling
updates removes the schedule; disconnecting the last registered client also disables it.

An installation receipt records version, revision, client config paths, and update
preference. It contains no Telegram credentials. Code updates trust the maintainer's
canonical repository and the locked dependency sources; passing CI is a functional
check, not proof against every malicious or faulty future change.

## Limitations

Full-text search depends on the history available in Telegram. Messages without
supported text may not work as context anchors. Videos are returned as thumbnails;
the application does not perform bulk history exports. TDLib caches media locally
in the profile. Read-only restrictions apply to Telegram operations; authorization,
the local database, cache, and installation settings still change as needed.
