#!/usr/bin/env bash
# One-shot Raspberry Pi 5 installer for the tracking system.
# Assumes Raspberry Pi OS 64-bit Bookworm or later.
#
# Usage (any of these works, regardless of execute-bit on the script):
#   sudo bash deploy/scripts/install.sh                  # from repo root
#   sudo bash /home/pi/tracking/deploy/scripts/install.sh
#   APP_DIR=/home/pi/tracking sudo -E bash deploy/scripts/install.sh

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    exec sudo -E bash "$0" "$@"
fi

# Self-locate the repo root from the script's own path so the installer is
# safe to run from anywhere (``cd`` into the wrong dir is a frequent footgun).
SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(dirname "${SCRIPT_PATH}")"
REPO_ROOT="$(readlink -f "${SCRIPT_DIR}/../..")"

APP_DIR=${APP_DIR:-/opt/tracking-system}
SRC_DIR=${SRC_DIR:-${REPO_ROOT}}

echo "[install] source repo: ${SRC_DIR}"
echo "[install] target:      ${APP_DIR}"

if [ ! -f "${SRC_DIR}/tracking_engine/requirements.txt" ]; then
    echo "[install] sanity check failed: ${SRC_DIR}/tracking_engine/requirements.txt missing" >&2
    echo "[install] either run from the repo root, or set SRC_DIR=/path/to/repo" >&2
    exit 2
fi

echo "[install] installing system packages"
apt-get update
apt-get install -y python3 python3-venv python3-pip git rsync mosquitto mosquitto-clients

echo "[install] creating service user"
if ! id tracking >/dev/null 2>&1; then
    useradd --system --create-home --shell /usr/sbin/nologin tracking
fi

install -d -m 0755 -o tracking -g tracking "${APP_DIR}"
install -d -m 0750 -o tracking -g tracking /var/log/tracking-engine
install -d -m 0700 -o tracking -g tracking /var/backups/tracking-engine
install -d -m 0750 -o root     -g tracking /etc/tracking-engine

if [ "${SRC_DIR}" != "${APP_DIR}" ]; then
    echo "[install] copying source to ${APP_DIR}"
    rsync -a --delete --exclude='.venv' --exclude='__pycache__' --exclude='.git' \
        "${SRC_DIR}/" "${APP_DIR}/"
    chown -R tracking:tracking "${APP_DIR}"
fi

# Restore +x bits that git-on-Windows often drops.
chmod +x "${APP_DIR}/deploy/scripts/"*.sh 2>/dev/null || true

if [ ! -d "${APP_DIR}/.venv" ]; then
    echo "[install] creating venv"
    sudo -u tracking python3 -m venv "${APP_DIR}/.venv"
fi

echo "[install] installing python dependencies"
sudo -u tracking "${APP_DIR}/.venv/bin/pip" install --upgrade pip
sudo -u tracking "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/tracking_engine/requirements.txt"

echo "[install] installing systemd units"
install -m 0644 "${APP_DIR}/deploy/systemd/tracking-engine.service"      /etc/systemd/system/
install -m 0644 "${APP_DIR}/deploy/systemd/tracking-thermal.service"     /etc/systemd/system/
install -m 0644 "${APP_DIR}/deploy/systemd/tracking-enroll-web.service"  /etc/systemd/system/
systemctl daemon-reload

echo "[install] installing cron jobs"
cat > /etc/cron.d/tracking-system <<EOF
30 3 * * * tracking ${APP_DIR}/deploy/scripts/backup_gallery.sh
45 3 * * * tracking ${APP_DIR}/deploy/scripts/purge_revoked.sh
EOF
chmod 0644 /etc/cron.d/tracking-system

echo "[install] enabling mosquitto"
bash "${APP_DIR}/deploy/scripts/install_mosquitto.sh"

echo
echo "[install] DONE."
echo "Next steps:"
echo "  1. Calibrate cameras (see docs/production_camera_only_runbook.md §3)."
echo "  2. (Phase D) Generate the face-embedding key:"
echo "     sudo -u tracking ${APP_DIR}/.venv/bin/python -m tracking_engine.reid.crypto --generate /etc/tracking-engine/secret.key"
echo "  3. Edit ${APP_DIR}/tracking_engine/config.multi_camera.yaml as needed."
echo "  4. systemctl enable --now tracking-engine"
echo "     (leave tracking-thermal and tracking-enroll-web disabled until phases B/D)"
