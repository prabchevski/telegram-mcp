#!/bin/bash
# Personal macOS installation. Releases are immutable; running clients keep their version.
set -euo pipefail
umask 077
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd -P)"
SAFE_EXEC_PATH="/usr/bin:/bin:/usr/sbin:/sbin"
SAFE_LANG="en_US.UTF-8"
PREPARE_ONLY=false
SKIP_SYSTEM_DEPS=false
REPLACE_LEGACY=false
UPGRADE=false
MIGRATE_PROFILE=""
AUTO_UPDATE=""
CLIENTS=""
REQUESTED_INSTALL_DIR=""
CODEX_CONFIG=""
GEMINI_CONFIG=""

usage() {
  cat <<'EOF'
Telegram MCP — macOS installation

bash install-macos.command [--clients codex|gemini|both|none] [--prepare-only]
  --prepare-only          Install and register without signing in to Telegram.
  --clients none          Prepare files without changing client settings.
  --install-dir PATH      Version root (default: ~/Applications/TelegramSearchMCP).
  --codex-config PATH     Explicit config.toml path (for isolated checks).
  --gemini-config PATH    Explicit settings.json path (for isolated checks).
  --replace-legacy        Replace only recognized 0.2/0.3 registrations.
  --upgrade               Upgrade recognized old registrations and reuse a compatible login.
  --migrate-profile NAME  auto|codex|gemini|none; choose when both old profiles exist.
  --auto-update MODE      on|off|keep; daily checked-main updates (on for interactive installs).
  --skip-system-deps      Skip Homebrew; requires --prepare-only and existing uv.

Internet access and Homebrew (https://brew.sh/) are required. Dependencies download separately.
Gemini support requires the local Gemini CLI.
Update: daily after successful GitHub checks; tgsearch update checks immediately.
Disconnect: bash uninstall-macos.command --help
EOF
}
fail() { printf '%s\n' "$1" >&2; exit 1; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --prepare-only) PREPARE_ONLY=true ;;
    --skip-system-deps) SKIP_SYSTEM_DEPS=true ;;
    --replace-legacy) REPLACE_LEGACY=true ;;
    --upgrade) REPLACE_LEGACY=true; UPGRADE=true ;;
    --clients|--install-dir|--codex-config|--gemini-config|--migrate-profile|--auto-update)
      [[ $# -ge 2 && -n "$2" ]] || fail "A value is required after $1."
      case "$1" in
        --clients) CLIENTS="$2" ;;
        --install-dir) REQUESTED_INSTALL_DIR="$2" ;;
        --codex-config) CODEX_CONFIG="$2" ;;
        --gemini-config) GEMINI_CONFIG="$2" ;;
        --migrate-profile) MIGRATE_PROFILE="$2" ;;
        --auto-update) AUTO_UPDATE="$2" ;;
      esac
      shift ;;
    *) fail "Unknown option: $1" ;;
  esac
  shift
done
[[ "$(/usr/bin/uname -s)" == Darwin ]] || fail "This installer supports macOS only."
[[ "$SKIP_SYSTEM_DEPS" == false || "$PREPARE_ONLY" == true ]] || fail "--skip-system-deps requires --prepare-only."
if [[ -z "$CLIENTS" ]]; then
  if [[ -t 0 ]]; then
    printf '\nWhere should Telegram Search be registered?\n  1) Codex\n  2) Gemini CLI\n  3) Both clients\n'
    read -r -p 'Choose 1, 2, or 3 [3]: ' choice
    case "$choice" in 1) CLIENTS=codex ;; 2) CLIENTS=gemini ;; 3|"") CLIENTS=both ;; *) fail "Choose 1, 2, or 3." ;; esac
  else
    CLIENTS=both
  fi
fi
case "$CLIENTS" in codex|gemini|both|none) ;; *) fail "--clients: accepted values are codex, gemini, both, none." ;; esac
[[ "$CLIENTS" != none || "$PREPARE_ONLY" == true ]] || fail "--clients none requires --prepare-only."

