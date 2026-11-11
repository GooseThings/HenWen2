#!/bin/bash
# HenWen + ASL Web UI: serve /asl-web-ui/ on HenWen's own vhost(s) only.
#
# The asl-web-ui package runs "a2enconf asl-web-ui", which serves the UI at
# /asl-web-ui/ on EVERY Apache virtual host -- on a server that also hosts
# unrelated sites, the node's admin UI appears on all of them. And on
# HenWen's own vhost, HenWen's catch-all "ProxyPass / http://127.0.0.1:<port>/"
# sends /asl-web-ui/ to Flask instead (docs/ASL-WEB-UI-PLAN.md, hazard 2).
#
# This script, for every HenWen vhost apache-common.sh finds:
#   - inserts, just before HenWen's catch-all ProxyPass:
#       IncludeOptional /etc/apache2/conf-available/asl-web-ui.conf
#       ProxyPass /asl-web-ui !
#     The include brings the UI's Alias, its ProxyPass for /asl-web-ui/api/
#     (which therefore matches first) and its security headers; the "!"
#     line keeps the rest of /asl-web-ui out of HenWen's proxy so the Alias
#     serves it. IncludeOptional makes this safe before the package is
#     installed: a missing file is skipped.
#   - disables the package's global conf ("a2disconf asl-web-ui") if it is
#     enabled. A2disconf run by an admin is recorded, and Debian's
#     apache2-maintscript-helper does not re-enable it on package upgrades.
#
# Backups of every touched vhost go to /root/henwen-asl-web-ui-backup-<stamp>;
# rollback.sh restores them and re-enables the global conf if this disabled
# it. Marker-guarded, safe to re-run. Does not touch Asterisk or HenWen.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../apache-common.sh
. "$HERE/../apache-common.sh"
MARKER="HenWen: ASL Web UI"
CONF_NAME="asl-web-ui"
CONF_AVAILABLE="${HENWEN_APACHE_CONF_AVAILABLE:-/etc/apache2/conf-available}/${CONF_NAME}.conf"
CONF_ENABLED="${HENWEN_APACHE_CONF_ENABLED:-/etc/apache2/conf-enabled}/${CONF_NAME}.conf"
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="/root/henwen-asl-web-ui-backup-$STAMP"

[ "$(id -u)" = 0 ] || { echo "Run as root (sudo)"; exit 1; }

FLASK_PORT="$(henwen_flask_port)"
mapfile -t APACHE_CONFS < <(henwen_discover_vhosts "$FLASK_PORT")
if [ "${#APACHE_CONFS[@]}" -eq 0 ]; then
  echo "ERROR: no HenWen Apache vhost found"
  echo "  (scanned $(henwen_apache_sites_dir)/*.conf for a ProxyPass to 127.0.0.1:${FLASK_PORT}/)."
  echo "Nothing to scope the ASL Web UI to. Set up HTTPS first:"
  echo "  sudo bash $HERE/../tx-spike/setup-https.sh <hostname> <email>"
  exit 1
fi
echo "== Found ${#APACHE_CONFS[@]} HenWen Apache vhost(s):"
printf '     %s\n' "${APACHE_CONFS[@]}"

echo "== Backing up to $BACKUP_DIR"
henwen_backup_vhosts "$BACKUP_DIR" "${APACHE_CONFS[@]}"
echo "$BACKUP_DIR" > /root/henwen-asl-web-ui-last-backup

INSERT_TEXT="    # ${MARKER}. Serves AllStarLink's web UI here only (its
    # package conf is disabled globally). Must stay above HenWen's own
    # catch-all ProxyPass. Added by HenWen's asl-web-ui/apply.sh.
    IncludeOptional ${CONF_AVAILABLE}
    ProxyPass /asl-web-ui !
"
echo "== Vhosts"
for conf in "${APACHE_CONFS[@]}"; do
  echo "   $conf"
  rc=0
  henwen_insert_before_proxypass "$conf" "$FLASK_PORT" "$MARKER" "$INSERT_TEXT" || rc=$?
  case "$rc" in
    0) echo "      patched" ;;
    1) echo "      already patched, skipping" ;;
    *) echo "      FAILED to find HenWen's catch-all ProxyPass. Restoring every vhost touched this run."
       henwen_restore_vhosts_from_manifest "$BACKUP_DIR" >/dev/null || true
       exit 1 ;;
  esac
  chmod 644 "$conf"
done

echo "== Global $CONF_NAME conf"
if [ -e "$CONF_ENABLED" ]; then
  a2disconf -q "$CONF_NAME"
  touch "$BACKUP_DIR/global-conf-was-enabled"
  echo "   disabled (it served /asl-web-ui/ on every virtual host)"
elif [ -e "$CONF_AVAILABLE" ]; then
  echo "   already disabled"
else
  echo "   asl-web-ui is not installed yet. When it is, its install enables the"
  echo "   global conf: run this script again afterwards."
fi

if ! apache2ctl configtest 2>&1 | grep -q "Syntax OK"; then
  echo "   Apache configtest FAILED. Restoring every vhost touched this run."
  henwen_restore_vhosts_from_manifest "$BACKUP_DIR" >/dev/null || true
  [ -e "$BACKUP_DIR/global-conf-was-enabled" ] && a2enconf -q "$CONF_NAME" || true
  exit 1
fi
henwen_apache_reload_or_start || exit 1

echo
echo "Done. The ASL Web UI is served at https://<HenWen hostname>/asl-web-ui/"
echo "on the vhost(s) above and on no other site."
echo "Rollback:  sudo bash $HERE/rollback.sh"
