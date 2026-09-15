# Uninstall Telegram Search MCP

## Disconnect the MCP while keeping your login

Use the root printed by the installer in these commands. When preserving a Codex
0.2 program, the new root may be `~/Applications/TelegramSearchMCPShared`.

Finish active Telegram requests, then run:

```sh
bash "$HOME/Applications/TelegramSearchMCP/current/uninstall-macos.command" --clients both
```

You can select only `codex` or `gemini`. The script removes recognized registrations
for this shared package and backs up the settings. It preserves unrelated MCP
entries, old 0.2/0.3 installations, program files, the profile, and Keychain entries.
Restart the selected clients. The shared service does not stop automatically,
because another connected client may still be using it.
Disconnecting the last client recorded by the 0.5 installer also removes its daily
update schedule. If you edited client configuration manually, run
`current/tgsearch updates off` before deleting the program directory.

If running the script from an extracted archive, specify the installed version or
its `current` link:

```sh
bash uninstall-macos.command --install-dir "$HOME/Applications/TelegramSearchMCP/current" --clients both
```

Here, `--install-dir` means one installed version; the installer uses the same
option for the root containing all versions. Replace the path if you used a custom
installation directory. If you used `--codex-config` or `--gemini-config`, pass
the same absolute paths to the uninstaller.

## Remove the program completely

1. Disconnect it from **all** clients using the command above, and close their old tasks.
2. Stop the service:

   ```sh
   "$HOME/Applications/TelegramSearchMCP/current/tgsearch" service stop
   ```

3. In Finder, delete `~/Applications/TelegramSearchMCP`. If you installed with a
   different `--install-dir`, delete that program directory instead.

Homebrew, uv, Python, and TDLib may be used by other programs, so the script does
not remove them automatically.

## Optionally remove data and revoke the login

The following paths apply to a fresh shared profile. If you reused an old archive's
login, data remains in `TelegramSearchMCP` (Codex) or `TelegramSearchMCPGemini`
(Gemini), with the original Keychain service. The private shared
`profile-source.json` records which was selected. Those old directories may still
serve other retained installations; do not delete them as part of routine removal.
Identify the actual Telegram device session before revoking a reused login.

Follow these steps only if you want to delete your local history and revoke this
installation's authorization:

1. In Telegram, open **Settings → Devices** and terminate the session named
   **Shared local read-only Telegram search**. Check the device and login time.
2. After stopping the service, use Finder to delete
   `~/Library/Application Support/TelegramSearchMCPShared`.
3. In macOS **Keychain Access**, find the service
   `local.unofficial-telegram-search-mcp-shared` and delete its
   `default:api_hash` and `default:database_key` entries.

Keep other applications' entries and any old profiles you still need.
Do not delete `tdlib.lock` to "fix" a running session. Stopping the service closes
the database without logging out; revoke the device from Telegram itself.

Skip this section if you want to preserve your login for a later reinstallation.
Removing only the program preserves the profile and Keychain entries.
