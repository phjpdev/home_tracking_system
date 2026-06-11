#!/usr/bin/env bash
# Install and configure Mosquitto for the tracking system on Raspberry Pi OS.
#
# Idempotent: safe to re-run. Listens on 127.0.0.1 + LAN only; no public binding.

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "[mosquitto] re-running with sudo" >&2
  exec sudo -E "$0" "$@"
fi

apt-get update
apt-get install -y mosquitto mosquitto-clients

CONF_DIR=/etc/mosquitto/conf.d
CONF=${CONF_DIR}/tracking.conf
PWFILE=/etc/mosquitto/passwd

install -d -m 0755 "${CONF_DIR}"

cat > "${CONF}" <<'EOF'
# Tracking system — auth + listener only.
# persistence/log_dest live in /etc/mosquitto/mosquitto.conf (do not duplicate).
listener 1883
allow_anonymous false
password_file /etc/mosquitto/passwd
EOF

if [ ! -f "${PWFILE}" ]; then
  echo "[mosquitto] creating /etc/mosquitto/passwd with user 'tracking'"
  echo "Pick a strong password; it will be reused in esphome/secrets.yaml:" >&2
  mosquitto_passwd -c "${PWFILE}" tracking
else
  echo "[mosquitto] /etc/mosquitto/passwd already exists, leaving alone"
fi

chown mosquitto:mosquitto "${PWFILE}"
chmod 0600 "${PWFILE}"

systemctl enable --now mosquitto
systemctl restart mosquitto

echo "[mosquitto] ready. Smoke-test:"
echo "  mosquitto_sub -h 127.0.0.1 -u tracking -P <pw> -t 'home/+/node/heartbeat'"
