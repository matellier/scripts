#!/bin/bash
# Deploy the maintained backup script + systemd units to system paths.
# Run with sudo. This git repo is the source of truth; system copies are
# deployed here (NOT symlinked — SELinux forbids a systemd service from
# executing files under /home, which carry the user_home_t label).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Script: root-owned copy in a bin_t path. root:root means a non-root user
# cannot alter code that runs as root. restorecon guarantees the bin_t label.
install -m 755 -o root -g root "${REPO_DIR}/backup-fedora.sh" /usr/local/bin/backup-fedora.sh
restorecon -v /usr/local/bin/backup-fedora.sh

# systemd units
install -m 644 -o root -g root "${REPO_DIR}/backup-fedora.service" /etc/systemd/system/backup-fedora.service
install -m 644 -o root -g root "${REPO_DIR}/backup-fedora.timer"   /etc/systemd/system/backup-fedora.timer

systemctl daemon-reload
systemctl enable --now backup-fedora.timer

echo "Deployed. Test with: sudo systemctl start backup-fedora.service"
