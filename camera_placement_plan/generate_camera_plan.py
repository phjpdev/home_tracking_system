"""
Camera Placement Plan Generator — Phase 1.5
============================================

Generates an annotated floor plan image and a JSON config file for the
tracking engine, based on a list of camera specifications.

Hardware target:
    - Prototype: 6 × WiFi cameras (cams 1–6) for early integration.
    - Production-locked: OEM PoE IP camera board (HiSilicon-class SoC +
      Sony image sensor, 1080p+ H.264/H.265 RTSP, IRCUT, IR LED ring,
      M12 2.8 mm lens). Same lens spec across all 7 cameras + spares.
      See docs/FINAL_CAMERA_SELECTION.md for the exact SKU.

Building:        Floor-plan envelope 19.8 m x 10.2 m, ceiling 3.0 m.
                 Interior rooms occupy a horizontal strip
                 (y ~ 4500-8400 mm); the rest of the envelope is the
                 outdoor terrace / deck and is NOT tracked.

Hardware mounting constraints (reference floor plan / v1.2 review):
    - The long N and S walls of K/WZ + SZ + Yoga are continuous
      sliding-glass / patio doors. No mid-wall camera mounting.
    - The structural frame between glass panels at every interior
      CORNER is solid steel and is the only allowed wall fixing
      point. All cameras therefore mount on a ceiling drop bracket
      AT the interior corners.

Phase 1.5 placements (7 cameras):
    - K/WZ    (kitchen + living)  - 4 corner cameras (cams 1-4); each
                                    wedge clipped to the L-shaped K/WZ
                                    trackable polygon (fireplace blocker
                                    truncates the upper L-arm at
                                    x = FIREPLACE_X_MM).
    - Yoga                        - 2 cameras (cams 5-6) on the east
                                    end of Yoga, looking diagonally
                                    back across the room.
    - Hallway (behind Yoga)       - 1 camera (cam 7) looking west
                                    along the corridor, polygon-clipped.
                                    Added in v1.5 for hallway
                                    re-identification continuity.
    - SZ      (bedroom)           - NO camera. Privacy room. Fall
                                    detection is delegated to a
                                    non-RF, non-imaging sensor: a
                                    low-resolution thermal IR grid
                                    (MLX90640 32x24, 55 deg FOV, on an
                                    Olimex ESP32-POE-ISO running ESPHome).
                                    On-site mmWave trials (Aqara FP2,
                                    Apollo R1) failed here due to metal
                                    in the wall assembly
                                    scattering 24/60 GHz mmWave.
    - BZ      (bathroom)          - out of scope for cameras; fall
                                    detection = thermal IR grid +
                                    water-leak sensor (secondary).

FOV cones are clipped to each room's TRACKABLE POLYGON (authoritative
vertices on the reference floor plan; occlusion-aware).

USAGE
-----
    python3 generate_camera_plan.py

Outputs (written to ./output/):
    camera_placement_plan.png   — annotated floor plan image (cameras,
                                  thermal BZ/SZ footprints, and privacy
                                  hardware mount markers with mm coords)
    cameras_config.json         — machine-readable camera config +
                                  privacy_thermal_zones_mm,
                                  privacy_hardware_mounts_mm (SZ/BZ)

To adjust placements, edit the CAMERAS list below and re-run.

REQUIREMENTS
------------
    pip install matplotlib pillow numpy

The script expects ./floor_plan.png to be present alongside it.
"""

from pathlib import Path
import json

import numpy as np
from PIL import Image
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Wedge
from matplotlib.path import Path as MplPath
import matplotlib.patches as mpatches


# ============================================================
# 1. Building & coordinate-system constants
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
FLOOR_PLAN_PATH = SCRIPT_DIR / "floor_plan.png"
OUTPUT_DIR = SCRIPT_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# ----------------------------------------------------------------------
# Coordinate frame (origin = NW / top-left corner of the floor-plan
# envelope, +x east, +y south, +z up; units = millimetres).
#
# This is the SAME frame the dashboard uses to render dots — so the
# (x, y) values we emit per person POST go straight into the rendering
# without further transformation.
# ----------------------------------------------------------------------

# Full envelope of the floor plan (matches the rounded-rect outline in
# floor_plan.png — i.e. building shell + outdoor deck).
BLD_WIDTH_MM  = 19800
BLD_HEIGHT_MM = 10200

# Pixel coordinates of the OUTER ENVELOPE corners in floor_plan.png.
# (Estimated visually from the current image; will be re-derived
# automatically by step_pipeline/extract_floor_plan.py once the
# new STEP file lands.)
BLD_LEFT_PX   =  27
BLD_RIGHT_PX  = 997
BLD_TOP_PX    =  50
BLD_BOTTOM_PX = 485

# INTERIOR bounding box (the rectangular room strip), enclosing
# every trackable polygon defined below:
#   - Cameras MUST be inside this box. is_inside_interior() warns
#     if a placement falls outside.
#   - The trackable area itself is described per-room by the
#     polygons in TRACKABLE_AREAS_MM.
INTERIOR_X_MIN_MM =  1700
INTERIOR_X_MAX_MM = 17900
INTERIOR_Y_MIN_MM =  2500
# Must envelop the tallest thermal privacy footprints (BZ extends to y=9100).
INTERIOR_Y_MAX_MM =  9100

