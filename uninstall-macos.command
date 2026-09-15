#!/bin/bash
set -euo pipefail
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd -P)"
INSTALL_DIR=""
CLIENT_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      cat <<'EOF'
Отключить Telegram Search MCP от клиентов, сохранив историю и собственный вход:
  bash uninstall-macos.command --install-dir /absolute/path/to/version [options]
Если скрипт запущен из установленной версии, --install-dir не нужен.
  --clients codex|gemini|both    По умолчанию both.
  --codex-config PATH           Явный путь настроек Codex.
  --gemini-config PATH          Явный путь настроек Gemini.

Удаляются только распознанные записи этого общего пакета. Создаются резервные
копии настроек. Программа, профиль и Keychain сохраняются. После отключения
всех клиентов можно выполнить tgsearch service stop и удалить каталог программы.
Это не затрагивает другие установки и старые сессии Codex/Gemini 0.2/0.3.
EOF
      exit 0 ;;
    --install-dir)
      [[ $# -ge 2 ]] || exit 2
      INSTALL_DIR="$2"; shift ;;
    --clients|--codex-config|--gemini-config)
      [[ $# -ge 2 ]] || exit 2
      CLIENT_ARGS+=("$1" "$2"); shift ;;
    *) printf 'Неизвестный параметр: %s\n' "$1" >&2; exit 2 ;;
  esac
  shift
done
INSTALL_DIR="${INSTALL_DIR:-$SOURCE_DIR}"
[[ "$INSTALL_DIR" = /* && -f "$INSTALL_DIR/.telegram-search-install" && -x "$INSTALL_DIR/client-config" ]] || {
  printf 'Укажите установленную версию: --install-dir /absolute/path/to/version\n' >&2
  exit 1
}
"$INSTALL_DIR/client-config" unregister "${CLIENT_ARGS[@]}"
printf '\nПодключение отключено. Перезапустите выбранные клиенты.\n'
printf 'Файлы программы, собственная история и вход в Telegram сохранены.\n'
printf 'Когда все клиенты отключены, остановите общий сервис:\n  %q/tgsearch service stop\n' "$INSTALL_DIR"
