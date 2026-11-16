#!/bin/bash
# henwen-ami-setup
# Verifies and optionally fixes the Asterisk AMI configuration for HenWen.
# Run as root: sudo bash ami-setup.sh
#
# This script:
#   1. Shows the current manager.conf
#   2. Tests the AMI connection directly using Python (no app needed)
#   3. Creates HenWen's own AMI user, [henwen], if it is missing
#   4. Updates the HenWen service file to match
#   5. Reloads Asterisk manager module
#
# HenWen used to take the first user in manager.conf, which on ASL3 is
# [admin]. The ASL Web UI's Asterisk > Settings > AMI Secret rewrites exactly
# [admin] and updates only Allmon3, so HenWen now has a user of its own and
# never touches [admin] (docs/ASL-WEB-UI-PLAN.md, hazard 1).

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=apache-common.sh
. "$SCRIPT_DIR/apache-common.sh"

MANAGER_CONF="/etc/asterisk/manager.conf"
HENWEN_AMI_USER="henwen"
# HenWen 2.0 installs as HenWen2; 1.x as HenWen. HENWEN_SERVICE overrides.
if [ -n "${HENWEN_SERVICE:-}" ]; then
    SERVICE_NAME="$HENWEN_SERVICE"
elif [ -f /etc/systemd/system/HenWen2.service ]; then
    SERVICE_NAME="HenWen2"
else
    SERVICE_NAME="HenWen"
fi
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
AMI_PORT=5038
AMI_HOST="127.0.0.1"

# Colors
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

echo ""
echo "============================================"
echo "  HenWen AMI Setup and Verification"
echo "============================================"
echo ""

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}ERROR: Please run as root: sudo bash ami-setup.sh${NC}"
    exit 1
fi

# ── Check Asterisk is running ─────────────────────────────────────────────────
echo -e "${CYAN}[1/5] Checking Asterisk status...${NC}"
if systemctl is-active --quiet asterisk; then
    echo -e "      ${GREEN}Asterisk is running.${NC}"
else
    echo -e "      ${RED}Asterisk is NOT running!${NC}"
    echo "      Start it first: sudo systemctl start asterisk"
    exit 1
fi

# ── Show current manager.conf ─────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[2/5] Current manager.conf contents:${NC}"
if [ -f "$MANAGER_CONF" ]; then
    echo "---"
    # Secrets are masked: this output ends up in terminals, install logs
    # and pasted bug reports.
    sed -E 's/^([[:space:]]*secret[[:space:]]*=[[:space:]]*)[^;[:space:]]+/\1(hidden)/I' "$MANAGER_CONF"
    echo "---"
else
    echo -e "      ${RED}$MANAGER_CONF not found!${NC}"
    exit 1
fi

# ── Extract existing AMI user/secret from manager.conf ───────────────────────
echo ""
echo -e "${CYAN}[3/5] Parsing AMI credentials from manager.conf...${NC}"

# Use Python to parse it reliably
AMI_INFO=$(python3 - "$HENWEN_AMI_USER" <<'PYEOF'
import re, sys
want = sys.argv[1].lower()

conf = open('/etc/asterisk/manager.conf').read()

# Check enabled
m = re.search(r'^\s*enabled\s*=\s*(\S+)', conf, re.MULTILINE)
enabled = m.group(1).lower() if m else 'yes'
if enabled in ('no','false','0'):
    print("ENABLED=no")
    sys.exit(0)
print("ENABLED=yes")

# Extract port
m = re.search(r'^\s*port\s*=\s*(\d+)', conf, re.MULTILINE)
print("PORT=" + (m.group(1) if m else "5038"))

# HenWen's own user only. The secret is eval'd and sed'd into the unit
# file, so only a conservative character set is accepted; anything else is
# reported as unusable rather than guessed at.
current_header = None
secret = None
for line in conf.splitlines():
    line = line.strip()
    if not line or line.startswith(';'):
        continue
    hdr = re.match(r'^\[([^\]]+)\]', line)
    if hdr:
        current_header = hdr.group(1).strip().lower()
        continue
    if current_header == want and '=' in line:
        k = line.split('=',1)[0].strip().lower()
        v = line.split('=',1)[1].split(';')[0].strip()
        if k == 'secret' and re.fullmatch(r'[A-Za-z0-9_.@+-]+', v):
            secret = v
print("HAVE_STANZA=" + ("yes" if re.search(r'^\s*\[' + re.escape(want) + r'\]', conf, re.MULTILINE | re.IGNORECASE) else "no"))
if secret:
    print("SECRET='" + secret + "'")
PYEOF
)