find_brew() {
  local candidate
  for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [[ -x "$candidate" ]]; then printf '%s\n' "$candidate"; return; fi
  done
  command -v brew
}
if [[ "$SKIP_SYSTEM_DEPS" == false ]]; then
  BREW_BIN="$(find_brew)" || fail "Install Homebrew from https://brew.sh/ and try again."
  printf '\nChecking uv and TDLib. Missing dependencies will be downloaded…\n'
  "$BREW_BIN" list --versions uv >/dev/null 2>&1 || "$BREW_BIN" install uv
  "$BREW_BIN" list --versions tdlib >/dev/null 2>&1 || "$BREW_BIN" install tdlib
  UV_BIN="$("$BREW_BIN" --prefix)/bin/uv"
else
  UV_BIN="${TGSEARCH_INSTALL_UV:-$(command -v uv || true)}"
fi
[[ "$UV_BIN" = /* && -x "$UV_BIN" ]] || fail "An absolute path to the uv executable is required (TGSEARCH_INSTALL_UV)."
# The managed bootstrap interpreter is read-only with respect to Telegram and clients.
# --system excludes project virtualenvs; --managed-python excludes system/Homebrew Python.
# Never repair permissions of a global interpreter, and do not install global Python shims.
BOOTSTRAP_PYTHON="$("$UV_BIN" python find --managed-python --system --no-project --no-config 3.13 2>/dev/null || true)"
if [[ -z "$BOOTSTRAP_PYTHON" ]]; then
  "$UV_BIN" python install --no-bin --no-config 3.13
  BOOTSTRAP_PYTHON="$("$UV_BIN" python find --managed-python --system --no-project --no-config 3.13)"
fi
SAFE_USER_HOME="$(/usr/bin/env -i PATH="$SAFE_EXEC_PATH" "$BOOTSTRAP_PYTHON" -I -c 'import os,pwd; print(pwd.getpwuid(os.getuid()).pw_dir)')"
[[ -z "$REQUESTED_INSTALL_DIR" || "$REQUESTED_INSTALL_DIR" = /* ]] || fail "--install-dir must be an absolute path."
[[ -z "$CODEX_CONFIG" || "$CODEX_CONFIG" = /* ]] || fail "--codex-config must be an absolute path."
[[ -z "$GEMINI_CONFIG" || "$GEMINI_CONFIG" = /* ]] || fail "--gemini-config must be an absolute path."

# Retain configured client-home overrides solely for registration; runtime ignores them.
[[ -n "$CODEX_CONFIG" || -z "${CODEX_HOME:-}" ]] || CODEX_CONFIG="$CODEX_HOME/config.toml"
[[ -n "$GEMINI_CONFIG" || -z "${GEMINI_CLI_HOME:-}" ]] || GEMINI_CONFIG="$GEMINI_CLI_HOME/.gemini/settings.json"

[[ "$UPGRADE" == false ]] || MIGRATE_PROFILE="${MIGRATE_PROFILE:-auto}"
if [[ "$PREPARE_ONLY" == true ]]; then
  MIGRATE_PROFILE="${MIGRATE_PROFILE:-none}"
  AUTO_UPDATE="${AUTO_UPDATE:-keep}"
else
  MIGRATE_PROFILE="${MIGRATE_PROFILE:-auto}"
  AUTO_UPDATE="${AUTO_UPDATE:-on}"
  REPLACE_LEGACY=true
fi
installer_args=(--source "$SOURCE_DIR" --uv "$UV_BIN" --python "$BOOTSTRAP_PYTHON" --clients "$CLIENTS" --migrate-profile "$MIGRATE_PROFILE" --auto-update "$AUTO_UPDATE")
[[ -z "$REQUESTED_INSTALL_DIR" ]] || installer_args+=(--install-dir "$REQUESTED_INSTALL_DIR")
[[ -z "$CODEX_CONFIG" ]] || installer_args+=(--codex-config "$CODEX_CONFIG")
[[ -z "$GEMINI_CONFIG" ]] || installer_args+=(--gemini-config "$GEMINI_CONFIG")
[[ "$PREPARE_ONLY" == false ]] || installer_args+=(--prepare-only)
[[ "$REPLACE_LEGACY" == false ]] || installer_args+=(--replace-legacy)
exec /usr/bin/env -i HOME="$SAFE_USER_HOME" PATH="$SAFE_EXEC_PATH" LANG="$SAFE_LANG" \
  "$BOOTSTRAP_PYTHON" -I "$SOURCE_DIR/scripts/install.py" "${installer_args[@]}"
