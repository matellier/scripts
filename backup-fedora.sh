#!/bin/bash
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
NFS_SERVER="192.168.50.104"
NFS_EXPORT="/volume1/fedora_nfs"
MOUNT_POINT="/mnt/nas-backup"
BACKUP_USER="mtellier"

export RESTIC_REPOSITORY="${MOUNT_POINT}/restic-backup"
export RESTIC_PASSWORD_FILE="/etc/restic/fedora.pass"

# Service runs as root with no $HOME — give restic a persistent metadata cache
# so each run isn't a full cold read (and to silence the cache-dir warning).
export RESTIC_CACHE_DIR="/var/cache/restic"

SOURCES=(
    "/home/${BACKUP_USER}/.ssh"
    "/home/${BACKUP_USER}/Documents"
    "/home/${BACKUP_USER}/Pictures"
    "/home/${BACKUP_USER}/Videos"
)

# Retention: keep this many of each, prune the rest.
KEEP_DAILY=14
KEEP_WEEKLY=8
KEEP_MONTHLY=12

# ── Mount ─────────────────────────────────────────────────────────────────────
mkdir -p "${MOUNT_POINT}" "${RESTIC_CACHE_DIR}"

# Unmount on exit (success or error) — repo only mounted during the backup window,
# so a NAS reboot can never hang the OS.
trap 'umount "${MOUNT_POINT}" 2>/dev/null || true' EXIT

if mountpoint -q "${MOUNT_POINT}"; then
    echo "WARNING: ${MOUNT_POINT} already mounted — reusing"
else
    for i in 1 2 3 4 5; do
        mount -t nfs "${NFS_SERVER}:${NFS_EXPORT}" "${MOUNT_POINT}" && break
        echo "Mount attempt $i failed — retrying in 30s..."
        sleep 30
    done
    mountpoint -q "${MOUNT_POINT}" || { echo "ERROR: NFS mount failed after 5 attempts"; exit 1; }
fi

# ── Sanity: repo must exist (run `restic init` once during setup) ──────────────
if ! restic cat config >/dev/null 2>&1; then
    echo "ERROR: restic repo not found at ${RESTIC_REPOSITORY} — run 'restic init' first"
    exit 1
fi

# ── Backup ────────────────────────────────────────────────────────────────────
restic backup \
    --verbose \
    --tag nightly \
    --exclude-caches \
    "${SOURCES[@]}"

# ── Prune old snapshots ───────────────────────────────────────────────────────
restic forget \
    --tag nightly \
    --keep-daily   "${KEEP_DAILY}" \
    --keep-weekly  "${KEEP_WEEKLY}" \
    --keep-monthly "${KEEP_MONTHLY}" \
    --prune

# ── Integrity check (structure + metadata) ────────────────────────────────────
restic check

echo "Backup complete: ${RESTIC_REPOSITORY}"
