# LUKS disk encryption for the Pi 5 SSD

Adds defence-in-depth: even with the AES-GCM column encryption of face
embeddings, the rest of the database (positional history, identity
display names, fusion event log) is plaintext SQLite. LUKS protects
those at rest if the SSD is stolen.

## Approach

The Pi 5 boots from a small unencrypted boot partition; the application
data partition (containing `/opt/tracking-system`,
`/var/log/tracking-engine`, `/var/backups/tracking-engine`, and the
SQLite gallery) lives on a LUKS-encrypted partition unlocked
automatically at boot via a key file stored on a small USB key the
household keeps physically separate.

This is a **physical-tamper** threat model — it does not protect
against a remote attacker who already has root on the running Pi.

## One-time setup

These commands wipe the target partition; back up first.

```bash
sudo apt install -y cryptsetup

# Identify the partition (e.g. /dev/nvme0n1p2).
DEV=/dev/nvme0n1p2

# Format + open.
sudo cryptsetup luksFormat --type luks2 "${DEV}"
sudo cryptsetup open "${DEV}" tracking-data
sudo mkfs.ext4 -L tracking-data /dev/mapper/tracking-data

# Mount and lay out application directories.
sudo mkdir -p /mnt/tracking-data
sudo mount /dev/mapper/tracking-data /mnt/tracking-data
sudo install -d -m 0755 -o tracking -g tracking /mnt/tracking-data/{opt,var-log,var-backups}
sudo mv /opt/tracking-system/* /mnt/tracking-data/opt/ 2>/dev/null || true
sudo rmdir /opt/tracking-system 2>/dev/null || true
sudo ln -s /mnt/tracking-data/opt /opt/tracking-system
```

## Boot-time auto-unlock via USB key

1. Insert a small USB key. Identify its mount label (e.g. `BOOTKEY`).
2. Generate a 32-byte random key and add it as a LUKS slot:
   ```bash
   sudo dd if=/dev/urandom of=/media/BOOTKEY/tracking.luks bs=32 count=1
   sudo chmod 0400 /media/BOOTKEY/tracking.luks
   sudo cryptsetup luksAddKey "${DEV}" /media/BOOTKEY/tracking.luks
   ```
3. Add a `/etc/crypttab` row:
   ```text
   tracking-data UUID=<part-uuid> /media/BOOTKEY/tracking.luks luks,nofail,discard
   ```
4. Add `/etc/fstab`:
   ```text
   /dev/mapper/tracking-data /mnt/tracking-data ext4 defaults,nofail 0 2
   ```
5. Test with `sudo systemctl daemon-reload && sudo reboot`.

If the USB key is removed, the application data partition fails to
mount and the tracking service is automatically held by systemd's
dependency on `/opt/tracking-system`. The Pi keeps booting; the
operator is notified by a fall-event heartbeat metric going stale.

## Operational notes

- The USB key is a **second** factor. Store it physically separate from
  the Pi (e.g. on the homeowner's keychain). A spare copy in a sealed
  envelope in a different room is a good idea.
- Rotate the LUKS key once a year (`cryptsetup luksChangeKey`).
- Backups via `deploy/scripts/backup_gallery.sh` should target a
  destination **inside** `/mnt/tracking-data` so they inherit the same
  encryption guarantee, or an encrypted off-Pi NAS over SSH.
