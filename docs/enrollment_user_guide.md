# Enrollment user guide

For the homeowner / operator. How to give the tracking system a name to
attach to each resident.

> All face data stays on the Raspberry Pi 5 in this house. Nothing leaves
> your network. Embeddings (numerical fingerprints) are stored encrypted
> at rest; original face images are never saved.

## What you need

- The Raspberry Pi 5 is powered on and connected to the LAN.
- The `tracking-engine` service is running (`systemctl status tracking-engine`).
- You know the name of at least one camera you can stand in front of
  (e.g. `cam_kwz_sw`). Camera names are listed in
  [../camera_placement_plan/output/cameras_config.json](../camera_placement_plan/output/cameras_config.json).
- The person being enrolled is **present** and has **explicitly agreed**
  to having their face fingerprint recorded.

## Option 1 — Browser (recommended for non-technical users)

1. On a phone or laptop on the same Wi-Fi, open
   `http://<pi-ip>:8088`. The Pi's address is usually displayed on the
   router.
2. Fill in the form:
   - **Display name** — what the smart home will use ("Jean Patrick").
   - **Camera** — e.g. `cam_kwz_sw`.
   - **Frames to capture** — 5 is plenty.
   - **Consent basis** — keep `consent` unless the lawyer says otherwise.
   - **Retention days** — leave empty (indefinite, until revoked).
3. Press **Start enrollment** and stand in front of the chosen camera
   for 10–20 seconds, looking gently left/right.
4. The page will show the resulting `identity_id` and append a row to
   the **Existing identities** table.

To remove someone later, press **Delete** in that table. Deletion is
**permanent** and cascades to every face / position / sighting record
the system holds for that person.

## Option 2 — Command line (for the integrator)

```bash
python -m tracking_engine.enroll \\
    --config tracking_engine/config.multi_camera.yaml \\
    --camera cam_kwz_sw \\
    --name "Jean Patrick" \\
    --frames 5 \\
    --consent-basis consent
```

The CLI prints the new `identity_id` on success. Repeat for each person
in the household.

## What happens behind the scenes

1. The tool opens the chosen RTSP feed.
2. It detects the largest person bounding box per frame.
3. SCRFD finds a frontal face inside the head region.
4. ArcFace turns the face into a 128-D number vector.
5. After 5 vectors are captured, the system:
   - Creates an `identity_id` (UUID).
   - Stores all 5 vectors encrypted with AES-256-GCM in `reid_gallery.db`.
   - Stores a `consent_record` row with the lawful basis and timestamp.
6. The next time the engine sees that face on any camera, the POST
   payload to the smart-home controller (Maro) includes
   `"identity_name": "Jean Patrick"`.

## Subject-rights actions

| Request | Tool | Effect |
|---------|------|--------|
| Show me what you have | `python -m tracking_engine.tools.list_identities` | Prints identity_id, name, consent state, face count |
| Withdraw consent | UI **Revoke** button, or `python -m tracking_engine.tools.revoke_consent --id <uuid>` | Sets `revoked_at`; nightly job purges embeddings |
| Erase everything | UI **Delete** button, or `python -m tracking_engine.tools.delete_identity --id <uuid>` | Removes face + appearance + sighting rows immediately |

## When something goes wrong

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| "face stack unavailable" | ONNX models not yet exported | See [../tracking_engine/models/README.md](../tracking_engine/models/README.md) |
| "no qualifying face crops collected" | Not enough light, glare on glasses, person too far | Stand 1–2 m from the camera; remove glasses; turn lights on |
| Enrollment looks fine but the name does not appear in the payload | Body Re-ID still in tentative state | Walk around for ~20 s so the body track is confirmed first |