# Ceiling height above floor (+z); cameras + thermal privacy grids mount here.
CEILING_H_MM = 3000

# Per-room interior bounds (left-to-right), aligned with colour regions
# on the reference floor_plan.png.
# Estimates from pixel measurement until step_pipeline/extract_floor_plan.py
# regenerates them from STEP geometry.
# BZ/SZ rectangles envelope PRIVACY_THERMAL_ZONES_MM thermal footprints.
ROOM_BOUNDS_MM = {
    "K/WZ":    {"x": ( 1700,  6700), "y": (4500, 8400)},
    # Axis-aligned envelopes around thermal / privacy footprints (BZ, SZ).
    "BZ":      {"x": ( 6400, 10000), "y": (7100, 9100)},
    "SZ":      {"x": (10100, 14200), "y": (5000, 8000)},
    "Yoga":    {"x": (12100, 17800), "y": (4500, 8400)},
    "Hallway": {"x": (12000, 17900), "y": (2500, 4500)},
}

# ---------------------------------------------------------------------
# Trackable-area polygons (mm, envelope frame).
#
# Authoritative floor-plan polygons: each is the *physical* area that
# the cameras assigned to that room can actually see. Used as the clip
# mask for the FOV wedges in the
# rendered plan: inside the polygon the wedge fill is drawn fully;
# outside the polygon nothing is drawn.
#
# K/WZ trackable area is L-shaped because cams 3 and 4 sit at the
# top/east of K/WZ and can see well past the K/WZ-BZ wall into the
# upper portion of BZ + SZ; the wider arm reflects that real-world
# coverage, not just the K/WZ rectangle.
#
# Yoga is a simple rectangle slightly inset from the room walls.
#
# Polygon vertices are listed in order (closed automatically).
# ---------------------------------------------------------------------
# Optical blockers: solid objects between K/WZ and SZ that camera
# light cannot pass through. The fireplace sits roughly at
# x ≈ 8500 in the upper strip; from any K/WZ camera, anything east
# of it in the L-arm is occluded, so the L-arm of the K/WZ
# trackable polygon stops at the fireplace.
FIREPLACE_X_MM = 10000

TRACKABLE_AREAS_MM: dict[str, list[tuple[int, int]]] = {
    "K/WZ": [
        ( 2500,           9200),
        ( 2500,           5000),
        (FIREPLACE_X_MM,  5000),
        (FIREPLACE_X_MM,  7400),
        ( 6300,           7400),
        ( 6300,           9200),
    ],
    "Yoga": [
        (14100, 5300),
        (17900, 5300),
        (17900, 8800),
        (14100, 8800),
    ],
    # Hallway strip adjacent to Yoga (see `floor_plan.png`). Cam 7 fans
    # along the corridor axis; polygon clips FOV to the trackable strip.
    # Refine vertices after an as-built survey if the corridor differs.
    "Hallway": [
        (14000, 8700),
        (10100, 8700),
        (10100, 8000),
        (14000, 8000),
    ],
}

# ---------------------------------------------------------------------
# Privacy rooms (BZ, SZ): thermal IR fall-detection footprints (mm).
# Floor-plan polygons in the SAME envelope frame as trackable areas.
# No camera wedges here — rendered as filled overlays + exported to JSON
# for ``thermal_fall_detection.PrivacyThermalFallDetector``.
#
# SZ optional bed / couch rest polygon: set vertices when calibrated on
# site (null in JSON disables rest-only suppression cues).
# ---------------------------------------------------------------------
PRIVACY_THERMAL_ZONES_MM: dict[str, list[tuple[int, int]]] = {
    "BZ": [
        (6400, 7400),
        (10000, 7400),
        (10000, 8700),
        (8500, 8700),
        (8500, 9200),
        (6400, 9200),
    ],
    "SZ": [
        (10100, 5000),
        (14200, 5000),
        (14200, 8000),
        (10100, 8000),
    ],
}

SZ_REST_ZONE_POLYGON_MM: list[tuple[int, int]] | None = None


def _polygon_centroid_mm(vertices: list[tuple[int, int]]) -> tuple[float, float]:
    """Closed-polygon centroid in mm (matches thermal footprint polygons)."""
    xa = np.array([v[0] for v in vertices], dtype=np.float64)
    ya = np.array([v[1] for v in vertices], dtype=np.float64)
    xn = np.roll(xa, -1)
    yn = np.roll(ya, -1)
    cross = xa * yn - xn * ya
    area = float(np.sum(cross) * 0.5)
    if abs(area) < 1e-9:
        return float(np.mean(xa)), float(np.mean(ya))
    cx = float(np.sum((xa + xn) * cross) / (6.0 * area))
    cy = float(np.sum((ya + yn) * cross) / (6.0 * area))
    return cx, cy


def _rnd_mm(v: float) -> int:
    return int(round(v))


