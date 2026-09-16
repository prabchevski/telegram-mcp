# Verification

## Version 0.7.2 — September 16, 2026

- Rename the shared agent installation guide to `INSTALL_WITH_AI.md` and describe
  Codex and Gemini CLI equally, including a separate command for each client.
- Update all current documentation, required archive files and generated release
  links to the new guide name. Historical releases retain their original files.
- The unchanged 284-test suite, source inventory, archive/wheel verification and
  actual isolated macOS install/0.6.1 migration checks cover this documentation release.
- Runtime behavior, tool counts and verification limits remain those of 0.7.1.

Current reproducible checks:

```sh
uv run --frozen pytest
uv run --frozen python -I scripts/release.py audit
uv run --frozen python -I scripts/release.py build --output dist
uv build --wheel --out-dir dist
uv run --frozen python -I scripts/release.py verify-wheel dist/telegram_search_mcp-0.7.2-py3-none-any.whl
uv run --frozen python -I scripts/smoke-install-macos.py dist/telegram-mcp-macos-v0.7.2.zip
uv run --frozen python -I scripts/smoke-upgrade-06.py dist/telegram-mcp-macos-v0.7.2.zip
python3 -I scripts/publish-release.py dist
```

The last command only prepares and verifies publication files; publication is a
separate CI step after the test and archive jobs succeed. Actual run results are
available in [GitHub Actions](https://github.com/prabchevski/telegram-mcp/actions).

## Version 0.7.1 — September 16, 2026

- 284 automated tests passed locally, including publication checks. The 272 core
  tests and the Linux/macOS Python 3.12/3.14
  matrix passed for the migration implementation. Publication checks additionally
  cover matching artifacts, release notes and immutable published versions.
- The real unmodified 0.6.1 installer/updater installed the candidate in disposable
  macOS directories. The new updater refreshed both client registrations, preserved
  unrelated settings and sending off/on, and exposed six/nine tools over real MCP stdio.
  GitHub responses and desktop notification delivery were substituted in this test.
- Repeat runs, private backups, removed/edited registrations, concurrent edits,
  notification failure and graceful service replacement are covered.
- The actual source ZIP installation/update and wheel/source inventory checks passed.
- Intel background preparation and build serialization have simulated coverage;
  compilation on physical Intel hardware remains unverified.
- Existing Telegram login/session files, Keychain and personal client settings are
  not accessed by the development/install tests. A full daily cycle on another
  person's computer and a live Gemini CLI model session remain unverified.

## Version 0.7.0 — September 16, 2026

- 254 automated tests passed locally. An actual isolated macOS install and update
  preserved both client configurations and discovered six/nine tools as configured.

- Native Telegram recognition is covered for voice notes and video notes, cached
  results, pending text, timeout/restart duplicate protection, account binding,
  protected/self-destructing content, Premium/quota errors and bounded transcripts.
- Voice listing and modern TDLib pagination are tested, including partial-page
  continuation and exhausted cursors. Voice-only messages remain addressable.
- Both MCP clients share serialized recognition and receive text with a trust boundary.
- The pinned native TDLib parser/version/commit are checked without opening a profile.
- Intel compilation is implemented but not verified on physical Intel hardware.
- After the owner restarted Codex, two explicitly selected real voice notes were
  transcribed through the installed MCP using Telegram-native recognition. Both
  returned `completed`; polling without starting returned the same cached result.
  One Telegram transcript ended mid-word without MCP truncation. Audio accuracy
  was not independently assessed. No private transcript is included in this repo.


## Version 0.6.1 — September 15, 2026

- All 228 automated tests passed after updating the repository address and expected
  GitHub source-archive root to telegram-mcp.
- The renamed source ZIP passed its inventory audit and an actual isolated macOS
  installation and upgrade, including Codex and Gemini CLI tool discovery.

## Version 0.6.0 — September 15, 2026

- 228 automated tests passed locally, including outgoing text/document preparation,
  native success/failure/timeout handling, duplicate suppression, recovery after
  interrupted preparation, account binding, snapshot integrity, and shared-service
  delivery from two clients.
- The actual macOS source-archive installer was tested with disposable Codex and
  Gemini CLI settings. Both registrations exposed the same seven tools after
  enabling sending and returned to the original four after disabling it.
- Client confirmation settings and unrelated settings were preserved.
- Standard MCP stdio initialization, schemas and tool annotations were checked
  for both the default and sending-enabled configurations.
- After explicit owner authorization, a real text message and a document with
  caption were sent to Saved Messages using telegram_send_message. Both received
  status=sent and were independently read back using telegram_get_message.
- Existing Telegram login and Keychain binding were preserved.
- Gemini CLI registration and the common MCP transport were tested. A live
  Gemini CLI model session was not run on the maintainer's Mac.
- Source archive audit passed; private outbox records, user files and Telegram
  account/session data are excluded from release artifacts.

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

Historical 0.5.0 check commands (use the current commands above for main):

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
[GitHub Actions](https://github.com/prabchevski/telegram-mcp/actions).

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
