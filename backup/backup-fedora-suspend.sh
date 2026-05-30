#!/usr/bin/env bash
# Suspend the PC after a successful nightly backup.
# Runs as ExecStartPost of backup-fedora.service. Suspends only if:
#   - SUSPEND_AFTER=1 (set in the service), and
#   - no graphical session is actively in use (so it won't sleep on you if you're up).
set -euo pipefail

[ "${SUSPEND_AFTER:-0}" = "1" ] || exit 0

for s in $(loginctl list-sessions --no-legend 2>/dev/null | awk '{print $1}'); do
  typ=$(loginctl show-session "$s" -p Type --value 2>/dev/null || true)
  case "$typ" in
    wayland|x11)
      idle=$(loginctl show-session "$s" -p IdleHint --value 2>/dev/null || echo yes)
      if [ "$idle" = "no" ]; then
        logger -t backup-fedora "session $s active -> not suspending"
        exit 0
      fi
      ;;
  esac
done

logger -t backup-fedora "backup complete, session idle -> suspending"
systemctl suspend
