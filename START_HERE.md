# Quick start: Telegram in your AI client

The easiest way is to send this to your local AI client on your computer:

> Install https://github.com/prabchevski/telegram-mcp for my current client.
> Follow AGENTS.md and INSTALL.md, select the package for my operating system,
> verify its SHA-256, and preserve my existing Telegram login. I will complete
> any new login in a local window.

The agent prepares and verifies the installation. You need an installed AI client
and, for the first connection, a login to **your own** Telegram account. Get your
`api_id` and `api_hash` from **API development tools** at
[my.telegram.org](https://my.telegram.org). Do not send the API hash, QR code,
login code, or 2FA password to the AI chat.

## macOS: Codex or Gemini CLI

Apple Silicon and Intel are supported. You need internet access, your selected
client, and [Homebrew](https://brew.sh/). The installer adds uv, Python, and pinned
TDLib; the initial Intel TDLib build can take several minutes. Gemini must be
**Gemini CLI**, not the browser or phone app.

1. Open the [latest release](https://github.com/prabchevski/telegram-mcp/releases/latest).
   Under **Assets**, download `telegram-mcp-macos.zip` and
   `telegram-mcp-macos.zip.sha256` from the same release.
2. In Terminal, go to the directory containing both files and run:

   ```sh
   shasum -a 256 -c telegram-mcp-macos.zip.sha256
   ```

   Continue only after the result is `OK`.
3. Extract the ZIP and open `install-macos.command`. Select **Codex**,
   **Gemini CLI**, or **both**.
4. In the local Terminal, enter your API credentials and confirm the QR login:
   Telegram on your phone → **Settings → Devices → Link Desktop Device**.
   Enter a login code or 2FA password in the same window if requested.
5. Wait for the connection check, then restart your selected client.

If macOS does not open the file when double-clicked, type `bash ` with a trailing
space in Terminal, drag `install-macos.command` into that window, and press Enter.

A compatible saved login is reused. Daily updates from `main` after successful
CI checks are enabled by default. Disable them with `current/tgsearch updates off`
from the installation root printed by the installer.
See the [detailed guide, upgrades from older versions, and removal](INSTALL_MACOS.md).

## Windows x64: Codex desktop or ChatGPT Work

You need internet access and a client with local plugin support. Administrator
rights and WSL are not required. This package does not support native Windows ARM64.
Checks passed on Windows Server 2025 and separate x64 Windows 10 Enterprise
Evaluation 22H2 and Windows 11 Enterprise Evaluation 25H2 virtual machines. The
desktop runs verified installation, reinstallation, and MCP discovery as a
standard user; real Telegram login and the client interface remain unverified.
See [VERIFICATION.md](VERIFICATION.md) for exact builds and evidence.

1. In the [latest release](https://github.com/prabchevski/telegram-mcp/releases/latest),
   download `telegram-mcp-windows.zip` and its `.sha256` from the same release.
   Verify the checksum using the [Windows guide](INSTALL_WINDOWS.md), then extract the ZIP.
2. Run `install-windows.ps1` in 64-bit PowerShell as described in that guide.
3. Authorize Telegram in the local PowerShell window.
4. Restart Codex or ChatGPT. Open **Plugins → Personal**, install **Telegram MCP**,
   and start a new chat with the plugin. If your personal marketplace has a
   different name, select that name. In Codex CLI versions with plugin support,
   use `/plugins` as described in the [Windows guide](INSTALL_WINDOWS.md).

The installer adds the plugin to your personal marketplace, not the public store.
Workspace policies may restrict plugin access. To update on Windows, rerun the
installer from a newer release; your login is preserved.
See [installation, authorization, verification, and removal commands](INSTALL_WINDOWS.md).

## After connecting

Try “Find messages in my Telegram containing …” with a phrase you know.
The default installation exposes **15 tools** for searching and reading, chat
navigation, attachments, voice notes, and separate public-post search. Retrieved
messages are shared with the selected AI client. Secret Chats are not supported.

The agent searches your accessible history and subscriptions first. Before each
new public query, it checks the current free quota, explains whether a search
attempt would be consumed, asks permission, and waits for your answer. The quota
check itself uses no attempt. **Stars payments are disabled.** Telegram transcribes
voice messages on your request, subject to its own quotas.

Sending messages and changing drafts are disabled. Running `tgsearch sending on`
through the installed entry point adds seven more tools, including own-message editing; restart your client afterward.
See the [sending rules](README.md#optional-text-and-file-sending).

One login serves multiple chats and clients. Share the
[repository link](https://github.com/prabchevski/telegram-mcp) or a clean release
archive with others, **never** your installed profile, database, or secrets.
Downloading requires no GitHub account. Each person signs into their own Telegram
account on their own computer.
