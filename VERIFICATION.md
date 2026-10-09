# Verification

Thread-history pagination also has sparse-ID coverage across short native pages,
including a single cached row followed by enough rows to fill a 20-item MCP page.
These checks use synthetic sessions and do not access account data.

## Version 0.9.3 — Read reliability and release alignment

This release incorporates the September read-reliability fix that was installed
locally as 0.8.1 but was absent from the public 0.9 series. Windows transport,
credentials and public-search consent behavior are retained. Regression coverage
uses structured native IDs and validates history, date bounds, context, native
continuations, read-error diagnostics, and service survival over Unix sockets and
authenticated loopback TCP. These tests are required in release CI.

The updater refuses to overwrite unpublished local revisions and explains the
recovery step. Scheduled failures remain visible in `tgsearch updates status`
until a successful check replaces them.

For an explicitly authorized owner acceptance, run the released installed Python
with `scripts/verify-live-release.py --install-root INSTALL_ROOT --version VERSION
--revision RELEASE_SHA --chat-id SELECTED_CHAT_ID --query SELECTED_QUERY`. It
checks the installation receipt and running service against the release, then
reads bounded history/search/context through the installed MCP. It never sends,
creates drafts, consumes public-search quota, or prints retrieved message text.
This is a separate live acceptance step; synthetic CI must not be described as
proof of an authenticated Telegram read.

Local and published acceptance results are recorded by the release operator;
Windows desktop CI continues to validate installation and discovery, without
claiming a real Windows account/UI acceptance.

## Version 0.9.2 — English documentation and Windows desktop verification

All installation guides and user-facing examples are in English. Unicode fixture
content and paths remain in tests to verify non-ASCII messages and user folders.

Windows installer verification records the real operating-system edition, display
version, build and architecture. A desktop acceptance run must identify Windows 10
or Windows 11 explicitly and reject Windows Server. The earlier `windows-latest`
checks ran on **Windows Server 2025**, not either desktop edition.

