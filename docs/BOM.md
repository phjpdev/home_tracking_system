# Bill of Materials

Authoritative shopping list for the home deployment. Per
[plan/MASTER_PLAN.md](../plan/MASTER_PLAN.md) section 11.

## Compute and infrastructure

| # | Item | Qty | Notes |
|---|------|----:|-------|
| 1 | Raspberry Pi 5 (8 GB RAM) | 1 | Main compute. Active cooler + heatsink required. |
| 2 | Raspberry Pi 5 active cooler | 1 | Sustained CPU load demands it. |
| 3 | Pi 5 case (vented) | 1 | Optional but recommended. |
| 4 | 64 GB or 128 GB SSD on M.2 HAT or USB3 SSD | 1 | Database + logs; SD card is not durable enough. |
| 5 | Pi 5 PSU (27 W USB-C) | 1 | Official PSU avoids brownout warnings. |
| 6 | 8-port PoE+ switch (TP-Link TL-SG1008P or Ubiquiti USW-Lite-8-PoE) | 1 | Powers cameras + privacy ESP32 nodes. |
| 7 | Cat6 cable, 305 m box | 1 | Up to 35 m per run. Pull through ceiling cavity. |
| 8 | Cat6 keystones + faceplates + RJ45 plugs | 1 set | For tidy wall terminations. |
| 9 | Patch panel (12-port) | 1 | Optional but recommended for serviceability. |
| 10 | UPS (small, 600 VA) | 1 | Keeps Pi + switch alive through outages. |

## Cameras

Already specified — see [../camera_placement_plan/docs/FINAL_CAMERA_SELECTION.md](../camera_placement_plan/docs/FINAL_CAMERA_SELECTION.md).

| # | Item | Qty | Notes |
|---|------|----:|-------|
| 11 | OEM PoE IP camera board (HiSilicon Hi3516 + Sony IMX335) | 7 + 1 spare | 1080p+ RTSP H.265, IR night, M12 2.8 mm lens. |
| 12 | 3D-printed ceiling enclosures for cameras | 8 | Built so only the lens objective protrudes. |

## Privacy-zone sensors (SZ + BZ)

| # | Item | Qty | Notes |
|---|------|----:|-------|
| 13 | MLX90640 32 x 24 thermal grid, 55 deg FOV variant | 2 | One per privacy room. Pre-soldered breakout preferred. |
| 14 | Olimex ESP32-POE-ISO | 2 | PoE-powered, galvanic isolation, I2C-capable. |
| 15 | Wired NC magnetic reed switch | 2 | One per privacy room door. |
| 16 | Conductive water-leak probe (wired) | 1 | BZ only. Mount near shower egress. |
| 17 | 10 k resistors (1/4 W) | 10 | Pull-ups for reed + leak GPIO. |
| 18 | 3D-printed ceiling enclosures for ESP32 + MLX90640 | 2 | Hides board, exposes thermal aperture only. |
| 19 | Cat6 patch leads | 4 | Drops from ceiling to each node and the patch panel. |

## Workstation only (one-off, optional)

| # | Item | Qty | Notes |
|---|------|----:|-------|
| 20 | USB-C cable for ESP32 first-flash | 1 | OTA after first boot. |
| 21 | Crimper + cable tester | 1 set | Network installation. |

## Software costs

All zero-cost open-source: Raspberry Pi OS 64-bit, Mosquitto, ESPHome,
PyTorch (for one-time ONNX export only — not deployed on Pi),
ONNX Runtime, FAISS, paho-mqtt, prometheus_client, structlog.

## Estimated total (excluding cameras already costed)

| Block | Approx EUR |
|-------|----------:|
| Compute + infra (items 1–10) | 350 |
| Privacy sensors (items 13–19) | 180 |
| Workstation (items 20–21) | 40 |
| **Subtotal** | **570** |

Add cameras (~7 x 36 + 1 spare = ~290 EUR) + cabling labour for the
total deployment cost.