# ----------------------------------------------------------------------
# Privacy hardware: definitive mount coordinates (generated PNG + JSON).
# Thermal grids: CEILING-mounted, (x,y) = polygon centroid in plan view;
# z = CEILING_H_MM. Re-derived whenever PRIVACY_THERMAL_ZONES_MM changes.
# BZ water-leak sensor: floor, z = 0; edit after site survey (keep inside
# the BZ thermal polygon, typically shower egress / drainage path).
# ----------------------------------------------------------------------
_sz_cx, _sz_cy = _polygon_centroid_mm(PRIVACY_THERMAL_ZONES_MM["SZ"])
_bz_cx, _bz_cy = _polygon_centroid_mm(PRIVACY_THERMAL_ZONES_MM["BZ"])
PRIVACY_THERMAL_CEILING_MOUNT_MM: dict[str, dict[str, int]] = {
    "SZ": {
        "x_mm": _rnd_mm(_sz_cx),
        "y_mm": _rnd_mm(_sz_cy),
        "z_mm": CEILING_H_MM,
    },
    "BZ": {
        "x_mm": _rnd_mm(_bz_cx),
        "y_mm": _rnd_mm(_bz_cy),
        "z_mm": CEILING_H_MM,
    },
}

BZ_WATER_LEAK_FLOOR_MOUNT_MM = {
    "x_mm": 7450,
    "y_mm": 8900,
    "z_mm": 0,
    "wiring": "wired to BZ Olimex ESP32-POE-ISO GPIO (active-low pull-up)",
    "placement_note": (
        "Floor mount inside BZ thermal zone (L-tab). Adjust after site "
        "survey — target shower curb / wet drain path; used as secondary "
        "channel with ceiling thermal."
    ),
}

# Tunables mirrored into output/cameras_config.json ``thermal_fall_detection``.
THERMAL_FALL_DETECTION_EXPORT = {
    "rapid_transition_max_s":        1.0,
    "slow_transition_min_s":         3.0,
    "sz_still_confirmation_s":      30.0,
    "bz_still_horizontal_s":        20.0,
    "motion_threshold_normalized": 0.02,
    "sz_suppress_if_slow_to_rest_zone": True,
    # API hint for MQTT / ESPHome integration
    "event_schema_note": (
        "After confirmation, POST /events e.g. "
        '{ "type": "fall", "room": "SZ"|"BZ", "confidence":"high"|"medium", '
        '"ts": unix_f } ; include water_leak:true in BZ when Zigbee wet.'
    ),
}

# Walls that must NOT be used for camera mounting (mounting constraints):
#   - North wall of K/WZ + the K/WZ-BZ junction = continuous glazing.
#   - South walls of all rooms appear to be sliding glass too.
# All cameras are therefore CEILING-MOUNTED, away from any wall by
# at least ~1 m, dropped on a bracket/cable run.
GLASS_WALLS = (
    "north wall (K/WZ + BZ + SZ + Yoga continuous glazing)",
    "south sliding doors (K/WZ + SZ + Yoga)",
)

# Pixel-per-millimetre scale (used to project mm coords onto the image).
# Note: the image's vertical pixel scale is slightly compressed vs.
# horizontal (~15% off) because floor_plan.png isn't perfectly to
# scale. This will be fixed when we re-render from the new STEP file.
PX_PER_MM_X = (BLD_RIGHT_PX - BLD_LEFT_PX) / BLD_WIDTH_MM
PX_PER_MM_Y = (BLD_BOTTOM_PX - BLD_TOP_PX) / BLD_HEIGHT_MM


def mm_to_px(x_mm: float, y_mm: float) -> tuple[float, float]:
    """Map envelope-frame mm coords to image pixel coords."""
    return (BLD_LEFT_PX + x_mm * PX_PER_MM_X,
            BLD_TOP_PX  + y_mm * PX_PER_MM_Y)


def is_inside_interior(x_mm: float, y_mm: float) -> bool:
    """True iff the (x, y) point falls inside the interior strip."""
    return (INTERIOR_X_MIN_MM <= x_mm <= INTERIOR_X_MAX_MM
            and INTERIOR_Y_MIN_MM <= y_mm <= INTERIOR_Y_MAX_MM)


# ============================================================
# 2. Camera specifications
# ============================================================
# Coordinate convention:
#   Origin (0, 0) = building NW corner (top-left of plan)
#   +x → east (right on plan)
#   +y → south (down on plan)
#   +z → up    (toward ceiling)
#
# Yaw (compass-like, in our 2D frame):
#   0°   = facing east  (+x)
#   90°  = facing south (+y, down on screen)
#   180° = facing west  (-x)
#   270° = facing north (-y, up on screen)
#
# Tilt: degrees below horizontal (positive = looking down).

