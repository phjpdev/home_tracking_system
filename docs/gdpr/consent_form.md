# Consent to process facial biometric data

Print this form, sign in pen, and store one signed copy alongside the
DPIA. The system records the act of consent (timestamp + lawful basis)
in `consent_record`; this paper form is the auditable artefact.

---

## Subject

Full name: \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_

Relationship to household: \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_

Date of birth (only required for minors): \_\_\_\_-\_\_-\_\_

For minors, parental / guardian signature is required in addition to
(or in place of) the subject's signature.

## What you are agreeing to

The household indoor tracking system uses cameras in the kitchen,
living room, yoga room, and hallway. When you enrol, the system will:

1. Take 5 short head-only crops from a chosen camera.
2. Convert each crop to a 128-number "fingerprint" via an on-device AI
   model (ArcFace MobileFaceNet, 128-D).
3. Discard the crops immediately. **Your face image is never saved.**
4. Store the 5 fingerprints **encrypted (AES-256-GCM)** on the home's
   Raspberry Pi 5 only.
5. Use the fingerprints to recognise you across the cameras, so the
   smart-home can attach your name to a position and trigger your
   personal automations.

The bedroom and bathroom contain **no optical cameras**. They use a
low-resolution thermal sensor (32 x 24 pixels) that cannot identify
you and is used only to detect falls.

## What stays in your control

- Withdraw consent at any time — the operator runs
  `python -m tracking_engine.tools.revoke_consent --id <your-id>` and
  your fingerprints are purged within 24 hours.
- Request hard deletion at any time —
  `python -m tracking_engine.tools.delete_identity --id <your-id>`
  removes the fingerprints and all linked sightings immediately.
- Ask to see what is stored — `python -m tracking_engine.tools.list_identities`.
- No data leaves the home network. There is no cloud, no third party.

## Lawful basis (GDPR)

Article 9(2)(a) — **explicit consent** for processing biometric data
that uniquely identifies you. Article 6(1)(f) covers the anonymous
positional data not tied to your identity.

## Acknowledgement

I confirm that:

- I have read the above and understand what data is collected and why.
- I agree to the processing of my facial fingerprints for the stated
  purpose.
- I understand I can withdraw my consent at any time without giving a
  reason.

Subject signature: \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_

Date: \_\_\_\_-\_\_-\_\_

(For minors) Parent / guardian signature: \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_

(For minors) Date: \_\_\_\_-\_\_-\_\_

Witness (operator/integrator): \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_

System identity_id assigned: \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_

Retention: indefinite, until consent withdrawal or deletion request.
