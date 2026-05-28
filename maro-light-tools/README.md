# Maro Light Tools

Three self-contained web-based control panels for the lights in the apartment.
Each tool lives in its own subfolder. All run with stock Python 3 (no `pip install`
needed) and open a browser tab automatically.

## Network requirements

You need to reach two pieces of hardware on the apartment LAN (or routed via
**Tailscale**). Both speak Art-Net (UDP port 6454):

| Device | IP | Drives |
|---|---|---|
| ODE MK3 (Enttec) | `192.168.178.11` | DMX channels — spots, dimmers |
| Pixelator MK2 | `192.168.178.10` | ARGB pixel strips (WS2814 RGBW, 60 LED/m) |

If you are on Tailscale: ping both IPs first. If they don't answer, the head
Pi needs to advertise the 192.168.178.0/24 subnet — ask Calvin to enable
subnet routing in the Tailscale admin console.

## Tools

### 1. `dmx_test/` — DMX channel test (ODE MK3)

```
cd dmx_test
python3 dmx_test_gui.py     # or double-click dmx-test.command on Mac
```

Opens `http://127.0.0.1:8422`. Slider per DMX channel, four universes
selectable. Use this for individual DMX fixtures (tunable-white spots,
dimmers, valves).

### 2. `meter_test/` — ARGB strip test (Pixelator MK2)

```
cd meter_test
python3 meter_test_gui.py   # or double-click meter-test.command
```

Opens `http://127.0.0.1:8424`. Per-port colour animations on the 6 ARGB
strips. Reads `meter_test/config/artnet_mapping.json` and `strips.json` for
the strip → port mapping (bundled).

Strip mapping reference (all WS2814 RGBW, 60 LEDs/m, 4 channels per LED,
`pixel_grouping = 6` so each logical pixel drives 6 physical LEDs = 10 cm):

| Strip | start_universe | universes | reversed |
|---|---|---|---|
| YogaSchra | 0 | 1 | no |
| KZ/WZ | 1 | 2 | **yes** |
| Waschbecke | 3 | 1 | no |
| Dusche | 5 | 1 | no |
| FLUR | 6 | 1 | no |
| KLO | 7 | 1 | no |

### 3. `artnet_test/` — raw Art-Net packet debugger

```
cd artnet_test
python3 artnet_test_gui.py  # or double-click artnet-test.command
```

Low-level Art-Net packet sender for diagnosing universe/channel routing
problems. Use when you're not sure which Pixelator output port a strip is
actually on.

## Reference docs

`reference/dmx_binary_ref.html` — DMX channel layout reference  
`reference/dmx_planner.html`   — visual DMX planning tool

Open either one directly in a browser.

## Safety note

These tools and the Maro server both send Art-Net to the same controllers —
last packet on the wire wins. While testing, either:

- Stop the Maro server, **or**
- Pause Maro's Art-Net output:
  ```
  curl -X POST http://192.168.178.25:8420/api/artnet/enabled \
       -H "Content-Type: application/json" -d '{"enabled": false}'
  ```
  …and re-enable when you're done.
