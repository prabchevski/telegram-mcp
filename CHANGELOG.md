# Changelog

## Unreleased

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