#
# Phase 1.5 placements (7 cameras: 4 K/WZ corners + 2 Yoga east + 1 hallway).
#
# Constraints from the latest placement review (v1.2+):
#   - Long N + S walls of every interior room are sliding glass.
#     Mid-wall mounting impossible. The structural frame at every
#     interior CORNER is solid steel and is the only allowed wall
#     fixing point. All 6 cameras mount on a ceiling drop bracket
#     directly at an interior corner.
#   - K/WZ must have full coverage including the previously-missed
#     N strip. Four corner cameras (NW, NE, SW, SE) each looking
#     diagonally to the opposite corner -> every floor point sits
#     in >= 2 cameras' FOV for re-ID handover.
#   - Yoga: both cameras on the EAST end (NE + SE corners), each
#     looking diagonally back into the room. The east wall is the
#     solid end-of-building wall (not glass) and clipping each
#     wedge to Yoga prevents the cones from bleeding into SZ
#     through the SZ-Yoga doorway.
#   - SZ has NO camera this round (entrance/exit detection deferred).
#   - BZ remains out of scope.
#
CAMERAS = [
    # ---- K/WZ : 4 corner cameras, ceiling-mounted on the structural
    # frames at each interior corner (the steel pillars between the
    # glass panels). Each camera looks diagonally across the room
    # toward the opposite corner -> every floor point is in at least
    # 2 cameras' FOV for re-ID handover.
    #
    # Per-room wedge clipping is applied in render_floor_plan so the
    # FOV cones never cross a room boundary.
    # ---------------------------------------------------------------
    # LOCKED mounting coordinates (installable structural-frame points).
    # Change x_mm / y_mm / yaw_deg only after verifying against the
    # as-built structure and regenerating outputs.
    # ---------------------------------------------------------------
    {
        "id":       1,
        "name":     "cam_kwz_sw",
        "x_mm":     2600, "y_mm": 9100, "z_mm": CEILING_H_MM,
        "yaw_deg":  330,
        "tilt_deg":  35,
        "fov_h_deg": 66,
        "room":     "K/WZ",
        "role":     "Tracking (K/WZ south-west, looks NE-ish)",
        "color":    "#1f6dd1",
    },
    {
        "id":       2,
        "name":     "cam_kwz_nw",
        "x_mm":     2600, "y_mm": 5700, "z_mm": CEILING_H_MM,
        "yaw_deg":   25,
        "tilt_deg":  35,
        "fov_h_deg": 66,
        "room":     "K/WZ",
        "role":     "Tracking (K/WZ north-west, looks SE-ish)",
        "color":    "#2080e0",
    },
    {
        "id":       3,
        "name":     "cam_kwz_ne",
        "x_mm":     6200, "y_mm": 5400, "z_mm": CEILING_H_MM,
        "yaw_deg":  155,
        "tilt_deg":  35,
        "fov_h_deg": 66,
        "room":     "K/WZ",
        "role":     "Tracking (K/WZ north-east on K/WZ-BZ frame, "
                    "looks SW-ish into K/WZ + east into BZ/SZ strip)",
        "color":    "#1859b8",
    },
    {
        "id":       4,
        "name":     "cam_kwz_se",
        "x_mm":     6400, "y_mm": 7300, "z_mm": CEILING_H_MM,
        "yaw_deg":  330,
        "tilt_deg":  35,
        "fov_h_deg": 66,
        "room":     "K/WZ",
        "role":     "Tracking (K/WZ middle-east on K/WZ-BZ frame, "
                    "looks NW-ish into K/WZ + along BZ/SZ strip)",
        "color":    "#3098f0",
    },

    # ---- Yoga : 2 cameras placed on the SOLID end-of-building
    # wall (NE) and the SE corner of Yoga; both look diagonally
    # back into Yoga. Trackable area is the rectangle defined by
    # TRACKABLE_AREAS_MM["Yoga"]; nothing is shown outside it.
    {
        "id":       5,
        "name":     "cam_yoga_ne",
        "x_mm":    17800, "y_mm": 5400, "z_mm": CEILING_H_MM,
        "yaw_deg":  155,
        "tilt_deg":  35,
        "fov_h_deg": 66,
        "room":     "Yoga",
        "role":     "Tracking (Yoga north-east, looks SW-ish)",
        "color":    "#b87010",
    },
    {
        "id":       6,
        "name":     "cam_yoga_se",
        "x_mm":    14200, "y_mm": 8700, "z_mm": CEILING_H_MM,
        "yaw_deg":  330,
        "tilt_deg":  35,
        "fov_h_deg": 66,
        "room":     "Yoga",
        "role":     "Tracking (Yoga south-west on SZ-Yoga frame, "
                    "looks NE-ish; stereo with cam 5)",
        "color":    "#d99a30",
    },

    # ---- Hallway (north-strip corridor behind Yoga) : 1 camera ----
    # Single wide camera at the east end of the corridor fans west
    # along the long axis. Polygon clip keeps the wedge inside the
    # corridor (~2 m deep × ~5.7 m long).
    # Confirm this mount against the real hallway structural frame on
    # site, then adjust x_mm / y_mm / yaw_deg if needed. Lens spec matches
    # cams 1-6 so the same OEM PoE board (5MP, IRCUT, 2.8 mm M12)
    # can be ordered as the 7th + 1 spare.
    {
        "id":       7,
        "name":     "cam_hallway_n",
        "x_mm":    14100, "y_mm": 8200, "z_mm": CEILING_H_MM,
        "yaw_deg":  180,                       # West, along the corridor
        "tilt_deg":  35,
        "fov_h_deg": 66,
        "room":     "Hallway",
        "role":     "Tracking (hallway east end, looks W along corridor)",
        "color":    "#3aa566",
    },

    # NOTE: SZ + BZ have NO camera (privacy rooms). Fall detection
    # there is handled by non-camera sensors (low-resolution thermal
    # IR + optional water leak); see docs/FINAL_CAMERA_SELECTION.md and
    # thermal_fall_detection.py. Those sensors don't appear in this
    # CAMERAS list because they don't produce x/y tracking output, only
    # zone-level presence + fall events.
]


# ============================================================
# 3. Field-of-view geometry
# ============================================================

