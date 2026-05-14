# GDPR documentation

Authoritative privacy/compliance artefacts for the deployment.

| File | Purpose |
|------|---------|
| [dpia.md](dpia.md) | Data Protection Impact Assessment (Article 35) — sign before enabling face recognition. |
| [consent_form.md](consent_form.md) | Printable consent form for each enrolled resident (Article 9(2)(a)). |
| [entrance_signage.md](entrance_signage.md) | Article 13 transparency sign (EN / DE / FR). |
| [luks_setup.md](luks_setup.md) | Full-disk encryption recipe for the Pi 5 SSD. |

Operational tooling that backs these documents:

- AES-GCM column encryption of face embeddings:
  [../../tracking_engine/reid/crypto.py](../../tracking_engine/reid/crypto.py).
  Generate the key with
  `python -m tracking_engine.reid.crypto --generate /etc/tracking-engine/secret.key`.
- Subject rights:
  - List: [../../tracking_engine/tools/list_identities.py](../../tracking_engine/tools/list_identities.py)
  - Revoke: [../../tracking_engine/tools/revoke_consent.py](../../tracking_engine/tools/revoke_consent.py)
  - Erase: [../../tracking_engine/tools/delete_identity.py](../../tracking_engine/tools/delete_identity.py)
  - Nightly purge of revoked / expired data:
    [../../tracking_engine/tools/purge_retention.py](../../tracking_engine/tools/purge_retention.py)
    (run via [../../deploy/scripts/purge_revoked.sh](../../deploy/scripts/purge_revoked.sh))
