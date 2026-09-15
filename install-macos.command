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
CLIENTS=""
REQUESTED_INSTALL_DIR=""
CODEX_CONFIG=""
GEMINI_CONFIG=""

usage() {
  cat <<'EOF'
Telegram Search MCP — macOS installation

bash install-macos.command [--clients codex|gemini|both|none] [--prepare-only]
  --prepare-only          Install and register without signing in to Telegram.
  --clients none          Prepare files without changing client settings.
  --install-dir PATH      Version root (default: ~/Applications/TelegramSearchMCP).
  --codex-config PATH     Explicit config.toml path (for isolated checks).
  --gemini-config PATH    Explicit settings.json path (for isolated checks).
  --replace-legacy        Replace only recognized 0.2/0.3 registrations.
  --skip-system-deps      Skip Homebrew; requires --prepare-only and existing uv.

Internet access and Homebrew (https://brew.sh/) are required. Dependencies download separately.
Gemini support requires the local Gemini CLI.
Update: run the installer from the new archive.
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
    --clients|--install-dir|--codex-config|--gemini-config)
      [[ $# -ge 2 && -n "$2" ]] || fail "A value is required after $1."
      case "$1" in
        --clients) CLIENTS="$2" ;;
        --install-dir) REQUESTED_INSTALL_DIR="$2" ;;
        --codex-config) CODEX_CONFIG="$2" ;;
        --gemini-config) GEMINI_CONFIG="$2" ;;
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
INSTALL_ROOT="${REQUESTED_INSTALL_DIR:-$SAFE_USER_HOME/Applications/TelegramSearchMCP}"
[[ "$INSTALL_ROOT" = /* ]] || fail "--install-dir must be an absolute path."
[[ -z "$CODEX_CONFIG" || "$CODEX_CONFIG" = /* ]] || fail "--codex-config must be an absolute path."
[[ -z "$GEMINI_CONFIG" || "$GEMINI_CONFIG" = /* ]] || fail "--gemini-config must be an absolute path."

# Retain configured client-home overrides solely for registration; runtime ignores them.
[[ -n "$CODEX_CONFIG" || -z "${CODEX_HOME:-}" ]] || CODEX_CONFIG="$CODEX_HOME/config.toml"
[[ -n "$GEMINI_CONFIG" || -z "${GEMINI_CLI_HOME:-}" ]] || GEMINI_CONFIG="$GEMINI_CLI_HOME/.gemini/settings.json"
INSTALL_DIR="$(/usr/bin/env -i HOME="$SAFE_USER_HOME" PATH="$SAFE_EXEC_PATH" LANG="$SAFE_LANG" \
  "$BOOTSTRAP_PYTHON" -I - "$SOURCE_DIR" "$INSTALL_ROOT" <<'PY'
import os, pathlib, shutil, stat, sys, tempfile, tomllib
source, root = map(pathlib.Path, sys.argv[1:])
project = tomllib.loads((source / 'pyproject.toml').read_text())['project']
if project['name'] != 'telegram-search-mcp':
    raise SystemExit('The archive does not contain the expected telegram-search-mcp project.')
version = project['version']
if not version or any(c not in '0123456789.-abcdefghijklmnopqrstuvwxyz' for c in version):
    raise SystemExit('Invalid project version.')
for candidate in (root, *root.parents):
    if candidate.is_symlink():
        raise SystemExit('The installation path must not traverse symlinks.')
    if candidate.exists():
        info = candidate.stat()
        if not candidate.is_dir() or info.st_uid not in (os.getuid(), 0):
            raise SystemExit('Unsafe installation directory ownership.')
        if stat.S_IMODE(info.st_mode) & 0o022 and not info.st_mode & stat.S_ISVTX:
            raise SystemExit('The installation directory is writable by other users.')
root.mkdir(mode=0o700, parents=True, exist_ok=True)
if root.stat().st_uid != os.getuid():
    raise SystemExit('The installation directory must belong to the current user.')
marker = root / '.telegram-search-install-root'
if marker.is_symlink() or (marker.exists() and not marker.is_file()):
    raise SystemExit('Unsafe installation directory marker.')
current = root / 'current'
if current.is_symlink():
    previous = current.resolve()
    if previous.parent != root or not (previous / '.telegram-search-install').is_file():
        raise SystemExit('current points to unrelated files; it has been left unchanged.')
elif current.exists():
    raise SystemExit('current already exists and is not a symlink; it has been left unchanged.')
if root.exists() and not marker.exists() and any(root.iterdir()):
    raise SystemExit('The directory contains unrelated files; choose an empty directory.')
marker.write_text('telegram-search-mcp\n')
marker.chmod(0o600)
# Unique version directory: never rewrite a virtual environment a running process uses.
target = pathlib.Path(tempfile.mkdtemp(prefix=version + '-', dir=root))
try:
    for name in ('pyproject.toml', 'uv.lock', 'README.md', 'LICENSE', 'install-macos.command', 'uninstall-macos.command'):
        item = source / name
        if item.is_symlink() or not item.is_file():
            raise SystemExit('The archive is missing a required regular file: ' + name)
        shutil.copyfile(item, target / name)
    for item in sorted((source / 'src').rglob('*')):
        if item.is_symlink():
            raise SystemExit('Symlinks are not allowed in the source.')
        if item.is_file() and item.suffix == '.py':
            destination = target / item.relative_to(source)
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copyfile(item, destination)
except BaseException:
    shutil.rmtree(target)
    raise
print(target)
PY
)"
printf '\nInstalling a separate version: %s\n' "$INSTALL_DIR"
/usr/bin/env -i HOME="$SAFE_USER_HOME" PATH="$SAFE_EXEC_PATH" LANG="$SAFE_LANG" \
  "$UV_BIN" sync --project "$INSTALL_DIR" --frozen --no-dev --python "$BOOTSTRAP_PYTHON"
MCP_PYTHON="$INSTALL_DIR/.venv/bin/python"
/usr/bin/env -i HOME="$SAFE_USER_HOME" PATH="$SAFE_EXEC_PATH" LANG="$SAFE_LANG" \
  "$MCP_PYTHON" -I - "$INSTALL_DIR" "$INSTALL_ROOT" <<'PY'
import pathlib, shlex, sys
from telegram_search_mcp.registration import launcher_arguments
version, root = map(pathlib.Path, sys.argv[1:])
python = str(version / '.venv/bin/python')
for name, module in [('tgsearch', 'telegram_search_mcp.cli'), ('client-config', 'telegram_search_mcp.registration')]:
    wrapper = version / name
    wrapper.write_text('#!/bin/sh\nexec ' + shlex.join(['/usr/bin/env', *launcher_arguments(python, module)]) + ' "$@"\n')
    wrapper.chmod(0o700)
(version / '.telegram-search-install').write_text('telegram-search-mcp\n')
PY
registration_args=(register --clients "$CLIENTS" --python "$MCP_PYTHON")
[[ -z "$CODEX_CONFIG" ]] || registration_args+=(--codex-config "$CODEX_CONFIG")
[[ -z "$GEMINI_CONFIG" ]] || registration_args+=(--gemini-config "$GEMINI_CONFIG")
[[ "$REPLACE_LEGACY" == false ]] || registration_args+=(--replace-legacy)
"$INSTALL_DIR/client-config" "${registration_args[@]}"
# Change only our stable helper link after both client registrations succeed.
/usr/bin/env -i HOME="$SAFE_USER_HOME" PATH="$SAFE_EXEC_PATH" "$MCP_PYTHON" -I - "$INSTALL_ROOT" "$INSTALL_DIR" <<'PY'
import os, pathlib, sys, uuid
root, version = map(pathlib.Path, sys.argv[1:])
current = root / 'current'
if current.exists() and not current.is_symlink():
    raise SystemExit('current already exists and is not a symlink; it has been left unchanged.')
if current.is_symlink():
    previous = current.resolve()
    if previous.parent != root or not (previous / '.telegram-search-install').is_file():
        raise SystemExit('current points to unrelated files; it has been left unchanged.')
temporary = root / ('.current-' + uuid.uuid4().hex)
temporary.symlink_to(version.name, target_is_directory=True)
os.replace(temporary, current)
PY
printf '\nDone. Clients: %s. Client availability and connectivity have not been checked.\n' "$CLIENTS"
printf 'Management command: %s/current/tgsearch\n' "$INSTALL_ROOT"
printf 'Diagnostics: %q/current/tgsearch doctor\n' "$INSTALL_ROOT"
if [[ "$PREPARE_ONLY" == true ]]; then
  printf '\nAuthorization was skipped. Run this in your own private Terminal:\n  %q/current/tgsearch auth\n' "$INSTALL_ROOT"
  printf 'After signing in: %q/current/tgsearch doctor --connect\n' "$INSTALL_ROOT"
else
  printf '\nSign in to YOUR OWN Telegram account. Use your own api_id and api_hash from https://my.telegram.org.\n'
  if "$INSTALL_DIR/tgsearch" doctor >/dev/null 2>&1; then
    printf "The existing shared profile is ready; checking the connection.\n"
  else
    "$INSTALL_DIR/tgsearch" auth
  fi
  "$INSTALL_DIR/tgsearch" doctor --connect
fi
printf '\nRestart Codex / Gemini CLI. Only search, message, context, and media tools are available.\n'
