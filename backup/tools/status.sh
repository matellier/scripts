#!/usr/bin/env bash
# Backup health at a glance: systemd service result, next scheduled run, and the
# most recent snapshot actually in the repo.
#   Usage: ./status.sh
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_lib.sh"
require_root "$@"

echo "── service (last run) ──────────────────────────────────────"
systemctl status backup-fedora.service --no-pager -n 6 || true
echo
echo "── timer (next scheduled run) ──────────────────────────────"
systemctl list-timers backup-fedora.timer --no-pager || true
echo
echo "── latest snapshot in repo ─────────────────────────────────"
mount_repo
restic snapshots --latest 1
