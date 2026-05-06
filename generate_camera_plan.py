"""
Camera Placement Plan Generator — Phase 1.3
============================================

Generates an annotated floor plan image and a JSON config file for the
tracking engine, based on a list of camera specifications.

Hardware target (prototype): 6 x WiFi cameras already procured by client.
                             (See docs/FINAL_CAMERA_SELECTION.md for the
                             production PoE recommendation.)

Building:        Floor-plan envelope 19.8 m x 10.2 m, ceiling 3.0 m.
                 Interior rooms occupy a horizontal strip
                 (y ~ 4500-8400 mm); the rest of the envelope is the
                 outdoor terrace / deck and is NOT tracked.

Hardware mounting constraints (per client review of v1.2):
    - The long N and S walls of K/WZ + SZ + Yoga are continuous
      sliding-glass / patio doors. No mid-wall camera mounting.
    - The structural frame between glass panels at every interior
      CORNER is solid steel and is the only allowed wall fixing
      point. All cameras therefore mount on a ceiling drop bracket
      AT the interior corners.

Phase 1.3 placements (per client screenshots, 6 cameras):
    - K/WZ  (kitchen + living)   - 4 corner cameras (NW, NE, SW, SE)
                                   each looking diagonally to the
                                   opposite corner -> full coverage
                                   incl. the previously-missed N strip.
    - Yoga                       - 2 cameras on the EAST end (NE, SE)
                                   both looking diagonally back into
                                   Yoga. With per-room FOV clipping
                                   their cones cannot bleed into SZ.
    - SZ    (bedroom)            - NO camera this round. Entrance /
                                   exit detection is deferred (see
                                   README §6 for replacement options).
    - BZ    (bathroom)           - out of scope.

FOV cones are clipped to each camera's OWN ROOM rectangle (not just
the global interior strip) so a wedge cannot visually cross a wall.

USAGE
-----
    python3 generate_camera_plan.py

Outputs (written to ./output/):
    camera_placement_plan.png   — annotated floor plan image
    cameras_config.json         — machine-readable camera config

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
INTERIOR_Y_MIN_MM =  4500
INTERIOR_Y_MAX_MM =  8700

# Per-room interior bounds (left-to-right), matching the colour
# overlays the client drew on the updated floor_plan.png.
# Estimates from pixel measurement of the colour blocks; will be
# regenerated automatically by step_pipeline/extract_floor_plan.py
# once the STEP-parsing tool is in place.
ROOM_BOUNDS_MM = {
    "K/WZ": {"x": ( 1700,  6700), "y": (4500, 8400)},
    "BZ":   {"x": ( 6700,  8420), "y": (6400, 8400)},  # out of scope
    "SZ":   {"x": ( 8420, 12100), "y": (4500, 8400)},
    "Yoga": {"x": (12100, 17800), "y": (4500, 8400)},
}

# ---------------------------------------------------------------------
# Trackable-area polygons (mm, envelope frame).
#
# Defined directly by the client on the floor plan: each polygon is
# the *physical* area that the cameras assigned to that room can
# actually see. Used as the clip mask for the FOV wedges in the
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
        ( 2400,           8700),
        ( 2500,           4500),
        (FIREPLACE_X_MM,  4500),
        (FIREPLACE_X_MM,  6500),
        ( 6300,           6500),
        ( 6300,           8700),
    ],
    "Yoga": [
        (14100, 4800),
        (17900, 4800),
        (17900, 8300),
        (14100, 8300),
    ],
}

# Walls that must NOT be used for camera mounting (per client review):
#   - North wall of K/WZ + the K/WZ-BZ junction = continuous glazing.
#   - South walls of all rooms appear to be sliding glass too.
# All cameras are therefore CEILING-MOUNTED, away from any wall by
# at least ~1 m, dropped on a bracket/cable run.
GLASS_WALLS = (
    "north wall (K/WZ + BZ + SZ + Yoga continuous glazing)",
    "south sliding doors (K/WZ + SZ + Yoga)",
)

# Ceiling height, default mounting height for ceiling-mounted cameras.
CEILING_H_MM = 3000

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
# Phase 1.3 placements (6 cameras: 4 K/WZ corners + 2 Yoga east).
#
# Constraints from the latest client feedback (review of v1.2):
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
    # POSITIONS LOCKED BY CLIENT (installable mounting points). Do
    # not change x_mm / y_mm / yaw_deg without explicit client sign-
    # off — these were set against the actual structural frames in
    # the building.
    # ---------------------------------------------------------------
    {
        "id":       1,
        "name":     "cam_kwz_sw",
        "x_mm":     2500, "y_mm": 8600, "z_mm": CEILING_H_MM,
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
        "x_mm":     2500, "y_mm": 5200, "z_mm": CEILING_H_MM,
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
        "x_mm":     6200, "y_mm": 4900, "z_mm": CEILING_H_MM,
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
        "x_mm":     6300, "y_mm": 6400, "z_mm": CEILING_H_MM,
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
        "x_mm":    17800, "y_mm": 4900, "z_mm": CEILING_H_MM,
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
        "x_mm":    14200, "y_mm": 8200, "z_mm": CEILING_H_MM,
        "yaw_deg":  330,
        "tilt_deg":  35,
        "fov_h_deg": 66,
        "room":     "Yoga",
        "role":     "Tracking (Yoga south-west on SZ-Yoga frame, "
                    "looks NE-ish; stereo with cam 5)",
        "color":    "#d99a30",
    },
    # NOTE: SZ has NO camera this round (per latest client feedback).
    # Bedroom entrance/exit detection becomes a deferred item;
    # candidate replacements are listed in README §6.
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
    "K/WZ": "#1f6dd1",
    "Yoga": "#d99a30",
}


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
    client can see the outer bound the wedges are clipped to.
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
    ]
    ax.legend(handles=legend_elements, loc="upper left",
              bbox_to_anchor=(0.01, 0.99), fontsize=9, framealpha=0.95)

    fig.suptitle(
        "Camera Placement Plan — Phase 1.4 "
        "(per-camera FOV wedges, clipped to client-defined polygons)",
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
        "phase": "1.4-prototype-wifi",
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
                "in_scope": room not in ("BZ", "SZ"),
                "scope_note": (
                    "out of scope this round" if room == "BZ" else
                    "no camera this round (entry/exit deferred)"
                    if room == "SZ" else "full tracking"
                ),
                "trackable_polygon_mm": [
                    list(pt) for pt in TRACKABLE_AREAS_MM[room]
                ] if room in TRACKABLE_AREAS_MM else None,
            }
            for room, b in ROOM_BOUNDS_MM.items()
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
                    # Prototype: client-procured WiFi camera, OV2640-class.
                    # Replace with the production model once finalised — see
                    # docs/FINAL_CAMERA_SELECTION.md.
                    "model":             "wifi-prototype-OV2640",
                    "transport":         "wifi",
                    "stream_proto":      "rtsp-or-mjpeg",
                    "fov_h_deg":         cam["fov_h_deg"],
                    "fov_v_deg":         50,
                    "stream_resolution": [640, 480],
                    "stream_fps":        12,
                    "ir_night":          False,
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