def fov_radius_mm(z_mm: float, tilt_deg: float) -> float:
    """
    Visualisation radius for a camera's FOV wedge — intentionally
    larger than the room so the wedge can span its angular FOV all
    the way to the trackable-polygon boundary.

    Rationale: the per-room TRACKABLE_AREAS_MM polygon already
    encodes the *physical* outer bound (walls + occlusion + max
    useful detection range). The wedge is then clipped to that
    polygon in render_floor_plan, so each camera's drawn area
    fills as much of the polygon as its angular FOV permits.

    The original geometric ceiling distance d_centre = z/tan(tilt)
    is left here as a sanity check for documentation / future use.
    """
    t = np.radians(tilt_deg)
    _d_centre_documented_only = z_mm / np.tan(t)  # noqa: F841
    return 30_000.0


# ============================================================
# 4. Plot generation
# ============================================================

# Per-room outline colour for the trackable-area polygon. K/WZ
# blue matches the K/WZ background block in floor_plan.png; Yoga
# orange matches the Yoga background block.
ROOM_AREA_COLOR = {
    "K/WZ":    "#1f6dd1",
    "Yoga":    "#d99a30",
    "Hallway": "#3aa566",
}

# Privacy thermal footprints (BZ / SZ) — distinct from optical trackables.
PRIVACY_THERMAL_FACE = {
    "BZ": "#c44bd6",
    "SZ": "#6b4bc4",
}

PRIVACY_THERMAL_ALPHA = 0.18


def draw_privacy_thermal_zones(ax) -> None:
    """Fill + outline BZ/SZ thermal fall-detection polygons."""
    for name, pts_mm in PRIVACY_THERMAL_ZONES_MM.items():
        color = PRIVACY_THERMAL_FACE[name]
        poly_px = [mm_to_px(x, y) for x, y in pts_mm]
        ax.add_patch(
            Polygon(poly_px, closed=True, facecolor=color,
                    alpha=PRIVACY_THERMAL_ALPHA, edgecolor=color,
                    linewidth=1.2, linestyle="-", zorder=6))


def draw_privacy_hardware_mounts(ax) -> None:
    """Mark thermal ceiling mounts + BZ floor water-leak (plan view xy)."""
    for room_code, mt in PRIVACY_THERMAL_CEILING_MOUNT_MM.items():
        px, py = mm_to_px(mt["x_mm"], mt["y_mm"])
        face = PRIVACY_THERMAL_FACE[room_code]
        ax.scatter(
            [px], [py],
            s=320,
            marker="s",
            c=face,
            edgecolors="white",
            linewidths=2.0,
            zorder=16,
            clip_on=False,
        )
        bx = px + 14
        by = py - (18 if room_code == "SZ" else -18)
        ax.text(
            bx,
            by,
            f"{room_code} thermal\n(x,y,z)=({mt['x_mm']}, {mt['y_mm']}, {mt['z_mm']}) mm",
            fontsize=7,
            fontweight="bold",
            color="white",
            ha="left",
            va="top" if room_code == "SZ" else "bottom",
            bbox=dict(
                boxstyle="round,pad=0.35",
                facecolor=face,
                edgecolor="white",
                linewidth=1.2),
            zorder=17,
            clip_on=False,
        )

    wl = BZ_WATER_LEAK_FLOOR_MOUNT_MM
    px_w, py_w = mm_to_px(wl["x_mm"], wl["y_mm"])
    ax.scatter(
        [px_w], [py_w],
        s=300,
        marker="o",
        c="#00acc1",
        edgecolors="white",
        linewidths=2.0,
        zorder=16,
        clip_on=False,
    )
    ax.text(
        px_w + 14,
        py_w + 16,
        f"BZ leak\n(x,y,z)=({wl['x_mm']}, {wl['y_mm']}, {wl['z_mm']}) mm",
        fontsize=7,
        fontweight="bold",
        color="white",
        ha="left",
        va="bottom",
        bbox=dict(
            boxstyle="round,pad=0.35",
            facecolor="#00838f",
            edgecolor="white",
            linewidth=1.2),
        zorder=17,
        clip_on=False,
    )


def trackable_clip_path(room: str) -> MplPath:
    """Build a matplotlib clip Path from a room's TRACKABLE_AREAS_MM
    polygon (mm vertices projected to image pixel coords). Used to
    clip each camera's FOV wedge so it can never extend beyond the
    physically trackable area, even if the wedge geometry would
    reach further.
    """
    polygon_mm = TRACKABLE_AREAS_MM[room]
    polygon_px = [mm_to_px(x, y) for x, y in polygon_mm]
    polygon_px.append(polygon_px[0])
    return MplPath(polygon_px)


def draw_trackable_outline(ax, room: str) -> None:
    """Draw the trackable-area polygon as a dashed OUTLINE only
    (no fill). Each camera's individual wedge fill is what
    actually shades the area; the outline is just so the
    makes the clip boundary of the FOV wedges obvious.
    """
    polygon_mm = TRACKABLE_AREAS_MM[room]
    polygon_px = [mm_to_px(x, y) for x, y in polygon_mm]
    color = ROOM_AREA_COLOR[room]
    ax.add_patch(Polygon(polygon_px, closed=True,
                         facecolor="none",
                         edgecolor=color, linewidth=1.4,
                         linestyle=(0, (4, 3)), zorder=7,
                         alpha=0.85))


