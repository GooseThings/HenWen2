#!/bin/bash
# Undo asl-web-ui/apply.sh: restore the HenWen vhost(s) it backed up (most
# recent run by default, or pass a backup dir) and re-enable the package's
# global conf if apply.sh was the one that disabled it.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../apache-common.sh
. "$HERE/../apache-common.sh"
[ "$(id -u)" = 0 ] || { echo "Run as root (sudo)"; exit 1; }

BACKUP_DIR="${1:-$(cat /root/henwen-asl-web-ui-last-backup 2>/dev/null || true)}"
[ -n "$BACKUP_DIR" ] && [ -d "$BACKUP_DIR" ] || { echo "Backup dir not found: '$BACKUP_DIR'"; exit 1; }

RESTORED=()
mapfile -t RESTORED < <(henwen_restore_vhosts_from_manifest "$BACKUP_DIR" 2>/dev/null || true)
[ "${#RESTORED[@]}" -gt 0 ] || { echo "No backed-up vhost file found in $BACKUP_DIR"; exit 1; }
echo "== Restored:"
printf '     %s\n' "${RESTORED[@]}"

if [ -e "$BACKUP_DIR/global-conf-was-enabled" ]; then
  a2enconf -q asl-web-ui && echo "== Re-enabled the global asl-web-ui conf"
fi

apache2ctl configtest 2>&1 | grep -q "Syntax OK" || { echo "Restored config failed configtest. Investigate before reloading."; exit 1; }
henwen_apache_reload_or_start
echo "Done."
