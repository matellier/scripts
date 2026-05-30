# Fedora nightly backup → Synology NAS

Restic-based nightly backup of the home directory to a Synology NAS over NFS,
driven by a systemd timer that **wakes the PC from sleep** at 02:00 local time.

This git repo is the **source of truth**. System copies are *deployed* (not
symlinked) because SELinux forbids systemd from executing files under `/home`
(they carry the `user_home_t` label).

## Files

| File | Deployed to | Purpose |
|------|-------------|---------|
| `backup-fedora.sh` | `/usr/local/bin/` | Mounts NFS (retries, unmounts on exit), runs `restic backup` → `forget --prune` → `check`. |
| `backup-fedora-suspend.sh` | `/usr/local/bin/` | After a successful backup, suspends the PC again **if** `SUSPEND_AFTER=1` and no graphical session is in use. |
| `backup-fedora.service` | `/etc/systemd/system/` | oneshot; wraps the backup in `systemd-inhibit --what=sleep`; runs the suspend helper afterward. |
| `backup-fedora.timer` | `/etc/systemd/system/` | `OnCalendar=02:00`, `WakeSystem=true` (RTC wake from suspend), `Persistent=true`. |
| `deploy.sh` | — | Installs all of the above with correct perms + `restorecon`, then enables the timer. |

## How it works

- **02:00 local** the timer fires. `WakeSystem=true` arms the RTC to wake the
  machine from suspend-to-RAM. (`Persistent=true` runs a missed backup at next
  boot if the PC was fully **powered off** — RTC wake can't help from poweroff.)
- The service holds a sleep inhibitor for the whole backup so KDE/logind can't
  re-suspend mid-run. The script mounts the NAS only for the backup window and
  unmounts on exit (a NAS hang can never block the OS).
- On success, `backup-fedora-suspend.sh` returns the machine to sleep — unless
  you're actively using it. Set `Environment=SUSPEND_AFTER=0` in the service to
  disable.

## Prerequisites (one-time, e.g. after a fresh Fedora install)

1. **restic**: `sudo dnf install -y restic`
2. **Repo password file** (root-only):
   ```bash
   sudo install -d -m 700 /etc/restic
   printf '%s' 'YOUR-RESTIC-REPO-PASSWORD' | sudo tee /etc/restic/fedora.pass >/dev/null
   sudo chmod 600 /etc/restic/fedora.pass
   ```
3. **NAS reachable**: NFS export `192.168.50.104:/volume1/fedora_nfs` (edit the
   `NFS_*` vars in `backup-fedora.sh` if these change).
4. **Initialize the repo once** (only if it doesn't already exist on the NAS):
   ```bash
   sudo mount -t nfs 192.168.50.104:/volume1/fedora_nfs /mnt/nas-backup
   sudo RESTIC_PASSWORD_FILE=/etc/restic/fedora.pass \
     restic -r /mnt/nas-backup/fedora-mtellier-restic init
   sudo umount /mnt/nas-backup
   ```

## Deploy

```bash
sudo ./deploy.sh
```

## Verify

```bash
systemctl list-timers backup-fedora.timer        # shows next 02:00 run
sudo systemctl start backup-fedora.service        # run now
journalctl -u backup-fedora.service -n 40 --no-pager
sudo RESTIC_PASSWORD_FILE=/etc/restic/fedora.pass \
  restic -r /mnt/nas-backup/fedora-mtellier-restic snapshots --compact   # NAS must be mounted
```

### Test the wake-from-sleep (optional)

Temporarily set the timer a few minutes out, suspend, and confirm it wakes:

```bash
sudo systemctl edit backup-fedora.timer   # add [Timer] OnCalendar= (clear) then OnCalendar=<now+3min>
systemctl suspend
# ...machine should wake ~3 min later and run the backup; then revert the override:
sudo systemctl revert backup-fedora.timer
```

## Notes

- Retention is set in `backup-fedora.sh` (`KEEP_DAILY/WEEKLY/MONTHLY`).
- Sources backed up: `~/.ssh`, `~/Documents`, `~/Pictures`, `~/Videos`.
- History: replaced an earlier attempt to drive backups via *backrest*, which
  doesn't mount the NAS and was silently writing the repo to local disk. Backrest
  was removed; this systemd + restic path is the single source of truth.
