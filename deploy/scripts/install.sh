#!/usr/bin/env bash
# One-shot Raspberry Pi 5 installer for the tracking system.
# Assumes Raspberry Pi OS 64-bit Bookworm or later.

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  exec sudo -E "$0" "$@"
fi

APP_DIR=${APP_DIR:-/opt/tracking-system}
SRC_DIR=${SRC_DIR:-$(pwd)}

echo "[install] installing system packages"
apt-get update
apt-get install -y python3 python3-venv python3-pip git mosquitto mosquitto-clients

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
    rsync -a --delete --exclude='.venv' --exclude='__pycache__' "${SRC_DIR}/" "${APP_DIR}/"
    chown -R tracking:tracking "${APP_DIR}"
fi

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
cat > /etc/cron.d/tracking-system <<'EOF'
30 3 * * * tracking /opt/tracking-system/deploy/scripts/backup_gallery.sh
45 3 * * * tracking /opt/tracking-system/deploy/scripts/purge_revoked.sh
EOF
chmod 0644 /etc/cron.d/tracking-system

echo "[install] enabling mosquitto"
"${APP_DIR}/deploy/scripts/install_mosquitto.sh"

echo
echo "[install] DONE."
echo "Next steps:"
echo "  1. Calibrate cameras (see deployment guide)."
echo "  2. Generate the face-embedding key: "
echo "     sudo -u tracking ${APP_DIR}/.venv/bin/python -m tracking_engine.reid.crypto --generate /etc/tracking-engine/secret.key"
echo "  3. Edit ${APP_DIR}/tracking_engine/config.multi_camera.yaml as needed."
echo "  4. systemctl enable --now tracking-engine tracking-thermal tracking-enroll-web"
