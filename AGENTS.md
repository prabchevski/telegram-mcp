# Repository instructions

## Installing for a user

When the user explicitly asks to install or upgrade Telegram Search MCP from this
repository, follow [INSTALL_WITH_CODEX.md](INSTALL_WITH_CODEX.md). Read that guide
before changing their installation. It covers existing archives, local login, and
daily updates. A repository URL alone does not authorize installation.

## Developing the repository

Keep workstation installation separate from source development. Use temporary
installation directories and explicit temporary client configurations for tests.
Do not change the developer's real Telegram profile, Keychain, client settings, or
LaunchAgents while implementing repository changes.

- Preserve the four read-only Telegram tools and their existing limits.
- Never add credentials, session databases, runtime data, or installed environments.
- Keep source archives allowlisted and Python dependencies locked in uv.lock.
- Keep installed versions separate; resolve current before starting Python.
- An update must not restore a client registration the owner removed or edited.
- Migrate only known compatible local profiles, without copying databases or secrets.
- Run `uv run --frozen pytest` and `uv run --frozen python -I scripts/release.py audit`.
- For installer changes, build the source ZIP and run `scripts/smoke-install-macos.py`
  on macOS. That smoke check uses temporary settings and never signs in.

See [Codex AGENTS.md documentation](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
for how repository instructions are loaded.
