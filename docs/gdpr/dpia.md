# Data Protection Impact Assessment (DPIA)

System: home indoor multi-person tracking + privacy-zone fall detection.
Version: 1.0.
Date prepared: \_\_\_\_-\_\_-\_\_
Prepared by: \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_
Reviewed by household owner(s): \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_

This DPIA satisfies Article 35 GDPR for the use of biometric data
(facial recognition) inside a private dwelling. It must be completed and
signed by the household owner(s) **before** facial recognition
(`reid.face.enabled: true`) is switched on in production.

## 1. Purpose and necessity

The system enables context-aware home automation: lights, music, sockets
and HVAC respond to the actual position and identity of household
members. It also provides safety-critical fall detection in the bedroom
and bathroom where optical cameras are not permitted.

Without identity attribution the household cannot differentiate
preferences per person (e.g. "play my workout playlist when I enter
Yoga"). Body Re-ID alone is anonymous; facial recognition is required
**only** to assign a name to an otherwise-anonymous track.

## 2. Data categories processed

| Category | Where stored | Encrypted at rest | Lawful basis (Art. 6 / Art. 9) |
|----------|--------------|-------------------|---------------------------------|
| Person bounding boxes, floor-mm positions | RAM only; POSTed to Maro | n/a | Art. 6(1)(f) legitimate interest |
| Body Re-ID embeddings (512-D, anonymous) | `appearance_embedding` table | LUKS disk encryption | Art. 6(1)(f) legitimate interest |
| Face embeddings (128-D, identifying) | `face_embedding` table | AES-256-GCM column + LUKS disk | Art. 9(2)(a) explicit consent |
| Identity display name | `identity` table | LUKS disk encryption | Art. 9(2)(a) explicit consent |
| Consent record | `consent_record` table | LUKS disk encryption | Art. 6(1)(c) legal obligation |
| Thermal frames (32x24, non-imaging) | RAM only; not persisted | n/a | Art. 6(1)(f) legitimate interest |
| Fall event metadata (room, time, confidence) | RAM only; POSTed to Maro | n/a | Art. 6(1)(d) vital interests |
| Door open/closed, leak wet/dry | RAM only; POSTed to Maro | n/a | Art. 6(1)(f) legitimate interest |

Source images of faces are **never** persisted: the enrollment flow
captures crops in memory, computes the embedding, and discards the crop
before returning.

## 3. Recipients

| Recipient | Data | Channel |
|-----------|------|---------|
| Maro FastAPI smart-home controller (LAN) | Positions, identities, events | HTTP POST over LAN |
| Operator (household member) | All data, via CLI / web UI | local console |
| No third party | — | — |

No data leaves the LAN. Firewall rules drop egress to non-RFC1918
addresses.

## 4. Retention schedule

| Data | Default retention | Trigger to delete |
|------|-------------------|-------------------|
| Position POST payloads | not persisted | n/a |
| Body appearance embeddings (anonymous) | 90 days | nightly purge job |
| Face embeddings (enrolled) | indefinite | consent revocation, identity deletion |
| Consent records | until identity deletion | identity deletion |
| Sightings | 90 days | nightly purge job |
| Fusion event log | 365 days | manual review + prune |
| Backup snapshots | 14 days | rolling delete in backup script |

Implemented in
[../../deploy/scripts/purge_revoked.sh](../../deploy/scripts/purge_revoked.sh)
which invokes [../../tracking_engine/tools/purge_retention.py](../../tracking_engine/tools/purge_retention.py).

## 5. Technical and organisational measures

### Technical

- AES-256-GCM column-level encryption of every `face_embedding` row,
  key file `/etc/tracking-engine/secret.key`, mode 0640
  (`root:tracking`).
- LUKS full-disk encryption of the Pi 5 SSD recommended; document
  enabling step in [../deployment_guide.md](../deployment_guide.md).
- Mosquitto bound to LAN, password authenticated.
- Service runs as unprivileged `tracking` user with `NoNewPrivileges`,
  `ProtectSystem=full`, `ProtectHome=true`, `PrivateTmp`.
- Hard-delete cascade on `DELETE FROM identity` via SQLite
  `ON DELETE CASCADE` on `face_embedding` and `consent_record`.
- Manual `python -m tracking_engine.tools.delete_identity` is the
  one-liner for an erasure request.

### Organisational

- Consent collected and signed before any face is enrolled
  ([consent_form.md](consent_form.md)).
- A printed sign at the entrance of the dwelling lists which rooms have
  optical cameras and which do not
  ([entrance_signage.md](entrance_signage.md)).
- Bedroom and bathroom are physically free of optical cameras.
- Quarterly review of `list_identities`; revoke consent for anyone no
  longer in the household.

## 6. Risk assessment

| Risk | Likelihood | Impact | Mitigation | Residual |
|------|-----------:|-------:|------------|----------|
| Face embedding theft via local file copy | Low | High | AES-GCM column + LUKS disk + 0640 key | Low |
| Wrong-name fusion | Low | Medium | Probabilistic fusion + `conflict_deferred` + audit log | Low |
| Inadvertent face capture of a visitor | Medium | Medium | No enrollment without explicit consent; anonymous tracks auto-expire in 90 d | Low |
| Network exfiltration | Very low | Critical | Egress firewall, no cloud, no telemetry | Very low |
| Operator misuse (delete after dispute) | Low | Medium | Backup snapshots kept for 14 days | Low |
| Camera RTSP captured externally | Low | High | Cameras on isolated VLAN; OpenIPC firmware after smoke test | Low |
| Power outage masks a fall event | Medium | High | Small UPS keeps Pi + switch + ESP32 alive ~30 minutes | Medium |

## 7. Consultation

- [ ] Household owner(s) reviewed and approved the processing.
- [ ] (Optional) Independent DPO / lawyer consulted: \_\_\_\_\_\_\_\_\_\_
- [ ] No formal supervisory authority consultation required (private
      household, no third-party data subjects in scope).

## 8. Sign-off

| Role | Name | Signature | Date |
|------|------|-----------|------|
| Data controller (household owner) | | | |
| Integrator | | | |
| (Optional) DPO | | | |

Re-review trigger: any change to data categories, retention defaults,
recipient list, or addition of new modalities (e.g. gait, voice).
