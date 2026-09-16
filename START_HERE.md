# Telegram in Codex and Gemini CLI: quick start

This archive connects **your own Telegram account** to Codex, Gemini CLI, or both
clients on one Mac. Gemini integration requires **Gemini CLI**; the Gemini website
and mobile app are not supported.

## Before you start

Prefer AI-assisted setup? Give Codex or Gemini CLI the repository link and ask it to follow
[INSTALL_WITH_AI.md](INSTALL_WITH_AI.md). It can also upgrade an older archive.

1. Install the client you want to use: Codex or Gemini CLI.
2. Install [Homebrew](https://brew.sh/) if you do not already have it.
3. Get your own `api_id` and `api_hash` under **API development tools** at
   [my.telegram.org](https://my.telegram.org). See
   [Telegram's official instructions](https://core.telegram.org/api/obtaining_api_id).

Internet access is required. The installer adds uv, TDLib, and a suitable Python
version if they are missing. Check that Codex/Gemini CLI is installed and available
separately. The archive contains no other person's account, history, or keys.

## Install

1. [Download the latest verified ZIP](https://github.com/prabchevski/telegram-mcp/releases/latest/download/telegram-mcp-macos.zip)
   and extract it. Open the `telegram-mcp-macos` folder.
2. Open `install-macos.command`. Choose **Codex**, **Gemini CLI**, or **both**.
3. On the first installation, enter your `api_id` and hidden `api_hash` in Terminal.
4. Scan the QR code from Telegram on your phone: **Settings → Devices → Link
   Desktop Device**. If prompted, enter your login code and 2FA password in Terminal.
5. Wait for the connection check to succeed, then restart the selected clients.

A compatible saved login from an older archive is reused, so steps 3–4 may be
skipped. Finish old Telegram requests before upgrading. If two old profiles use
different accounts, choose one as described in [the migration guide](INSTALL_MACOS.md#upgrade-old-archives-and-preserve-a-login).

Daily updates from main after successful GitHub checks are enabled by default.
The installer prints the program root; use that path if it differs from the one
below. Old Codex installations may use `~/Applications/TelegramSearchMCPShared`
for the new program. Turn daily updates off with `current/tgsearch updates off`.

If macOS will not open the installer with a double-click, open Terminal, type `bash `
with a trailing space, drag `install-macos.command` into the window, and press Enter.

**Do not paste your api_hash, login code, or 2FA password into an AI chat.** Search
results expose the retrieved messages from your account to the selected AI client.

## Check the installation

Ask your client: "Find messages in my Telegram containing …" and use a phrase you
recognize. Six tools are available by default: search, message, context, media,
voice-message listing, and Telegram-native transcription. You can also ask it to
transcribe a specific voice message; Telegram's account and quota limits apply.

To enable text and document sending, run `current/tgsearch sending on` from the
printed installation root and restart the clients. This adds three tools, for nine
in total. Sending requires your explicit instruction; editing/deleting messages
is not supported. See [sending instructions](README.md#optional-text-and-file-sending).

To check the installation in Terminal:

```sh
"$HOME/Applications/TelegramSearchMCP/current/tgsearch" doctor --connect
```

Sign in once for both clients. A shared service handles simultaneous requests and
starts automatically when first needed.

## More help

- [Detailed installation, updates, and troubleshooting](INSTALL_MACOS.md)
- [Uninstallation](UNINSTALL_MACOS.md)

Share the [repository link](https://github.com/prabchevski/telegram-mcp) with
friends. They can download and install it without a GitHub account or repository
invitation. Each person signs in to their own Telegram account on their own Mac.
