# Telegram in Codex and Gemini CLI: quick start

This archive connects **your own Telegram account** to Codex, Gemini CLI, or both
clients on one Mac. Gemini integration requires **Gemini CLI**; the Gemini website
and mobile app are not supported.

## Before you start

1. Install the client you want to use: Codex or Gemini CLI.
2. Install [Homebrew](https://brew.sh/) if you do not already have it.
3. Get your own `api_id` and `api_hash` under **API development tools** at
   [my.telegram.org](https://my.telegram.org). See
   [Telegram's official instructions](https://core.telegram.org/api/obtaining_api_id).

Internet access is required. The installer adds uv, TDLib, and a suitable Python
version if they are missing. Check that Codex/Gemini CLI is installed and available
separately. The archive contains no other person's account, history, or keys.

## Install

1. [Download the latest source ZIP](https://github.com/prabchevski/telegram-search-mcp/archive/refs/heads/main.zip)
   and extract it. Open the `telegram-search-mcp-main` folder.
2. Open `install-macos.command`. Choose **Codex**, **Gemini CLI**, or **both**.
3. On the first installation, enter your `api_id` and hidden `api_hash` in Terminal.
4. Scan the QR code from Telegram on your phone: **Settings → Devices → Link
   Desktop Device**. If prompted, enter your login code and 2FA password in Terminal.
5. Wait for the connection check to succeed, then restart the selected clients.

If macOS will not open the installer with a double-click, open Terminal, type `bash `
with a trailing space, drag `install-macos.command` into the window, and press Enter.

**Do not paste your api_hash, login code, or 2FA password into an AI chat.** Search
results expose the retrieved messages from your account to the selected AI client.

## Check the installation

Ask your client: "Find messages in my Telegram containing …" and use a phrase you
recognize. Four tools are available: search, message, context, and media.
This MCP cannot send or change messages.

To check the installation in Terminal:

```sh
"$HOME/Applications/TelegramSearchMCP/current/tgsearch" doctor --connect
```

Sign in once for both clients. A shared service handles simultaneous requests and
starts automatically when first needed.

## More help

- [Detailed installation, updates, and troubleshooting](INSTALL_MACOS.md)
- [Uninstallation](UNINSTALL_MACOS.md)

Share the [repository link](https://github.com/prabchevski/telegram-search-mcp) with
friends. They can download and install it without a GitHub account or repository
invitation. Each person signs in to their own Telegram account on their own Mac.
