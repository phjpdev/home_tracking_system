#!/usr/bin/env bash
# Nightly GDPR enforcement job:
#   * Hard-delete every identity whose consent was revoked.
#   * Drop appearance_embedding + sighting rows older than RETENTION_DAYS.
#
# Cron:
#   45 3 * * *  /opt/tracking-system/deploy/scripts/purge_revoked.sh

set -euo pipefail

RETENTION_DAYS=${RETENTION_DAYS:-90}
VENV=${VENV:-/opt/tracking-system/.venv/bin/python}
CONFIG=${CONFIG:-/opt/tracking-system/tracking_engine/config.multi_camera.yaml}

"${VENV}" -m tracking_engine.tools.purge_retention \
    --config "${CONFIG}" \
    --retention-days "${RETENTION_DAYS}"