def draw_camera(ax, cam: dict, clip_path: MplPath) -> None:
    """Draw one camera: its FOV wedge (clipped to the room's
    trackable polygon) + position marker triangle + numbered ID
    badge.
    """
    cx, cy = mm_to_px(cam["x_mm"], cam["y_mm"])
    color = cam["color"]

    # FOV wedge on the floor.
    # Note: imshow with extent=[0, w, h, 0] flips the y-axis.
    # Matplotlib's Wedge angles use math convention (0°=+x, CCW
    # positive); on the flipped axis, math 90° (+y) renders as
    # visually DOWN, which matches our yaw convention (yaw=90°
    # = south). So yaw_deg passes through to Wedge unchanged.
    half_fov = cam["fov_h_deg"] / 2
    theta1 = cam["yaw_deg"] - half_fov
    theta2 = cam["yaw_deg"] + half_fov
    radius_px = fov_radius_mm(cam["z_mm"], cam["tilt_deg"]) * PX_PER_MM_X
    wedge = Wedge((cx, cy), radius_px,
                  theta1=theta1, theta2=theta2,
                  facecolor=color, alpha=0.22,
                  edgecolor=color, linewidth=1.0, linestyle="--",
                  zorder=8)
    ax.add_patch(wedge)
    wedge.set_clip_path(clip_path, transform=ax.transData)

    # Triangle pointing in the yaw direction (look-vector arrow).
    # Note: imshow with extent=[0, w, h, 0] flips the y-axis, so
    # math 90° (+y) renders visually DOWN — matches our yaw
    # convention (yaw=90° means facing south).
    yaw_rad = np.radians(cam["yaw_deg"])
    dx = np.cos(yaw_rad)
    dy = np.sin(yaw_rad)
    sz = 11
    tip = (cx + dx * sz, cy + dy * sz)
    perp_x, perp_y = -dy, dx
    base_l = (cx - dx * sz * 0.5 + perp_x * sz * 0.7,
              cy - dy * sz * 0.5 + perp_y * sz * 0.7)
    base_r = (cx - dx * sz * 0.5 - perp_x * sz * 0.7,
              cy - dy * sz * 0.5 - perp_y * sz * 0.7)
    ax.add_patch(Polygon([tip, base_l, base_r], closed=True,
                         facecolor=color, edgecolor="white",
                         linewidth=1.5, zorder=14))

    # Numbered ID badge offset behind the camera (opposite the look
    # direction) so it never overlaps the trackable-area polygon.
    label_dx = -dx * 22 - 10
    label_dy = -dy * 22
    ax.text(cx + label_dx, cy + label_dy, str(cam["id"]),
            ha="center", va="center", fontsize=11,
            fontweight="bold", color="white",
            bbox=dict(boxstyle="circle,pad=0.30",
                      facecolor=color, edgecolor="white",
                      linewidth=1.5),
            zorder=15)


def render_floor_plan() -> None:
    """Render the placement plan as a single full-width image:

      * Floor plan fills the whole figure (no side panel).
      * Per-camera FOV wedges, clipped to the trackable polygon.
      * Polygon outline drawn dashed so the bound is visible.
      * Compact legend in the top-left of the floor plan.

    The detailed camera spec table + coordinate-system notes used to
    sit in a side panel; that data now lives only in
    output/cameras_config.json + README.md so the rendered PNG is
    legible at any preview width.
    """
    img = Image.open(FLOOR_PLAN_PATH)
    img_w, img_h = img.size

    aspect = img_w / img_h
    fig_w = 14.0
    fig_h = fig_w / aspect + 0.6
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=160)
    ax.imshow(img, extent=[0, img_w, img_h, 0])

    for cam in CAMERAS:
        if not is_inside_interior(cam["x_mm"], cam["y_mm"]):
            print(f"[WARN] cam {cam['id']} ({cam['name']}) at "
                  f"({cam['x_mm']}, {cam['y_mm']}) is OUTSIDE the "
                  f"interior strip "
                  f"({INTERIOR_X_MIN_MM}..{INTERIOR_X_MAX_MM}, "
                  f"{INTERIOR_Y_MIN_MM}..{INTERIOR_Y_MAX_MM}).")

    for room in TRACKABLE_AREAS_MM:
        draw_trackable_outline(ax, room)

    draw_privacy_thermal_zones(ax)
    draw_privacy_hardware_mounts(ax)

    room_clips = {room: trackable_clip_path(room)
                  for room in TRACKABLE_AREAS_MM}
    for cam in CAMERAS:
        draw_camera(ax, cam, room_clips[cam["room"]])

    legend_elements = [
        mpatches.Patch(facecolor="#1f6dd1", alpha=0.22, edgecolor="#1f6dd1",
                       linestyle="--",
                       label="Per-camera FOV (clipped to trackable polygon)"),
        mpatches.Patch(facecolor="none", edgecolor="#1f6dd1",
                       linestyle="--", linewidth=1.4,
                       label="Trackable-area polygon (outer bound)"),
        mpatches.Patch(facecolor="#1f6dd1", edgecolor="white",
                       label="Camera position (triangle = view direction)"),
        mpatches.Patch(facecolor="#c44bd6", alpha=PRIVACY_THERMAL_ALPHA,
                       edgecolor="#c44bd6",
                       label="Thermal IR fall-detection footprint (BZ)"),
        mpatches.Patch(facecolor="#6b4bc4", alpha=PRIVACY_THERMAL_ALPHA,
                       edgecolor="#6b4bc4",
                       label="Thermal IR fall-detection footprint (SZ)"),
        plt.Line2D([0], [0], marker="s", linestyle="none", color="#c44bd6",
                   markeredgecolor="white", markersize=10,
                   label="BZ thermal ceiling mount (coords on label)"),
        plt.Line2D([0], [0], marker="s", linestyle="none", color="#6b4bc4",
                   markeredgecolor="white", markersize=10,
                   label="SZ thermal ceiling mount (coords on label)"),
        plt.Line2D([0], [0], marker="o", linestyle="none", color="#00acc1",
                   markeredgecolor="white", markersize=10,
                   label="BZ water-leak sensor (floor mount)"),
    ]
    ax.legend(handles=legend_elements, loc="upper left",
              bbox_to_anchor=(0.01, 0.99), fontsize=9, framealpha=0.95)

    fig.suptitle(
        "Camera Placement Plan — Phase 1.5 "
        "(7 cameras incl. hallway; PoE OEM board locked; "
        "SZ/BZ via thermal-IR fall sensors)",
        fontsize=12, fontweight="bold", y=0.985,
    )
    ax.set_xlim(0, img_w)
    ax.set_ylim(img_h, 0)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.tight_layout()

    out_img = OUTPUT_DIR / "camera_placement_plan.png"
    plt.savefig(out_img, dpi=180, bbox_inches="tight",
                pad_inches=0.25, facecolor="white")
    plt.close(fig)
    print(f"[ok] wrote {out_img}")


