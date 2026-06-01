#!/usr/bin/env bash
# Shared config + NFS mount helpers for the manual restic backup tools.
# Sourced by the other scripts in this folder — not meant to be run directly.
#
# Values here MUST match /usr/local/bin/backup-fedora.sh (the nightly job).

NFS_SERVER="192.168.50.104"
NFS_EXPORT="/volume1/fedora_nfs"
MOUNT_POINT="/mnt/nas-backup"
BACKUP_USER="mtellier"

export RESTIC_REPOSITORY="${MOUNT_POINT}/fedora-${BACKUP_USER}-restic"
export RESTIC_PASSWORD_FILE="/etc/restic/fedora.pass"
export RESTIC_CACHE_DIR="/var/cache/restic"

# These tools need root: the NFS mount, the repo on the NAS, and the password
# file at /etc/restic/fedora.pass are all root-owned. Re-exec under sudo if not.
require_root() {
    if [ "${EUID:-$(id -u)}" -ne 0 ]; then
        echo "→ re-running under sudo (NFS mount + root-owned repo)…" >&2
        exec sudo -- "$0" "$@"
    fi
}

# Mount the NAS only if it isn't already, and unmount on exit only if WE mounted
# it (so we never yank a mount the nightly job or you set up). Mirrors the
# nightly script: 5 retries, then give up.
_WE_MOUNTED=0
mount_repo() {
    mkdir -p "${MOUNT_POINT}" "${RESTIC_CACHE_DIR}"
    if mountpoint -q "${MOUNT_POINT}"; then
        return 0
    fi
    local i
    for i in 1 2 3 4 5; do
        mount -t nfs "${NFS_SERVER}:${NFS_EXPORT}" "${MOUNT_POINT}" && { _WE_MOUNTED=1; return 0; }
        echo "mount attempt $i failed — retrying in 5s…" >&2
        sleep 5
    done
    echo "ERROR: could not mount ${NFS_SERVER}:${NFS_EXPORT}" >&2
    exit 1
}
_unmount_repo() {
    [ "${_WE_MOUNTED}" -eq 1 ] && umount "${MOUNT_POINT}" 2>/dev/null || true
}
trap _unmount_repo EXIT
