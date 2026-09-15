# Changelog

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