def export_config() -> None:
    config = {
        "phase": "1.5-prototype-poe",
        "coordinate_system": {
            "origin": "NW corner of the floor-plan envelope (top-left)",
            "x_axis": "east (+x = right on plan)",
            "y_axis": "south (+y = down on plan)",
            "z_axis": "up (+z = ceiling)",
            "units":  "millimetres",
            "envelope_mm":        [BLD_WIDTH_MM, BLD_HEIGHT_MM],
            "interior_bounds_mm": {
                "x_min": INTERIOR_X_MIN_MM, "x_max": INTERIOR_X_MAX_MM,
                "y_min": INTERIOR_Y_MIN_MM, "y_max": INTERIOR_Y_MAX_MM,
            },
            "ceiling_height_mm":  CEILING_H_MM,
        },
        "rooms": {
            room: {
                "x_mm":     list(b["x"]),
                "y_mm":     list(b["y"]),
                "in_scope": room not in ("BZ",),
                "scope_note": (
                    "no camera — thermal IR + water leak (BZ) fall detection; "
                    "see thermal_fall_detection + FINAL_CAMERA_SELECTION.md"
                    if room == "BZ" else
                    "no camera — thermal IR (SZ) fall detection; "
                    "see thermal_fall_detection + FINAL_CAMERA_SELECTION.md"
                    if room == "SZ" else "full tracking"
                ),
                "trackable_polygon_mm": [
                    list(pt) for pt in TRACKABLE_AREAS_MM[room]
                ] if room in TRACKABLE_AREAS_MM else None,
                "privacy_thermal_polygon_mm": (
                    [list(pt) for pt in PRIVACY_THERMAL_ZONES_MM[room]]
                    if room in PRIVACY_THERMAL_ZONES_MM else None
                ),
                "privacy_hardware_mounts_mm": (
                    {
                        "thermal_ceiling_mount_mm": (
                            PRIVACY_THERMAL_CEILING_MOUNT_MM["SZ"]
                        ),
                        "mount_note": (
                            "Ceiling-mounted MLX90640 (32x24, 55 deg FOV); "
                            "(x,y) is centroid of privacy_thermal_polygon_mm; "
                            "z = ceiling_height_mm."
                        ),
                    }
                    if room == "SZ"
                    else {
                        "thermal_ceiling_mount_mm": (
                            PRIVACY_THERMAL_CEILING_MOUNT_MM["BZ"]
                        ),
                        "water_leak_floor_mount_mm": (
                            BZ_WATER_LEAK_FLOOR_MOUNT_MM
                        ),
                        "mount_note": (
                            "Thermal: ceiling as SZ. Water leak: wired "
                            "conductive probe to Olimex ESP32-POE-ISO GPIO; "
                            "adjust xy after site survey inside "
                            "privacy_thermal_polygon_mm."
                        ),
                    }
                    if room == "BZ"
                    else None
                ),
            }
            for room, b in ROOM_BOUNDS_MM.items()
        },
        "privacy_hardware_mounts_mm": {
            "coordinate_frame": (
                "floor-plan envelope mm: origin NW, +x east, +y south; "
                "thermal z = ceiling; leak z = floor"
            ),
            "SZ": {
                "thermal_ceiling_mount_mm":
                    PRIVACY_THERMAL_CEILING_MOUNT_MM["SZ"],
            },
            "BZ": {
                "thermal_ceiling_mount_mm":
                    PRIVACY_THERMAL_CEILING_MOUNT_MM["BZ"],
                "water_leak_floor_mount_mm": (
                    BZ_WATER_LEAK_FLOOR_MOUNT_MM
                ),
            },
        },
        "privacy_thermal_zones_mm": {
            k: [[x, y] for x, y in poly]
            for k, poly in PRIVACY_THERMAL_ZONES_MM.items()
        },
        "thermal_fall_detection": {
            **THERMAL_FALL_DETECTION_EXPORT,
            "sz_rest_zone_polygon_mm": (
                [[x, y] for x, y in SZ_REST_ZONE_POLYGON_MM]
                if SZ_REST_ZONE_POLYGON_MM else None
            ),
            "logic_module": "thermal_fall_detection.PrivacyThermalFallDetector",
            "description": (
                "SZ: rapid vertical→horizontal (≤rapid_transition_max_s) + "
                "sz_still_confirmation_s motionless horizontal blob → "
                "high/med fall; slow transition or stationary rest polygon "
                "suppresses bedtime false positives. "
                "BZ: no bed/couch — horizontal-on-floor "
                "bz_still_horizontal_s stillness triggers fall "
                "(water leak → high confidence for shower)."
            ),
        },
        "privacy_room_sensors": {
            "SZ": {
                "type": "thermal-ir-grid",
                "model": "MLX90640 (32x24, 55 deg FOV)",
                "host": "Olimex ESP32-POE-ISO (ESPHome firmware)",
                "transport": "MQTT over PoE",
                "frame_rate_hz": 8,
                "door_sensor": "wired NC magnetic reed switch (ESP32 GPIO)",
                "purpose": (
                    "presence + fall detection (heat blob posture; not imaging)"
                ),
                "rationale": (
                    "On-site mmWave (Aqara FP2, Apollo R1) failed due to metal in "
                    "walls; thermal IR is optical and RF-immune. MLX90640 chosen "
                    "over AMG8833 (8x8) for reliable posture inference."
                ),
                "mqtt_topics": {
                    "thermal_frame":  "home/sz/thermal/frame",
                    "door_state":     "home/sz/door/state",
                    "node_heartbeat": "home/sz/node/heartbeat",
                },
            },
            "BZ": {
                "type": "thermal-ir-grid + wired-water-leak",
                "model": (
                    "MLX90640 (32x24, 55 deg FOV) + wired conductive leak probe"
                ),
                "host": "Olimex ESP32-POE-ISO (ESPHome firmware)",
                "transport": "MQTT over PoE",
                "frame_rate_hz": 8,
                "door_sensor": "wired NC magnetic reed switch (ESP32 GPIO)",
                "purpose": (
                    "presence + fall detection; wet floor boosts shower-slip "
                    "confidence"
                ),
                "rationale": (
                    "same radar failure mode as SZ; thermal-on-floor + "
                    "water-on-floor => very high confidence for shower falls. "
                    "Wired leak probe avoids Zigbee battery + range failure modes."
                ),
                "mqtt_topics": {
                    "thermal_frame":  "home/bz/thermal/frame",
                    "door_state":     "home/bz/door/state",
                    "leak_state":     "home/bz/leak/state",
                    "node_heartbeat": "home/bz/node/heartbeat",
                },
            },
        },
        "cameras": [
            {
                "id":   cam["id"],
                "name": cam["name"],
                "room": cam["room"],
                "role": cam["role"],
                "mount": {
                    "x_mm":     cam["x_mm"],
                    "y_mm":     cam["y_mm"],
                    "z_mm":     cam["z_mm"],
                    "yaw_deg":  cam["yaw_deg"],
                    "tilt_deg": cam["tilt_deg"],
                },
                "sensor": {
                    # Production-locked: OEM PoE IP camera board
                    # (HiSilicon Hi3516-class SoC + Sony image sensor),
                    # 1080p+ H.264 / H.265 RTSP, IRCUT, IR LED ring,
                    # M12 2.8 mm lens. Embedded behind a 3D-printed
                    # frame so only the lens objective is visible.
                    # See docs/FINAL_CAMERA_SELECTION.md for the
                    # exact SKU once the 1-unit smoke test passes.
                    "model":             "oem-poe-board-h3516-imx335",
                    "transport":         "ethernet-poe-802.3af",
                    "stream_proto":      "rtsp",
                    "codec_main":        "h.265",
                    "codec_sub":         "h.264",
                    "fov_h_deg":         cam["fov_h_deg"],
                    "fov_v_deg":         50,
                    "stream_resolution_main": [2592, 1944],
                    "stream_resolution_sub":  [640,  480],
                    "stream_fps_sub":    15,
                    "ir_night":          True,
                    "ir_cut_filter":     True,
                    "firmware_target":   "OpenIPC (after smoke test)",
                },
            }
            for cam in CAMERAS
        ],
    }
    out_json = OUTPUT_DIR / "cameras_config.json"
    out_json.write_text(json.dumps(config, indent=2))
    print(f"[ok] wrote {out_json}")


if __name__ == "__main__":
    if not FLOOR_PLAN_PATH.exists():
        raise SystemExit(
            f"floor_plan.png not found in {SCRIPT_DIR}. "
            "Place the floor plan image alongside this script."
        )
    render_floor_plan()
    export_config()
    print("Done.")
