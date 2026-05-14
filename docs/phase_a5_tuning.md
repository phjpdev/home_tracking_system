# Phase A.5 — OSNet Re-ID threshold tuning notes

Template. Fill in once the ONNX model is exported and a representative
multi-person clip exists. See [plan/MASTER_PLAN.md](../plan/MASTER_PLAN.md)
section 5 for the full procedure.

## Test footage used

| Camera | Clip | Duration | People in frame |
|--------|------|----------|-----------------|
| TBD | TBD | TBD | TBD |

## Pairwise cosine-distance summary

Generated via `python tools/export_osnet_onnx.py --self-test --sample-dir tools/sample_crops`.

| Person A | Person B | min | median | max |
|----------|----------|----:|-------:|----:|
| TBD | TBD | | | |

Intra-person target: median < 0.2.
Inter-person target: median > 0.4.

## Chosen thresholds

```yaml
reid:
  body:
    threshold_match: 0.35   # adjusted: ____
    threshold_high:  0.20   # adjusted: ____
    ema_alpha:       0.3
    tentative_to_confirmed_frames: 15
    min_bbox_area:   4000
    min_blur_var:    50
```

## Two-person handover test

| Action | Expected | Observed |
|--------|----------|----------|
| Person 1 enters cam_kwz_sw | new global_id assigned | TBD |
| Person 1 walks to cam_kwz_ne FOV | same global_id appears on cam_kwz_ne POST | TBD |
| Person 2 enters cam_kwz_sw alongside Person 1 | second global_id assigned | TBD |
| Both leave cam_kwz_sw, return after 30 s | same two global_id values re-attached | TBD |

## Notes / surprises

(record anything weird here — e.g. mirror reflections triggering false detections,
clothing similarity causing merges, lighting changes between cameras)
