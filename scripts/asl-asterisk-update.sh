#!/bin/bash
# HenWen - AllStarLink package-list refresher (root helper)
#
# Run by the weekly Asterisk package check (and Manager > Asterisk Updates'
# "Check Now") through one narrowly-scoped NOPASSWD sudoers rule (see
# provision-sudoers.sh):
#
#   asl-asterisk-update.sh check     refresh apt's package lists (apt-get update)
#
# HenWen only reports what's available. Installing is done with AllStarLink's
# own tools -- Cockpit's Software Updates page, which the ASL Web UI's
# Diagnostics page links to, or apt -- since HenWen handed that job over
# (docs/ASL-WEB-UI-PLAN.md, decision 2). The old `install` mode, and the
# sudoers rule that ran it as root via systemd-run, are gone.
set -u

MODE="${1:-}"
export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C

case "$MODE" in
    check)
        echo "Refreshing package lists (apt-get update)..."
        apt-get update -o DPkg::Lock::Timeout=120
        exit $?
        ;;
    *)
        echo "usage: $0 check" >&2
        exit 2
        ;;
esac
