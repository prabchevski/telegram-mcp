#!/bin/bash
set -euo pipefail
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd -P)"
INSTALL_DIR=""
CLIENT_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      cat <<'EOF'
Disconnect Telegram Search MCP from clients while keeping your history and login:
  bash uninstall-macos.command --install-dir /absolute/path/to/version [options]
--install-dir is optional when running this script from an installed version.
  --clients codex|gemini|both    Default: both.
  --codex-config PATH           Explicit Codex settings path.
  --gemini-config PATH          Explicit Gemini settings path.

Only recognized registrations for this shared package are removed. Settings are
backed up. The program, profile, and Keychain entries are preserved. After all
clients are disconnected, run tgsearch service stop and remove the program directory.
Other installations and old Codex/Gemini 0.2/0.3 sessions are not affected.
EOF
      exit 0 ;;
    --install-dir)
      [[ $# -ge 2 ]] || exit 2
      INSTALL_DIR="$2"; shift ;;
    --clients|--codex-config|--gemini-config)
      [[ $# -ge 2 ]] || exit 2
      CLIENT_ARGS+=("$1" "$2"); shift ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
  shift
done
INSTALL_DIR="${INSTALL_DIR:-$SOURCE_DIR}"
[[ "$INSTALL_DIR" = /* && -f "$INSTALL_DIR/.telegram-search-install" && -x "$INSTALL_DIR/client-config" ]] || {
  printf 'Specify an installed version: --install-dir /absolute/path/to/version\n' >&2
  exit 1
}
"$INSTALL_DIR/client-config" unregister "${CLIENT_ARGS[@]}"
printf '\nDisconnected. Restart the selected clients.\n'
printf 'Program files, your history, and your Telegram login have been preserved.\n'
printf 'Once all clients are disconnected, stop the shared service:\n  %q/tgsearch service stop\n' "$INSTALL_DIR"
