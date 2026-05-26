# Calibration day — one-page guide

For the homeowner / operator. You will walk through the house once,
phone in hand, telling each camera *"this is where I am right now"* at a
handful of spots in each room. The system uses those positions to learn
the floor map. Takes 15–25 minutes total. No tape measure required.

> **See it once before you do it.** A 30-second video clip of the loop
> (Recapture → tap plan → tap feet → walk) lives next to this guide —
> ask the installer for the link, or see
> [`calibration_day_video_script.md`](calibration_day_video_script.md)
> if they haven't shot it yet.

> Nothing in this process needs an installer or a remote technician —
> you do it yourself. The whole tool runs on the Raspberry Pi in your
> house; nothing about the calibration leaves your network.

## What you need

- The **Raspberry Pi 5** is powered on and on the house Wi-Fi.
- All **seven cameras** are powered and reachable (red lights or PoE
  status LEDs on; the tracking engine has shown them in its log at
  least once).
- A **smartphone** on the same Wi-Fi as the Pi.
- A pair of shoes with a clear, contrasting sole or a marker on your
  shoe (a strip of bright tape works) — this makes it easier to click
  exactly where your feet are.

## 1. Start the calibration tool

On the Pi (one of the following):

- Already enabled as a service:
  ```bash
  sudo systemctl start tracking-calibrate-web
  ```
- Or one-off from the repo:
  ```bash
  python -m tracking_engine.calibrate_web
  ```

Either way the tool listens on **port 8090**.

## 2. Open it on your phone

In your phone's browser, go to **`http://<pi-ip>:8090`**. The Pi's IP
is the one shown by your router, or whichever address the installer
gave you (often `192.168.178.<something>`).

You should see:

- Left half — the house floor plan with the rooms labelled (`K/WZ`,
  `BZ`, `SZ`, `Hallway`, `Yoga`).
- Right half — a grid of seven small camera tiles, each labelled with
  its name and the room it covers.

If a tile says *"no snapshot yet"*, tap **Recapture all** once at the
top of the page. After a moment every tile should show a live image
from that camera.

## 3. The loop — repeat at each spot

This is the only loop you have to learn. Do it for every position you
visit:

1. **Stand still** at the spot. Make sure your shoes are inside the
   field of view of as many cameras as possible.
2. Tap **Recapture all** at the top. Wait one second — each tile now
   contains a fresh frame **with you in it**.
3. On the floor plan, **tap exactly where you are standing**. A blue
   numbered dot appears.
4. In every camera tile where you can see yourself, **tap exactly on
   your own feet** in the image. A small blue circle marks the click.
5. For tiles where you are *not* visible, tick the **not visible**
   checkbox so the system knows that's deliberate, not a miss.
6. Walk to the next spot, repeat from step 1.

You don't have to go in any particular order. You can also fix a
mistake at any time by tapping a position in the list and re-clicking
the wrong tile.

## 4. How many positions, where?

A rough target — feel free to do a few more:

| Room | Cameras covering it | Positions to visit | Where |
|------|---------------------|--------------------|-------|
| K/WZ (kitchen + living) | 4 (`cam_kwz_sw`, `_nw`, `_ne`, `_se`) | **8** | Spread across the L-shape: kitchen end, living end, both sides of the doorway to BZ. Try to put at least one position in each diagonal quadrant. |
| Yoga | 2 (`cam_yoga_ne`, `cam_yoga_se`) | **6** | Four corners + middle + near-doorway. Both cameras should see most of these. |
| Hallway | 1 (`cam_hallway_n`) | **6** | Six spots along the corridor — east end, near both door thresholds, west end, plus two intermediate. |

The numbers in the top bar of the page tell you in real time whether
each camera has enough clicks. A green pip next to a camera name means
that camera is happy.

> Tip: walking in a **Z pattern** or an **X across the room** beats
> walking along the walls. Cameras learn the floor better when your
> positions are spread out in two dimensions, not all on the same line.

## 5. What the colours mean

Two numbers tell you how well it's going. **Watch them, fix anything red.**

- **Per-camera residual** (top of each tile, e.g. `mean 42 / max 89 mm`)
  - **green** — that camera's fit is solid.
  - **amber** — usable, but more spread-out positions would help.
  - **red** — at least one click is off. Tap the position list, find
    the position with the worst disagreement, and re-tap your feet on
    that tile.
- **Cross-camera disagreement** (per position, e.g. `Δ 38 mm` in the list)
  - This is the killer feature. When two cameras both saw you at the
    same spot, this number tells you how far apart they place you in
    real-world coordinates. Below 100 mm is great, above 250 mm means
    one of the cameras got a sloppy click.

The page **recomputes automatically** about a second after every
click, so you'll see the numbers move as you work.

## 6. Save when done

When every camera shows a **green pip** in the top bar:

- Tap **Download session** first — this writes a backup JSON to your
  phone's downloads. If anything goes wrong with the save, you can
  load that file back into the tool later instead of redoing the walk.
- Then tap **Save**. The Pi writes the new calibration. A confirmation
  toast appears at the bottom of the screen.

Restart the tracker once so it picks up the new file (the installer
can do this for you, or you can run on the Pi):

```bash
sudo systemctl restart tracking-engine
```

That's it. Your house is calibrated.

## 7. If something goes wrong

- **A camera tile is grey or stuck on "no decoded frame yet"** — the
  camera is offline or unreachable. Skip it for now (do not tick "not
  visible" at every position; that would calibrate it as useless).
  Call the installer; once the camera is back, re-run the tool and
  add positions only for that camera.
- **The Save button won't write** — a banner at the bottom will list
  the reason (usually one camera has fewer than 6 clicks, or one
  camera's residual is over 250 mm). Fix the listed problem and try
  again. There is also a **force** option in the warning popup if you
  really need to save a less-than-perfect calibration.
- **I clicked the wrong spot on the floor plan** — tap the position in
  the list at the bottom of the left panel and press **Delete**. Then
  add it again.
- **I clicked the wrong spot on a camera tile** — select that position
  in the list, then tap again on the tile (the click is replaced, not
  added). Or use the **Clear click** button at the bottom of the tile.
- **I want to take a break** — tap **Download session** before closing
  the tab. To resume, tap **Load session** in the same toolbar and
  pick the JSON.

## When you should recalibrate later

You only need to repeat this if:

- a camera was moved, even slightly (someone bumped the bracket, the
  ceiling was repainted, the camera was unscrewed and put back),
- a major piece of furniture moved that you used as a click landmark,
- the system starts placing people clearly off where they actually
  are.

Spot fixes for a single moved camera can use the older single-camera
tool in `tools/calibrate_homography.py`; the full re-walk described
above is only needed if multiple cameras have moved.
