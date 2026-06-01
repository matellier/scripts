# backup/tools — manual restic helpers

Read-only/restore helpers for the nightly restic→NAS backup. The nightly job
itself lives one level up in `~/scripts/backup/` (units + `backup-fedora.sh` +
`deploy.sh`); these are for poking at the repo by hand.

All scripts self-elevate with `sudo` (the NFS mount, the NAS repo, and
`/etc/restic/fedora.pass` are root-owned) and mount/unmount the NAS on demand.
Shared config lives in `_lib.sh` and must match `backup-fedora.sh`.

| Script | Does |
|--------|------|
| `status.sh` | Service result + next scheduled run + latest snapshot |
| `list-snapshots.sh` | List all backups (passes extra args to `restic snapshots`) |
| `list-files.sh` | List files in a snapshot — `./list-files.sh [snap] [path]` |
| `restore.sh` | Restore — `./restore.sh <snap\|latest> <target-dir> [include-path]` |

Full reference: Obsidian note **fedora-backup**.
