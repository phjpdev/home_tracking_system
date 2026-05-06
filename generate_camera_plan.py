"""
Camera Placement Plan Generator — Phase 1
==========================================

Generates an annotated floor plan image and a JSON config file for the
tracking engine, based on a list of camera specifications.

Hardware target: ESP32-CAM-MB (OV2640 sensor) over WiFi.
Building:        ~17.5 m × 5 m interior, ceiling height 3.0 m.

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
from matplotlib.patches import Rectangle, Polygon, Wedge, FancyBboxPatch
import matplotlib.patches as mpatches


# ============================================================
# 1. Building & coordinate-system constants
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
FLOOR_PLAN_PATH = SCRIPT_DIR / "floor_plan.png"
OUTPUT_DIR = SCRIPT_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# Pixel coordinates of the building rectangle in floor_plan.png.
# Derived once from the source plan (see README).
BLD_LEFT_PX   = 179
BLD_RIGHT_PX  = 940
BLD_TOP_PX    = 260
BLD_BOTTOM_PX = 442

# Real-world inside dimensions of the building (millimetres).
# 19.75 m envelope width includes rounded corners; interior is ~17.5 m.
BLD_WIDTH_MM  = 17500
BLD_HEIGHT_MM =  5000

# Ceiling height, used as the default mounting height for ceiling-mounted
# cameras. Update this if the actual ceiling differs.
CEILING_H_MM = 3000

# Pixel-per-millimetre scale (used to project mm coords onto the image).
PX_PER_MM_X = (BLD_RIGHT_PX - BLD_LEFT_PX) / BLD_WIDTH_MM
PX_PER_MM_Y = (BLD_BOTTOM_PX - BLD_TOP_PX) / BLD_HEIGHT_MM


def mm_to_px(x_mm: float, y_mm: float) -> tuple[float, float]:
    """Map building-frame mm coords to image pixel coords."""
    return (BLD_LEFT_PX + x_mm * PX_PER_MM_X,
            BLD_TOP_PX  + y_mm * PX_PER_MM_Y)


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

CAMERAS = [
    {
        "id":       1,
        "name":     "cam_kwz_ne",
        "x_mm":     7900, "y_mm":  300, "z_mm": CEILING_H_MM,
        "yaw_deg":  145,   # SW — into the room from NE corner
        "tilt_deg": 35,
        "fov_h_deg": 66,
        "room":     "K/WZ",
        "role":     "Tracking (kitchen + dining)",
        "color":    "#1f6dd1",
    },
    {
        "id":       2,
        "name":     "cam_kwz_sw",
        "x_mm":      300, "y_mm": 4700, "z_mm": CEILING_H_MM,
        "yaw_deg":  325,   # NE — into the room from SW corner
        "tilt_deg": 35,
        "fov_h_deg": 66,
        "room":     "K/WZ",
        "role":     "Tracking (sofa + entry, overlaps cam 1 for re-ID)",
        "color":    "#1f6dd1",
    },
    {
        "id":       3,
        "name":     "cam_bz_door",
        "x_mm":     4400, "y_mm": 2700, "z_mm": CEILING_H_MM,
        "yaw_deg":  270,   # north — back at the BZ entry door
        "tilt_deg": 60,    # steep: only see the door area
        "fov_h_deg": 66,
        "room":     "BZ",
        "role":     "Presence-only (bathroom door watcher)",
        "color":    "#138a9c",
    },
    {
        "id":       4,
        "name":     "cam_sz_door",
        "x_mm":     8400, "y_mm": 3500, "z_mm": CEILING_H_MM,
        "yaw_deg":  180,   # west — back at the SZ entry door
        "tilt_deg": 55,
        "fov_h_deg": 66,
        "room":     "SZ",
        "role":     "Presence-only (bedroom entry watcher)",
        "color":    "#a83e38",
    },
    {
        "id":       5,
        "name":     "cam_yoga",
        "x_mm":    12900, "y_mm":  300, "z_mm": CEILING_H_MM,
        "yaw_deg":  45,    # SE — into the room from NW corner
        "tilt_deg": 35,
        "fov_h_deg": 66,
        "room":     "Yoga",
        "role":     "Tracking (full yoga room)",
        "color":    "#b87010",
    },
]


# ============================================================
# 3. Field-of-view geometry
# ============================================================

def fov_radius_mm(z_mm: float, tilt_deg: float) -> float:
    """
    Practical floor coverage distance (radius) for visualization.

    Geometric reasoning:
      A camera at height z, tilted by t° below horizontal, has its
      frame centre on the floor at distance d_centre = z / tan(t).
      The far edge of the frame (where the floor recedes toward the
      horizon) becomes increasingly oblique and unreliable for person
      detection — humans appear as flat smears.

      We take the effective tracking radius as 1.4 × d_centre, capped
      at 6 m. Beyond ~6 m, the OV2640 at 640×480 doesn't have enough
      pixels-on-target to reliably detect a standing person.
    """
    t = np.radians(tilt_deg)
    centre = z_mm / np.tan(t)
    return min(centre * 1.4, 6000)


# ============================================================
# 4. Plot generation
# ============================================================

def label_room(ax, x_mm: float, y_mm: float,
               text: str, color: str, size: int = 15) -> None:
    px, py = mm_to_px(x_mm, y_mm)
    ax.text(px, py, text, ha="center", va="center",
            fontsize=size, fontweight="bold", color=color,
            bbox=dict(boxstyle="round,pad=0.30", facecolor="white",
                      edgecolor=color, linewidth=1.2, alpha=0.92),
            zorder=11)


def draw_camera(ax, cam: dict) -> None:
    cx, cy = mm_to_px(cam["x_mm"], cam["y_mm"])
    color = cam["color"]

    # FOV wedge on the floor
    # Note: imshow with extent=[0, w, h, 0] flips the y-axis.
    # Matplotlib's Wedge angles still use math convention (0°=+x,
    # CCW positive). On the flipped axis, math 90° (+y) renders as
    # visually DOWN, which matches our yaw convention (90° = south).
    # So we can pass yaw_deg directly to Wedge without conversion.
    half_fov = cam["fov_h_deg"] / 2
    theta1 = cam["yaw_deg"] - half_fov
    theta2 = cam["yaw_deg"] + half_fov

    radius_px = fov_radius_mm(cam["z_mm"], cam["tilt_deg"]) * PX_PER_MM_X

    ax.add_patch(Wedge((cx, cy), radius_px,
                       theta1=theta1, theta2=theta2,
                       facecolor=color, alpha=0.18,
                       edgecolor=color, linewidth=1.2, linestyle="--",
                       zorder=8))

    # Camera position marker — triangle pointing in the yaw direction.
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

    # ID badge
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
    img = Image.open(FLOOR_PLAN_PATH)
    img_w, img_h = img.size

    fig = plt.figure(figsize=(20, 11), dpi=140)
    gs = fig.add_gridspec(1, 2, width_ratios=[2.5, 1], wspace=0.04)
    ax = fig.add_subplot(gs[0, 0])
    ax_table = fig.add_subplot(gs[0, 1])
    ax_table.axis("off")
    ax.imshow(img, extent=[0, img_w, img_h, 0])

    # Room background fills (no borders — design decision per client review)
    kwz_poly = [(179, 262), (534, 262), (534, 342),
                (355, 342), (355, 441), (179, 441)]
    ax.add_patch(Polygon(kwz_poly, closed=True, facecolor="#4a6fc9",
                         alpha=0.15, edgecolor="none", zorder=2))
    ax.add_patch(Rectangle((355, 348), 179, 94, facecolor="#2e9bb8",
                           alpha=0.22, edgecolor="none", zorder=3))
    ax.add_patch(Rectangle((534, 262), 194, 176, facecolor="#c0524a",
                           alpha=0.15, edgecolor="none", zorder=2))
    ax.add_patch(Rectangle((728, 260), 212, 164, facecolor="#c98a2e",
                           alpha=0.18, edgecolor="none", zorder=2))

    # Room labels
    label_room(ax,  2300,  800, "K/WZ", "#4a6fc9", size=15)
    label_room(ax,  6100, 3700, "BZ",   "#2e9bb8", size=12)
    label_room(ax, 10800,  800, "SZ",   "#c0524a", size=15)
    label_room(ax, 15400,  800, "Yoga", "#c98a2e", size=15)

    ax.text(560, 130, "Outdoor Deck / Garden",
            ha="center", va="center", fontsize=11, style="italic",
            color="#777",
            bbox=dict(boxstyle="round,pad=0.32", facecolor="white",
                      edgecolor="#aaa", linewidth=0.8, alpha=0.92),
            zorder=11)

    # Cameras
    for cam in CAMERAS:
        draw_camera(ax, cam)

    # Right-hand spec table
    table_data = [
        [f"#{cam['id']}", cam["name"], cam["room"],
         f"{cam['x_mm']}, {cam['y_mm']}", f"{cam['z_mm']}",
         f"{cam['tilt_deg']}°", f"{cam['yaw_deg']}°"]
        for cam in CAMERAS
    ]
    col_labels = ["ID", "Name", "Room", "(x, y) mm",
                  "Height z mm", "Tilt", "Yaw"]
    t = ax_table.table(cellText=table_data, colLabels=col_labels,
                       loc="upper center", cellLoc="center",
                       colWidths=[0.05, 0.20, 0.10, 0.18, 0.13, 0.10, 0.10])
    t.auto_set_font_size(False)
    t.set_fontsize(9)
    t.scale(1.2, 1.7)
    for col_idx in range(len(col_labels)):
        cell = t[(0, col_idx)]
        cell.set_facecolor("#333")
        cell.set_text_props(color="white", fontweight="bold")
    for row_idx, cam in enumerate(CAMERAS, start=1):
        for col_idx in range(len(col_labels)):
            t[(row_idx, col_idx)].set_facecolor("#f8f8f8")
        id_cell = t[(row_idx, 0)]
        id_cell.set_facecolor(cam["color"])
        id_cell.set_text_props(color="white", fontweight="bold")

    notes = (
        "COORDINATES\n"
        "  Origin (0, 0) = building NW corner (top-left of plan)\n"
        "  X axis east →,  Y axis south ↓,  Z axis up\n"
        f"  Building inside ~ {BLD_WIDTH_MM} × {BLD_HEIGHT_MM} mm\n"
        f"  Ceiling at z = {CEILING_H_MM} mm\n\n"
        "YAW (compass-like)\n"
        "  0° = east,  90° = south,  180° = west,  270° = north\n\n"
        "TILT\n"
        "  Degrees below horizontal (+ = looking down)\n\n"
        "CAMERA SPEC\n"
        "  Sensor: OV2640,  H-FOV ≈ 66°,  V-FOV ≈ 50°\n"
        "  Stream: 640×480 @ ~12 fps over WiFi\n\n"
        "ROLES\n"
        "  Tracking — outputs full (x, y) per person\n"
        "  Presence-only — outputs zone + privacy=true,\n"
        "                  no per-person coordinates"
    )
    ax_table.text(0.02, 0.45, notes, fontsize=9.5, va="top", ha="left",
                  family="monospace", color="#222")

    legend_elements = [
        mpatches.Patch(facecolor="#1f6dd1", alpha=0.18, edgecolor="#1f6dd1",
                       linestyle="--",
                       label="Camera FOV cone (~66° H-FOV)"),
        mpatches.Patch(facecolor="#1f6dd1", edgecolor="white",
                       label="Camera position (triangle = view direction)"),
    ]
    ax.legend(handles=legend_elements, loc="lower left",
              bbox_to_anchor=(0.01, 0.01), fontsize=9, framealpha=0.95)

    fig.suptitle(
        "Camera Placement Plan — Phase 1 (5 × ESP32-CAM-MB, WiFi)",
        fontsize=15, fontweight="bold", y=0.97,
    )
    ax.set_xlim(0, img_w)
    ax.set_ylim(img_h, 0)
    ax.set_aspect("equal")
    ax.axis("off")

    out_img = OUTPUT_DIR / "camera_placement_plan.png"
    plt.savefig(out_img, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"[ok] wrote {out_img}")


def export_config() -> None:
    config = {
        "coordinate_system": {
            "origin": "building NW corner (top-left of floor plan)",
            "x_axis": "east (+x = right on plan)",
            "y_axis": "south (+y = down on plan)",
            "z_axis": "up (+z = ceiling)",
            "units":  "millimetres",
            "building_size_mm":   [BLD_WIDTH_MM, BLD_HEIGHT_MM],
            "ceiling_height_mm":  CEILING_H_MM,
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
                    "model":             "OV2640",
                    "fov_h_deg":         cam["fov_h_deg"],
                    "fov_v_deg":         50,
                    "stream_resolution": [640, 480],
                    "stream_fps":        12,
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
