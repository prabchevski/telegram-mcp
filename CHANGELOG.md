# Changelog

## 0.11.1 — 2026-10-22

- Fix local document sending for pinned TDLib 1.8.67: wrap `inputFileLocal` in
  `inputDocument`, preserving captions, immutable snapshots and exact reply IDs.
  The previous shape left the actual file unset in the native parser.
- Preserve numeric TDLib errors and exact allowlisted reasons for direct,
  asynchronous and reconciled send failures. Redact arbitrary error text and
  withhold messages for code 406. Keep the existing public `detail` field so
  already-running proxies remain compatible.
- Treat timeouts, aborted requests and server errors as uncertain outcomes:
  retain the attachment and original draft ID without automatic redispatch.
- Add 33 regressions, including two tests of the real pinned native parser.
  Owner-authorized Saved Messages acceptance on the local fix confirmed a
  29,054-byte Markdown document with a 602-unit UTF-16 caption, with and without
  a reply; both downloaded copies matched the source SHA-256.

## 0.11.0 — 2026-10-13

- Deliver verified updates to every supported client: Codex/Gemini CLI on macOS
  and the shared Codex/ChatGPT Work plugin on Windows x64. Windows gains a
  current-user daily/logon task, stable verified uv runtime, immutable upgrades,
  explicit opt-out, check-only mode, durable status and client-restart notices.
- Pin Windows updates to a published release at the exact successful canonical
  main CI SHA. Validate HTTPS redirects, checksum/digest, safe archive inventory,
  generated assets and source revision; refuse downgrade and unpublished fixes.
- Preserve macOS approval/trust and parallel-call preferences while refreshing
  known complete generated tool lists. Removed/disabled/custom connections and
  owner plugin changes are not restored. Normal upgrades and verification follow
  the same policy-preserving rules.
- Share the advertised read/send/edit tool catalog across client registrations
  and MCP discovery. Sending remains off unless enabled by the owner.
- Include a manifest-hashed SOURCE_REVISION in published archives, without adding
  generated files to raw Git snapshots consumed by historical updaters.

## 0.10.1 — 2026-10-13

- Add preparation, application and status tools for editing your own sent text
  messages and supported media captions. Review a five-minute snapshot before
  applying the exact replacement; changed messages fail safely and uncertain
  operations are never dispatched twice. Sending-enabled discovery has 22 tools.
- Normalize TDLib's omitted nullable link-preview fields before preparing,
  applying or reconciling own-message edits. Native JSON omits null object fields;
  sent text without a preview must remain editable, including Saved Messages.
- Keep malformed explicit preview settings rejected and add native-shaped
  regression fixtures across the core, MCP and shared-service transports.
- Exercise the editing core and native private ledger on Windows CI as well as
  macOS/Linux. Authenticated Saved Messages acceptance confirmed one real
  message was edited through MCP and retained the same native message ID.

## 0.10.0 — 2026-10-13

- Add optional preparation, application and status tools for editing the linked
  user's own sent text messages and supported media captions. Sending-enabled
  discovery now contains 22 tools; the default remains 15.
- Pin exact native IDs, account, original content and replacement in a private
  five-minute snapshot. Recheck authorship, edit permission and observed content
  before application; preserve media, caption position and link-preview options.
- Persist dispatch before the native call and never repeat an uncertain edit after
  a timeout or service restart. Status checks reconcile by reading the same message.
- Preserve sending preferences and migrate unchanged 0.9 registrations to the new
  tools. Existing Telegram login and pinned TDLib 1.8.67 remain in place.

## 0.9.3 — 2026-10-08

- Include the previously local read-reliability fix in the public release: preserve exact TDLib message IDs, native continuations, date boundaries, and context across short or empty pages.
- Keep the shared service alive after ordinary Telegram read errors and report operation-specific diagnostics without private message content.
- Explain unpublished local revisions instead of an opaque update HTTP 404; expose the last scheduled update failure in `tgsearch updates status`. Local fixes are never silently overwritten.
- Run native-ID and service error regressions on macOS/Linux and Windows. Add an explicit installed-release live-read verifier and require maintainers to reconcile local fixes before releasing.

