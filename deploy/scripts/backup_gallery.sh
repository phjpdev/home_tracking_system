#!/usr/bin/env bash
# Nightly gallery backup. Designed for cron:
#   30 3 * * *  /opt/tracking-system/deploy/scripts/backup_gallery.sh
#
# Snapshots reid_gallery.db (via the SQLite VACUUM INTO API so WAL is
# included consistently) and copies the matching FAISS file. Keeps the
# last 14 daily snapshots; older directories are pruned.

set -euo pipefail

GALLERY_DIR=${GALLERY_DIR:-/opt/tracking-system/tracking_engine}
BACKUP_ROOT=${BACKUP_ROOT:-/var/backups/tracking-engine}
KEEP_DAYS=${KEEP_DAYS:-14}

DB=${GALLERY_DIR}/reid_gallery.db
FAISS=${GALLERY_DIR}/reid_gallery.faiss

if [ ! -f "${DB}" ]; then
    echo "[backup] no gallery DB at ${DB}; nothing to back up" >&2
    exit 0
fi

today=$(date -u +%Y-%m-%d)
target_dir=${BACKUP_ROOT}/${today}
install -d -m 0700 "${target_dir}"

snapshot="${target_dir}/reid_gallery.db"
sqlite3 "${DB}" "VACUUM INTO '${snapshot}'"

if [ -f "${FAISS}" ]; then
    cp -p "${FAISS}" "${target_dir}/"
fi

# Drop schema dump for forensic inspection.
sqlite3 "${DB}" ".schema" > "${target_dir}/schema.sql"

# Prune older snapshots.
if [ -d "${BACKUP_ROOT}" ]; then
    find "${BACKUP_ROOT}" -maxdepth 1 -type d -name '20*' -mtime "+${KEEP_DAYS}" -print -exec rm -rf {} +
fi

echo "[backup] wrote ${target_dir}"