eval "$AMI_INFO" 2>/dev/null || true

if [ "${ENABLED:-yes}" = "no" ]; then
    echo -e "  ${RED}ERROR: AMI is disabled in manager.conf (enabled = no)${NC}"
    echo "  Fix: set 'enabled = yes' in the [general] section"
    exit 1
fi

USER="$HENWEN_AMI_USER"
echo "  AMI port:  ${PORT:-5038}"
echo "  AMI user:  $USER"
if [ -n "$SECRET" ]; then echo "  AMI secret: (set)"; else echo "  AMI secret: (none yet)"; fi

if [ "${HAVE_STANZA:-no}" = "yes" ] && [ -z "$SECRET" ]; then
    echo -e "  ${RED}[$USER] exists in manager.conf but has no usable secret.${NC}"
    echo "  Give it a secret of letters, digits and _.@+- only, or delete the"
    echo "  stanza,"
    echo "  and re-run this script."
    exit 1
fi

if [ -z "$SECRET" ]; then
    # No prompt: install.sh runs this unattended, and the name is fixed.
    SECRET=$(python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(32)))")
    BACKUP="${MANAGER_CONF}.henwen-$(date +%Y%m%d-%H%M%S)"
    cp -p "$MANAGER_CONF" "$BACKUP"
    echo ""
    echo "  Adding HenWen's own AMI user [$USER] to manager.conf"
    echo "  (backup: $BACKUP). [admin] and any other user are left alone."
    cat >> "$MANAGER_CONF" << AMIEOF

; HenWen's own AMI user, added by HenWen's ami-setup.sh. Kept separate from
; [admin], whose secret the ASL Web UI and Allmon3 manage.
[$USER]
secret = $SECRET
read = system,call,log,verbose,command,agent,user,config,dtmf,reporting,cdr,dialplan
write = system,call,log,verbose,command,agent,user,config,dtmf,reporting,cdr,dialplan
deny = 0.0.0.0/0.0.0.0
permit = 127.0.0.1/255.255.255.255
AMIEOF
    echo -e "  ${GREEN}Added AMI user '$USER'.${NC}"
    asterisk -rx "module reload manager" >/dev/null 2>&1 || true
    sleep 1
fi

# ── Test AMI connection directly ──────────────────────────────────────────────
echo ""
echo -e "${CYAN}[4/5] Testing AMI login for user '$USER'...${NC}"

TEST_RESULT=$(python3 - "$USER" "$SECRET" "${PORT:-5038}" <<'PYEOF'
import socket, time, sys

user   = sys.argv[1]
secret = sys.argv[2]
port   = int(sys.argv[3])

try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(6)
    s.connect(('127.0.0.1', port))

    # Read banner
    buf = b''
    deadline = time.time() + 4
    while time.time() < deadline:
        s.settimeout(0.5)
        try:
            c = s.recv(256)
            if c: buf += c
            if b'\r\n' in buf: break
        except socket.timeout:
            continue
    banner = buf.decode('utf-8','replace').strip()
    print("BANNER=" + banner)

    # Send login
    s.settimeout(6)
    login = (f"Action: Login\r\nUsername: {user}\r\nSecret: {secret}\r\nEvents: off\r\n\r\n")
    s.sendall(login.encode('utf-8'))

    # Read response
    resp = b''
    deadline = time.time() + 6
    s.settimeout(0.5)
    while time.time() < deadline:
        try:
            c = s.recv(1024)
            if c: resp += c
            if b'\r\n\r\n' in resp: break
        except socket.timeout:
            continue
    s.close()

    resp_str = resp.decode('utf-8','replace')
    if 'Response: Success' in resp_str:
        print("RESULT=success")
    elif 'Authentication failed' in resp_str or 'Response: Error' in resp_str:
        print("RESULT=authfail")
        # Extract message
        for line in resp_str.splitlines():
            if line.startswith('Message:'):
                print("MESSAGE=" + line.split(':',1)[1].strip())
                break
    else:
        print("RESULT=unknown")
        print("RAW=" + repr(resp_str[:200]))
except Exception as e:
    print("RESULT=error")
    print("ERROR=" + str(e))
PYEOF
)

