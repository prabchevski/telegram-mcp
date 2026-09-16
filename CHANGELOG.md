# Changelog

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
