# Verification

## Version 0.5.0 — September 15, 2026

**201 tests passed on Python 3.13, Apple Silicon macOS**, including the existing
search/service/registration suite and new migration/update checks:

- Compatible Codex/Gemini profile selection with private synthetic policies and
  databases; no DB copy; original Keychain namespace selection; existing shared
  profile priority; different-account refusal and explicit selection.
- A real held legacy file lock blocks adoption. Unsafe profile paths and policies
  are rejected. Tests never access the owner's actual Telegram credentials.
- Exact canonical main commit/branch/workflow success is required. Malformed,
  private-data, binary, and path-traversal source archives are refused.
- Real stable shell launchers resolve immutable versions before starting a process.
  Existing proxies choose the newest interpreter for the next service start.
- Failed activation restores the previous version and a changed client registration.
  Failed dependencies, concurrent config edits, disabled updates, and daily retry
  throttling keep the prior installation. Removed registrations are not restored.
- LaunchAgent enable/recovery/disable and last-client removal use mocked launchctl
  calls in temporary directories; no personal background job is installed by tests.
- A real source ZIP was installed with uv into a temporary macOS directory, then
  updated through the background installation path using the old installed Python.
  Client settings stayed byte-for-byte identical and the old executable remained.
- The resulting stable launcher completed an MCP handshake and exposed exactly
  four read-only tools, without invoking Telegram or opening a session.
- Source inventory passed; the built wheel matched the source and license metadata.
- The updater's unauthenticated GitHub API lookup recognized the already successful
  canonical main workflow. No personal installation was updated by that lookup.

Run the checks from the repository:

```sh
uv run --frozen pytest
uv run --frozen python -I scripts/release.py audit
uv run --frozen python -I scripts/release.py build --output dist
uv build --wheel --out-dir dist
uv run --frozen python -I scripts/release.py verify-wheel dist/telegram_search_mcp-0.5.0-py3-none-any.whl
uv run --frozen python -I scripts/smoke-install-macos.py dist/telegram-search-mcp-macos-v0.5.0.zip
```

CI runs the full suite on Linux/macOS with Python 3.12/3.14, then builds and performs
the real macOS archive installation/update check. Results appear in
[GitHub Actions](https://github.com/prabchevski/telegram-search-mcp/actions).

These checks do not establish successful migration of every real Telegram session,
first-time login, live search/media rendering in each AI client, physical Intel Mac
compatibility, or a full 24-hour launchd run on another user's computer. Those
require the owner's local installation. A revoked session still needs authorization.

## Historical 0.4.0 verification

Date: **September 15, 2026**. Checks were performed in a separate working copy
on Apple Silicon macOS, without using the active Telegram profile.

## Automated checks

**159 tests passed on Python 3.13**, with no skips, for the original 0.4.0 release.

- Existing search, message, context, and media operations; limits and untrusted
  metadata; path, privacy, and account/profile binding checks.
- Two real MCP stdio processes making six parallel calls through one shared test
  service, with at most one operation running at a time.
- Six independent processes starting one service simultaneously; after MCP clients
  close, the service remains available to the next task.
- Client cancellation, expired requests, a full queue, slow clients, graceful
  shutdown, and automatic idle shutdown.
- SIGTERM during a simulated native operation: the lock remains held until work
  finishes. A separate test process is forcibly crashed, then service recovery
  handles the stale socket without deleting the lock file.
- Unconfirmed TDLib closure keeps a real file lock that another owner cannot
  acquire. A native session failure retires the service instead of opening another
  TDLib client over the previous session.
- Protocol limits, UID and file permission checks, a fixed operation allowlist,
  and an actual transfer of 12 MiB of media through the local socket.
- Registration of both clients in separate temporary settings; preservation of
  unrelated settings; backups; refusal of unknown conflicts; rollback after a
  failed second write; simultaneous installers; recognition of legacy entry names.
- The installer always selects a uv-managed Python, excluding system Python and
  project environments; interpreter availability and download behavior were checked.
- Real installation with uv in a separate macOS directory, reinstallation while
  preserving the old executable, and verification/removal of temporary registrations.
- Deterministic archives, checksums, complete inventories, exclusion of runtime data
  and credentials, and verification that the wheel matches the source.

Queue tests replace Telegram with a simulated message source. These checks verify
process/session coordination and data transfer. They do not verify searches in a
real account or rendering of every media format inside Codex/Gemini.

## Live checks without account authorization

- A real MCP process: stdio handshake and discovery of exactly four tools.
- The installed native TDLib created a client and confirmed closure twice, without
  receiving database parameters, API credentials, or an account.
- Installation and update from a built ZIP, followed by starting the installed
  MCP and discovering four tools over stdio.
- Personal Codex and Gemini settings had identical checksums before and after the
  work. The previous working installation and profile were not switched.

Reproducible commands:

```sh
uv sync --frozen --group dev
uv run --frozen pytest
uv run --frozen python -I scripts/release.py build --output dist
uv build --wheel --out-dir dist
uv run --frozen python -I scripts/release.py verify-wheel dist/telegram_search_mcp-0.4.0-py3-none-any.whl
uv run --frozen python -I scripts/smoke-install-macos.py dist/telegram-search-mcp-macos-v0.4.0.zip
# Optional, with Homebrew TDLib installed; no login or database:
uv run --frozen python -I scripts/smoke-native-lifecycle.py
```

CI is configured for Linux/macOS and Python 3.12/3.14, followed by building and
performing a real isolated installation of the archive on macOS. Individual run
results are available under the repository's **Actions** tab, subject to access.

## Checks requiring user authorization or an installed client

- The account owner's new Telegram login for the 0.4 profile.
- Search and media retrieval through a live authorized account in the new version.
- Rendering inside an installed Gemini CLI. Gemini CLI was absent on the test Mac;
  its registration format and the standard MCP protocol were checked.
- Final verification of the new registration in personal Codex/Gemini clients after
  switching. The previous working Codex installation was preserved, so live
  Telegram search through its new registration has not been verified.
- Operation on a physical Intel Mac. Standard paths are supported, but no separate
  Intel device was available for testing.

The repeated profile-lock issue was addressed and tested with isolated concurrent
processes. The fix takes effect in a personal installation after signing in to the
new version and switching client registration. Existing active processes were
not stopped.
