#!/bin/bash
# HenWen 2.0 - Automated updater
#
# Launched via `sudo systemd-run --unit=henwen-updater --collect
# /opt/HenWen2/scripts/update.sh` from the Manager's "Launch Updater" button
# (superuser only, see /api/update/launch in app.py). Runs as its own
# transient systemd unit, deliberately outside HenWen2.service's cgroup,
# so the `systemctl restart HenWen2` at the end doesn't get killed along
# with the very service it's restarting.
#
# All three settings below are 2.0's. Changing only REPO_URL would be
# destructive: INSTALL_DIR would still be /opt/HenWen, the stopped 1.x
# rollback checkout, and the hard reset below would replace it with 2.0.
#
# Fetches over plain HTTPS from the public repo rather than the
# SSH-based `origin` remote (git@github.com:...) — this script runs as
# root, which may have no SSH key/agent configured, and the repo is
# public, so there's nothing to authenticate.
set -e

REPO_URL="https://github.com/GooseThings/HenWen2.git"
INSTALL_DIR="/opt/HenWen2"
SERVICE_NAME="HenWen2"
TAG="[HENWEN-UPDATE]"

cd "$INSTALL_DIR"

if [ ! -d .git ]; then
    echo "$TAG ERROR: $INSTALL_DIR is not a git checkout — cannot self-update." >&2
    exit 1
fi

BEFORE=$(git rev-parse HEAD)
echo "$TAG Current commit: $BEFORE"
echo "$TAG Fetching latest main from $REPO_URL..."
git fetch --quiet "$REPO_URL" main
AFTER=$(git rev-parse FETCH_HEAD)

if [ "$BEFORE" = "$AFTER" ]; then
    echo "$TAG Already up to date ($BEFORE)."
    exit 0
fi

echo "$TAG Updating $BEFORE -> $AFTER"
git reset --hard FETCH_HEAD

echo "$TAG Installing dependencies..."
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

echo "$TAG Verifying app.py..."
if ! "$INSTALL_DIR/venv/bin/python3" -m py_compile "$INSTALL_DIR/app.py"; then
    echo "$TAG ERROR: app.py failed to compile after update — rolling back to $BEFORE." >&2
    git reset --hard "$BEFORE"
    exit 1
fi

echo "$TAG Refreshing sudoers rule for restart/reload/update actions..."
INSTALL_DIR="$INSTALL_DIR" SERVICE_NAME="$SERVICE_NAME" bash "$INSTALL_DIR/scripts/provision-sudoers.sh"

echo "$TAG Reloading systemd and restarting $SERVICE_NAME..."
systemctl daemon-reload
systemctl restart "$SERVICE_NAME"

# The SIP WebSocket relay is its own unit (setup/browser-tx/apply.sh), so a
# HenWen restart doesn't drop browser calls. Restart it only when its code
# changed; that drops calls once, and the kiosk redials within seconds.
if ! git diff --quiet "$BEFORE" "$AFTER" -- henwen/sip_ws_relay.py; then
    echo "$TAG SIP WebSocket relay changed; restarting henwen-sip-ws..."
    systemctl try-restart henwen-sip-ws || true
fi

echo "$TAG Update complete: now on $AFTER"
