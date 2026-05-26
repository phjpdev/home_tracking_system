"""Multi-camera homography calibration web tool.

Browser-based calibration where the operator stands at a position, clicks
that position once on the floor plan, then clicks their feet in every
camera tile that sees them. The same shared world point pins every
camera's homography to the same coordinate system by construction —
unlike :mod:`tools.calibrate_homography`, which fits each camera in
isolation.

Run with::

    uvicorn tracking_engine.calibrate_web.app:app --host 0.0.0.0 --port 8090

Then open ``http://<pi-or-laptop>:8090`` on a phone or laptop.

Writes the same ``camera_calibrations.json`` schema the runtime already
consumes via :mod:`tracking_engine.pipeline.homography`, so no runtime
changes are required.
"""