## 0.9.2 — 2026-09-29

- Make all user-facing documentation, installation instructions and examples English, including the README, quick start and both platform guides.
- Record the actual Windows edition, build and architecture during installer verification. Desktop-specific checks reject Windows Server and the wrong desktop version instead of treating them as equivalent.
- Verify installation and updates on actual Windows 10 Enterprise 22H2 and Windows 11 Enterprise 25H2 x64 VMs, with 13 acceptance checks passing on each. Require both desktop checks before publishing a release; see VERIFICATION.md for measured builds and the remaining client UI/login acceptance scope.

## 0.9.1 — 2026-09-29

- Publish the Windows x64 installer for both Codex desktop and ChatGPT Work, alongside the macOS installer for Codex and Gemini CLI. This is the first published release of the Windows and public-search changes developed in 0.9.0.
- Make the repository URL the installation entry point: client/platform selection, verified release downloads, local Telegram login, registration, diagnostics, updates and removal are linked from README and the agent installation guide.
- Add read-only `telegram_get_public_search_quota` to inspect the account's live limits without consuming a public-search attempt. Discovery now has 15 tools by default, or 19 with sending.
- Require the agent to search account history/subscriptions first, explain the public scope and current free allowance, ask for explicit permission and wait before every new public query. Include this workflow in portable MCP instructions, tool descriptions and installation guides; never assume a fixed daily allowance or offer Stars payment.
- Gate the initial public search with a five-minute single-use confirmation token bound to the account, query and quota snapshot, plus `user_confirmed=true`. Changed quota requires a fresh check and consent; the flag is the agent's report of human consent. Free native continuation remains within the approved query.
- Migrate exact standard 0.8/0.9.0 registrations to both public-search tools while preserving removed/customized registrations and sending preferences.
- Advance the shared-service version so idle older daemons can be replaced before the new quota operation is used.

## 0.9.0 — 2026-09-29

- Add a native Windows x64 installer and local ChatGPT Work plugin. Immutable program versions and a validated JSON pointer avoid developer-mode symlink requirements.
- Use Windows Credential Manager, per-user protected filesystem ACLs and native file locks. Each user authorizes their own account; no session or secrets ship with the source.
- Retain one shared TDLib owner on Windows, using authenticated loopback transport, bounded requests and graceful draining. Unix socket behavior remains on macOS/Linux.
- Discover and verify the pinned Windows TDLib DLL. Add native Windows CI and isolated installer/MCP-discovery checks; default discovery now has 14 tools, or 18 with sending.
- Preserve other marketplace entries and user-edited/removed plugin registration during updates. Windows updates are manual; macOS updates retain their existing behavior.
- Add `telegram_search_public_posts` on all platforms: public channel text search without joining, separate from account history, free-only with server-fixed `star_count=0` and structured quota outcomes.
- Bind native public-search continuation to account/query, preserve empty-page continuation, suppress duplicates and reject loops. Mark text and channel metadata as untrusted and include only native-confirmed public links.
- Migrate exact standard 0.8 registrations to include the public-search tool; preserve deleted/customized registrations and sending preferences.

## 0.8.0 — 2026-09-16

- Add chat discovery, unread chat/history views, date ranges and per-chat sender/media/topic search.
- Include uncaptioned attachments and expose reply-thread history with bounded pagination.
- Download documents, photos, audio and full video into private local files, up to 100 MiB.
- Add native Telegram text drafts with observed-version checks and durable operation IDs.
- Extend prepared text/document sending with pinned replies, forum topics and native scheduled delivery.
- Distinguish scheduled acceptance from delivery and expose Telegram's current scheduled queue.
- Migrate unchanged 0.6/0.7 client registrations to 13 default tools or 17 with sending, preserving preferences.
- Keep the original four tool limits, login and runtime permissions unchanged.

## 0.7.2 — 2026-09-16

