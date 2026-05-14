# Phase C — Thermal fall detection tuning notes

Template. Fill in once the privacy-zone hardware is live and the
`tracking_engine.thermal.run` service is running on the Pi.

## Scenarios to validate

For each scenario, capture: time, observed posture sequence, alarm emitted (yes/no, confidence, latency).

### BZ — bathroom

| Scenario | Expected | Latency budget |
|----------|----------|---------------:|
| Lie down on floor for 25 s, no leak | `medium` fall | ≤ 22 s |
| Lie down on floor for 25 s + wet leak probe | `high` fall | ≤ 22 s |
| Walk in, shower 10 min upright | no alarm | — |
| Sit on toilet for 5 min | no alarm | — |

### SZ — bedroom

| Scenario | Expected | Latency budget |
|----------|----------|---------------:|
| Rapid drop to floor outside rest zone (1 s), hold 30 s | `high` fall | ≤ 32 s |
| Sit on bed, slide to lie down (3 s+), hold 5 min | no alarm | — |
| Stand in middle of room, fall rapidly, hold 30 s | `high` fall | ≤ 32 s |
| Walk through to bed, sleep 8 h | no alarm | — |

## Default tunables (in `tracking_engine/config.multi_camera.yaml`)

```yaml
thermal:
  detection:
    event_throttle_sec: 30.0
    body_temp_min_c: 28.0
    body_temp_max_c: 38.5
    motion_threshold_normalized: 0.02
    background_alpha: 0.02
```

Authoritative fall thresholds live in
`camera_placement_plan/output/cameras_config.json::thermal_fall_detection`:

```
rapid_transition_max_s        : 1.0
slow_transition_min_s         : 3.0
sz_still_confirmation_s       : 30.0
bz_still_horizontal_s         : 20.0
motion_threshold_normalized   : 0.02
sz_suppress_if_slow_to_rest_zone : true
```

Update them in `camera_placement_plan/generate_camera_plan.py` and
regenerate `cameras_config.json` if a tuning pass changes them.

## False-positive log

Note every spurious alarm with: timestamp, what was happening physically, frame snapshot if available, and the action taken (raise threshold, add rest zone polygon, etc.).

| Time | Room | Reason | Action |
|------|------|--------|--------|
| | | | |

## False-negative log

Same idea — missed falls. These are the more dangerous class.

| Time | Room | Description | Action |
|------|------|-------------|--------|
| | | | |
