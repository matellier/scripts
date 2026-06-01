#!/usr/bin/env bash
# List the files contained in a snapshot (long format).
#   Usage: ./list-files.sh [snapshot-id] [path-prefix ...]
# Defaults to the latest snapshot and the whole tree. Examples:
#   ./list-files.sh                                   # latest, everything
#   ./list-files.sh latest /home/mtellier/Documents   # latest, just Documents
#   ./list-files.sh a1b2c3d4                          # a specific snapshot
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_lib.sh"
require_root "$@"

SNAP="${1:-latest}"
shift || true   # remaining args (if any) are path prefixes to narrow the listing

mount_repo
restic ls -l "${SNAP}" "$@"