- Rename the installation guide to `INSTALL.md` for both Codex and Gemini CLI.
- Let either agent configure its own client directly; document a Gemini CLI command.
- Update documentation links, archive requirements and generated release notes.

## 0.7.1 — 2026-09-16

- Complete managed 0.6.1 updates by migrating unchanged standard Codex/Gemini
  registrations to six default tools or nine with sending; preserve login and preferences.
- Add a saved restart notice and macOS notification after tool migration.
- Prepare pinned TDLib in the background for old Intel updaters while retaining
  the previous working version; gracefully replace idle older shared services.
- Test the transition through the actual, unmodified 0.6.1 updater on Apple Silicon.
- Document the one-time repair required for pre-rename 0.6.0 and stranded 0.7.0 installs.
- Refresh quick-start, architecture and verification documentation for voice support.
- Publish tested versioned GitHub Releases automatically, with a stable download
  link, SHA-256 checksum, source inventory and wheel; preserve historical releases.

## 0.7.0 — 2026-09-16

- Add Telegram-native voice/video-note transcription with final and pending text, account-limit errors, and duplicate-start protection.
- Add bounded voice-message listing and preserve voice-only messages in message/context retrieval.
- Pin TDLib 1.8.67 and its source commit; adapt authorization, search pagination, and outgoing requests.
- Register six default tools or nine with sending for both Codex and Gemini CLI.

## 0.6.1 — 2026-09-15

- Rename the public repository and project to Telegram MCP.
- Point installation links and checked-main updates at prabchevski/telegram-mcp.
- Match the new GitHub source-archive root and name downloadable archives telegram-mcp-macos.

## 0.6.0 — 2026-09-15

- Add opt-in preparation, text/document sending, and outgoing status tools.
- Pin the resolved recipient, exact text and private attachment snapshot before sending.
- Persist dispatch state before TDLib calls; reuse the same draft ID without duplicate sends.
- Distinguish confirmed, pending, rejected and uncertain results, including native update races.
- Keep the four original tools and read-only default; retain client approval settings.
- Reuse the shared account and service without copying or replacing the Telegram session.

## 0.5.0 — 2026-09-15

- Add a Codex installation guide and scoped AGENTS.md for link-based setup.
- Reuse compatible Codex 0.2 / Gemini 0.3 local profiles and their original Keychain
  namespaces, without copying session data; preserve existing shared 0.4 profiles.
- Add daily opt-out updates from canonical main after successful GitHub checks,
  with manual check/update commands and schedule removal on final disconnection.
- Resolve immutable versions through stable launchers, preserve active operations,
  and activate new shared-service code on its next start.
- Validate pinned source downloads, refuse downgrades, preserve client edits, and
  retain the old version if staging or activation fails.

- Add direct public source downloads and installation instructions.
- Remove the archive shell wrapper; documentation and CI call the Python builder directly.
- Add the MIT License to the repository, package metadata, release archive, wheel,
  and installed copy.
- Translate documentation and installer/uninstaller messages into English.
- Rename the documentation files without the `_RU` suffix and update their links.

## 0.4.0 — 2026-09-15

- Unified `telegram-search-mcp` for Codex and Gemini CLI on macOS.
- One local TDLib service per profile, a shared bounded queue, automatic startup
  and recovery on the next request, and graceful shutdown.
- MCP processes no longer acquire the TDLib profile independently.
- Retained exactly four read-only Telegram operations.
- Retained isolated launchers, fixed TDLib paths, and text media metadata from
  Gemini 0.3. Restored `codex/imageDetail: original` for full images and a 12 MiB
  full-media limit from Codex 0.2; previews remain limited to 2 MiB.
- New independent data storage and Keychain service shared by both clients.
  Existing sessions and secrets are not copied automatically.
- Unified installer, separate client registration, versioned installations,
  diagnostics, Russian-language instructions, a verifiable archive, and CI.

Based on the Gemini CLI 0.3.0 archive and Codex 0.2.0 source.
