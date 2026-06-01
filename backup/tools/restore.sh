#!/usr/bin/env bash
# Restore files from a snapshot into a target directory.
#   Usage: ./restore.sh <snapshot-id|latest> <target-dir> [include-path]
#
# restic recreates the ORIGINAL absolute paths under <target-dir>, e.g. restoring
# to /tmp/restore yields /tmp/restore/home/mtellier/Documents/...  Restore to an
# empty/scratch dir and copy back what you need — never restore over live ~ blind.
#
# Examples:
#   ./restore.sh latest /tmp/restore                                  # everything
#   ./restore.sh latest /tmp/restore /home/mtellier/Documents/tax.pdf  # one file
#   ./restore.sh a1b2c3d4 /tmp/restore /home/mtellier/.ssh             # one tree
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_lib.sh"
require_root "$@"

SNAP="${1:-}"
TARGET="${2:-}"
INCLUDE="${3:-}"
if [ -z "${SNAP}" ] || [ -z "${TARGET}" ]; then
    echo "Usage: $0 <snapshot-id|latest> <target-dir> [include-path]" >&2
    exit 2
fi

mkdir -p "${TARGET}"
mount_repo
if [ -n "${INCLUDE}" ]; then
    restic restore "${SNAP}" --target "${TARGET}" --include "${INCLUDE}"
else
    restic restore "${SNAP}" --target "${TARGET}"
fi
echo "Restored ${SNAP} → ${TARGET}"