Both desktop VM jobs passed in [run 36602348419](https://github.com/prabchevski/telegram-mcp/actions/runs/36602348419)
at commit `239fa8682df0d01f57bca3006a7118310d9ec92d` on September 29, 2026:

| Actual desktop OS | Build | Architecture | Windows PowerShell | Result |
| --- | --- | --- | --- | --- |
| Windows 10 Enterprise Evaluation 22H2 | 19045.2006 | x64 | 5.1.19041.1682 | 13/13 checks passed |
| Windows 11 Enterprise Evaluation 25H2 | 26200.6584 | x64 | 5.1.26100.6584 | 13/13 checks passed |

Both used Python 3.13.13 and the same verified source archive, SHA-256
`32160c5d067170f04834428c13774b1ad2e6c404fd823e585df2ed1f8857316e`.
Evidence artifacts include `acceptance.json`, `test-user.json` and `provenance.json`.
The OS check uses CIM ProductType and coherent kernel/registry builds; Windows 11
can retain a misleading Windows 10 registry product name.

The checks cover the actual archive installer in Unicode paths, CLI startup,
pinned Python package and native TDLib, preservation of an existing marketplace
plugin, MCP initialization and discovery of 15 tools, sending enabled with 19
tools, a second immutable installation with an unrelated inherited PowerShell
module path, preservation of the previous interpreter and stable launcher,
preservation of sending preferences, and a return to 15 tools with sending off.
Strict cleanup passed as well. A native regression prevents the verifier itself
from holding a pywin32 DLL open while deleting uv-linked temporary environments.

The VMs use official Microsoft evaluation ISOs with pinned SHA-256 values and a
pinned QEMU container. Installation runs under a disposable standard user. The
unattended VM image disables UAC (`EnableLUA=0`); it is not evidence for every OEM
image, enterprise policy, Windows edition, patch level, or interactive UAC flow.
Python/uv bootstrap for the test driver precedes the installer check.

The same source passed [Test and package run 36602356173](https://github.com/prabchevski/telegram-mcp/actions/runs/36602356173),
including Windows Server, Linux/macOS, archive/wheel verification, the actual macOS
installer and the unmodified 0.6.1 updater. Local tests passed 516 checks with 10
native Windows skips; the source inventory contains 95 allowlisted text files.
Publication now requires both desktop VMs to pass again for the exact `main`
revision, alongside the existing tests and package checks.

These checks use disposable programs and client configuration, with no real
Telegram authorization, Telegram tool invocation or search-quota consumption.
Real Codex/ChatGPT graphical plugin activation, the owner's Telegram login, and
authenticated multi-chat use still require acceptance on the target computer.

## Version 0.9.1 — Windows release, public-search quota and explicit consent

This change adds a read-only quota operation and requires a fresh account/query/
quota-bound confirmation token and an explicit confirmation flag before the
initial public search. Tests cover missing, expired, reused and mismatched tokens,
changed quota, zero Stars, and free same-query continuation. The flag reports
consent collected by the agent; it is not independent proof of a human response.
No live Telegram search is used during development and no free quota is consumed.

The quota implementation passed 499 local tests, with 9 native Windows tests skipped,
and 252 native Windows tests plus actual installation/update smoke checks in
[CI run 36594846622](https://github.com/prabchevski/telegram-mcp/actions/runs/36594846622)
at commit `dfc02409276fa36da1d8d9e70411c67b43153b4d`.
Source inventory audit passed with 92 files. The final release additionally includes
the shared Codex/ChatGPT Work Windows wording and consolidated installation guides.
That final source tree passed 499 local tests (9 Windows-only skips), the 93-file
source audit, archive/wheel checks, the actual macOS installer, and the unmodified
0.6.1 updater smoke checks. `GEMINI.md` and both platform installation guides are
required release files.
Its authoritative results are the successful **Test and package** run for the release tag's
commit on `main`; publication runs only after all platform checks pass.
Expected discovery is 15/19/15 tools, with public search marked
non-read-only and quota checks marked read-only. Both platform smoke checks also
assert the default-false confirmation schema and token field.

Version 0.9.1 permits replacing an idle 0.9.0 shared service before using the new
operation. Active operations remain protected. User account authorization and the
Windows Codex/ChatGPT Work interface still require acceptance on the target computer,
including two simultaneous chats and closing the chat that started the service.

## Version 0.9.0 — Windows and free public posts search

Source baseline: remote main `9652983` (0.8.0). The unmodified baseline passed 308 tests on macOS. Changes add Windows ACL/locking/credential tests, mutually authenticated loopback transport tests, plugin registration preservation tests, and a Windows x64 CI job. The installer smoke check uses temporary program and marketplace directories, verifies the pinned native DLL, performs two immutable installations, and checks MCP discovery with 14/18/14 tools without authorizing Telegram or invoking a Telegram tool.

Local macOS result: 457 tests passed, with 9 native Windows tests skipped. Native
Windows CI passed 218 tests and also exercises inherited directory ACLs, Windows PowerShell 5.1,
Cyrillic installation paths, and Unicode MCP traffic through both ordinary and
hidden-console launchers. Actual macOS installation/update and the unmodified
0.6.1 updater passed in disposable installations. Archive, wheel, and publication
asset verification passed; generated Windows assets are checked against their
reviewed source literals.

Historical 0.9.0 CI: [successful run 36593192902](https://github.com/prabchevski/telegram-mcp/actions/runs/36593192902).

Verification status is recorded in the change's CI results. A successful automated run does not establish that a real Windows ChatGPT Work UI has installed the plugin or that a user's Telegram account has authorized successfully. Those two acceptance steps remain with the Windows user. This work never changes a developer's real profile, Keychain/Credential Manager entries, or client settings.

Public-post search uses the same backend, wire transport and MCP registration on
all platforms. Tests cover hardcoded zero Stars, quota preflight and limit races,
unsupported/legacy TDLib, native continuation and metadata trust. No live public
search was performed during development: tests do not consume Telegram quota.
A real Windows ChatGPT Work check must also verify service lifetime while two
chats use the plugin and the chat that started the service is closed.

## Version 0.8.0 — September 16, 2026

- Local result: 308 tests passed on Apple Silicon macOS / Python 3.13.
- Source audit passed; source ZIP, wheel and publication asset verification passed.
- The actual macOS archive installer, background update path and both-client MCP
  discovery passed with sending disabled/enabled (13/17 tools).
- The unmodified 0.6.1 updater installed 0.8.0 in two disposable installations;
  subsequent activation migrated both clients and requested a restart exactly once,
  preserving sending off/on and unrelated settings. No live profile was used.

- Navigation tests cover snapshot pagination, unread state without read receipts,
  exclusive upper dates, timezone validation, sender/media/topic filters, uncaptioned
  files, short native pages and channel discussions in linked groups.
- Download tests cover exact bytes, owner-only permissions, unique destinations,
  forbidden paths/symlinks, protected content and size limits before transfer.
- Draft tests cover changed versions, clearing, account binding, ambiguous timeouts
  and retries that must never overwrite later Telegram edits.
- Sending tests cover pinned replies/schedules, durable duplicate suppression,
  scheduled acceptance and refusing expired schedules instead of sending immediately.
- End-to-end tests exercise the actual MCP schema, Unix service connection and native
  backend with an injected fake Telegram session; no personal account is opened.
- Old running proxies retain the original outgoing response shape; updated proxies
  request reply/schedule metadata explicitly. The compatibility round trip is tested.
- Standard 0.6/0.7 registrations migrate for both clients with sending on/off;
  customized/removed registrations retain their existing settings.
- Telegram request shapes were checked against the pinned TDLib source commit
  `d1085f9cebc5a62379991ae1652673954f229c1f`, including its updated draft content schema.
- These new workflows have not yet been tested against a signed-in real Telegram
  account. Native draft synchronization, real downloads, and scheduled delivery need
  an explicitly authorized live acceptance run. Existing voice live evidence below
  does not establish these new behaviors. Physical Intel and live Gemini model-session
  limitations from previous releases remain.

Current reproduction commands:

```sh
uv run --frozen pytest
uv run --frozen python -I scripts/release.py audit
uv run --frozen python -I scripts/release.py build --output dist
uv build --wheel
uv run --frozen python -I scripts/smoke-install-macos.py dist/telegram-mcp-macos-v0.8.0.zip
uv run --frozen python -I scripts/smoke-upgrade-06.py dist/telegram-mcp-macos-v0.8.0.zip
python3 -I scripts/publish-release.py dist
```

## Version 0.7.2 — September 16, 2026

- Rename the shared agent installation guide to `INSTALL.md` and describe
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