eval "$TEST_RESULT" 2>/dev/null || true

echo "  Banner:  ${BANNER:-(none)}"

case "${RESULT}" in
    success)
        echo -e "  ${GREEN}AMI login SUCCESSFUL for user '$USER'${NC}"
        ;;
    authfail)
        echo -e "  ${RED}AMI login FAILED: Authentication rejected${NC}"
        echo "  Message: ${MESSAGE:-unknown}"
        echo ""
        echo "  This means the secret in manager.conf does not match what"
        echo "  Asterisk has loaded. Asterisk may not have reloaded manager.conf"
        echo "  since it was last edited."
        echo ""
        echo "  Reloading Asterisk manager module now..."
        asterisk -rx "module reload manager" && echo -e "  ${GREEN}Manager reloaded.${NC}" || true
        sleep 1
        echo "  Re-testing..."
        # Quick re-test
        RETEST=$(python3 -c "
import socket,time
s=socket.socket(); s.settimeout(5); s.connect(('127.0.0.1',${PORT:-5038}))
buf=b''
dl=time.time()+3
while time.time()<dl:
    s.settimeout(0.3)
    try:
        c=s.recv(256)
        if c: buf+=c
        if b'\r\n' in buf: break
    except: continue
s.settimeout(5)
s.sendall(b'Action: Login\r\nUsername: ${USER}\r\nSecret: ${SECRET}\r\nEvents: off\r\n\r\n')
resp=b''
dl=time.time()+5
s.settimeout(0.3)
while time.time()<dl:
    try:
        c=s.recv(1024)
        if c: resp+=c
        if b'\r\n\r\n' in resp: break
    except: continue
s.close()
print('ok' if b'Success' in resp else 'fail')
" 2>/dev/null || echo "error")
        if [ "$RETEST" = "ok" ]; then
            echo -e "  ${GREEN}Re-test PASSED after reload.${NC}"
            RESULT=success
        else
            echo -e "  ${RED}Still failing after reload.${NC}"
            echo "  Check: sudo asterisk -rx 'manager show users'"
        fi
        ;;
    error)
        echo -e "  ${RED}Socket error: ${ERROR}${NC}"
        echo "  Is Asterisk running and manager.conf enabled=yes?"
        exit 1
        ;;
    *)
        echo -e "  ${YELLOW}Unexpected response: ${RAW}${NC}"
        ;;
esac

# Only a login that worked is written into the unit file. Switching a
# running service to credentials Asterisk just refused would take away the
# AMI connection it has now.
if [ "${RESULT}" != "success" ]; then
    echo ""
    echo -e "  ${RED}Not updating $SERVICE_NAME: the AMI login for '$USER' did not succeed.${NC}"
    echo "  Its service file is unchanged and it keeps the credentials it has."
    echo "  Check: sudo asterisk -rx 'manager show user $USER'"
    echo "  then re-run this script."
    exit 1
fi

# ── Update service file ───────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[5/5] Updating the $SERVICE_NAME service file...${NC}"

if [ ! -f "$SERVICE_FILE" ]; then
    echo "  Service file not found at $SERVICE_FILE"
    echo "  Run install.sh first, then re-run this script."
