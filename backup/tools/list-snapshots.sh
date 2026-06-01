#!/usr/bin/env bash
# List all backups (snapshots) in the repo, newest last.
# Any extra args are passed straight to `restic snapshots`, e.g.:
#   ./list-snapshots.sh --tag nightly
#   ./list-snapshots.sh --json
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_lib.sh"
require_root "$@"

mount_repo
restic snapshots "$@"
