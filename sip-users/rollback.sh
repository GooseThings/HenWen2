#!/bin/bash
# Undo sip-users/apply.sh: remove the two #tryinclude lines (and the marker
# comment above each) from pjsip.conf / extensions.conf and reload. The
# generated files under /etc/asterisk/custom/ are left in place unless
# --purge is given, since they are harmless once nothing includes them and
# HenWen rewrites them on the next account change anyway.
set -euo pipefail

MARKER="HenWen per-user SIP"
PJSIP_CONF="${PJSIP_CONF:-/etc/asterisk/pjsip.conf}"
EXTENSIONS_CONF="${EXTENSIONS_CONF:-/etc/asterisk/extensions.conf}"
CUSTOM_DIR="${CUSTOM_DIR:-/etc/asterisk/custom}"
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

[ "$(id -u)" = 0 ] || { echo "Run as root (sudo)"; exit 1; }

strip_include() {   # strip_include <file> <include-file-basename>
  local file="$1" base="$2"
  [ -f "$file" ] || return 0
  if ! grep -qF -- "custom/$base" "$file"; then
    echo "== $file: nothing to remove"
    return 0
  fi
  cp -a "$file" "$file.pre-sipusers-rollback"
  # Drop the tryinclude line and the two marker comment lines apply.sh wrote.
  sed -i -e "/^; $MARKER: /d" \
         -e '/^; (see sip-users\/README.md). Remove with sip-users\/rollback.sh.$/d' \
         -e "/^#tryinclude \"custom\/$base\"$/d" "$file"
  echo "== $file: include removed (backup: $file.pre-sipusers-rollback)"
}
strip_include "$PJSIP_CONF" "henwen-sip-users.conf"
strip_include "$EXTENSIONS_CONF" "henwen-sip-users-dialplan.conf"

if [ "$PURGE" = 1 ]; then
  rm -f "$CUSTOM_DIR/henwen-sip-users.conf" "$CUSTOM_DIR/henwen-sip-users-dialplan.conf"
  echo "== Generated files removed"
fi

echo "== Reloading PJSIP and the dialplan"
asterisk -rx "module reload res_pjsip.so" || true
asterisk -rx "dialplan reload" || true
echo "== Done"