else
    # Update AMI_USER and AMI_SECRET in the service file
    sed -i "s|^Environment=\"AMI_USER=.*\"|Environment=\"AMI_USER=${USER}\"|" "$SERVICE_FILE"
    sed -i "s|^Environment=\"AMI_SECRET=.*\"|Environment=\"AMI_SECRET=${SECRET}\"|" "$SERVICE_FILE"

    # Verify the lines exist; add them if not
    if ! grep -q "^Environment=\"AMI_USER=" "$SERVICE_FILE"; then
        sed -i "/\[Service\]/a Environment=\"AMI_USER=${USER}\"" "$SERVICE_FILE"
    fi
    if ! grep -q "^Environment=\"AMI_SECRET=" "$SERVICE_FILE"; then
        sed -i "/AMI_USER/a Environment=\"AMI_SECRET=${SECRET}\"" "$SERVICE_FILE"
    fi

    systemctl daemon-reload

    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        systemctl restart "$SERVICE_NAME"
        sleep 1
        if systemctl is-active --quiet "$SERVICE_NAME"; then
            echo -e "  ${GREEN}$SERVICE_NAME service updated and restarted successfully.${NC}"
        else
            echo -e "  ${YELLOW}Service restart may have failed. Check: journalctl -u $SERVICE_NAME -n 20${NC}"
        fi
    else
        echo "  $SERVICE_NAME service not currently running. Start with: sudo systemctl start $SERVICE_NAME"
    fi
fi

# ── Also reload manager module to be sure ────────────────────────────────────
echo ""
echo "  Reloading Asterisk manager module..."
asterisk -rx "module reload manager" 2>/dev/null && echo -e "  ${GREEN}Manager reloaded.${NC}" || echo "  (reload skipped - may not be needed)"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "============================================"
if [ "${RESULT}" = "success" ]; then
    echo -e "  ${GREEN}AMI setup complete and verified!${NC}"
else
    echo -e "  ${YELLOW}AMI setup complete. Verify in the web UI.${NC}"
fi
echo ""
echo "  AMI User:   $USER"
echo "  AMI Secret: (hidden; it is in $MANAGER_CONF under [$USER] and in $SERVICE_FILE)"
echo "  AMI Port:   ${PORT:-5038}"
echo ""
echo "  To verify in the web UI:"
# This script is also run standalone, long after install.sh, so it can't
# rely on any shell variable install.sh may have computed -- re-detect the
# right URL the same way install.sh's own final banner does: prefer HTTPS
# (setup-https.sh's marker files) over a plain HenWen Apache vhost over the
# direct :5000 port, since :5000 bypasses Apache and with it the
# low-latency RX audio path's /ws-audio proxy. "A HenWen Apache vhost" is
# discovered the same way apache-common.sh's henwen_discover_vhosts() does
# for every other script here -- not a hardcoded henwen.conf/henwen-ssl.conf
# check, which would silently fall through to the :5000 branch on a box
# whose vhost has any other name.
IP=$(hostname -I | awk '{print $1}')
if [ -f /etc/asterisk/henwen-https-hostname ]; then
    WEBUI_HOSTNAME=$(cat /etc/asterisk/henwen-https-hostname 2>/dev/null || true)
    WEBUI_HTTPS_PORT=$(cat /etc/asterisk/henwen-https-port 2>/dev/null || true)
    WEBUI_PORT_SUFFIX=""
    [ -n "$WEBUI_HTTPS_PORT" ] && [ "$WEBUI_HTTPS_PORT" != "443" ] && WEBUI_PORT_SUFFIX=":${WEBUI_HTTPS_PORT}"
    echo "    https://${WEBUI_HOSTNAME}${WEBUI_PORT_SUFFIX}"
elif [ -n "$(henwen_discover_vhosts "$(henwen_flask_port)" 2>/dev/null | head -1)" ]; then
    echo "    http://${IP}/"
else
    echo "    http://${IP}:5000"
fi
echo "    -> AMI Diagnostics -> Run Test"
echo ""
echo "  To check logs:"
echo "    journalctl -u $SERVICE_NAME -f"
echo "============================================"
