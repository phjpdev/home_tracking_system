# ESPHome firmware for privacy-zone nodes

Two Olimex ESP32-POE-ISO boards. One per privacy room. Each one carries a
ceiling-mounted **MLX90640** thermal grid; the BZ board additionally hosts
the wired water-leak probe. Both boards publish to MQTT (Mosquitto on the
Raspberry Pi 5).

## Files

| File | Purpose |
|------|---------|
| `common.yaml` | Shared substitutions, ethernet/PoE, MQTT, I2C bus, OTA. Included by both node configs. |
| `sz_node.yaml` | SZ (bedroom) node — MLX90640 + door reed. |
| `bz_node.yaml` | BZ (bathroom) node — MLX90640 + door reed + wired leak probe. |
| `secrets.example.yaml` | Template for `secrets.yaml` (gitignored). |

## Flash

```bash
pip install esphome
cp esphome/secrets.example.yaml esphome/secrets.yaml   # edit credentials
esphome run esphome/sz_node.yaml
esphome run esphome/bz_node.yaml
```

First flash uses USB; subsequent updates can use OTA over LAN.

## MQTT topics

```
home/sz/thermal/frame     JSON {"ts":..., "shape":[24,32], "temp_c":[...]}
home/sz/door/state        "open" | "closed"
home/sz/node/heartbeat    {"state":"online" | "offline"}
home/bz/thermal/frame     same shape as SZ
home/bz/door/state        "open" | "closed"
home/bz/leak/state        "wet"  | "dry"
home/bz/node/heartbeat    {"state":"online" | "offline"}
```

Cross-reference: matching consumer in `tracking_engine/thermal/mqtt_subscriber.py`.

## Wiring

| Signal | Pin (both boards) | Notes |
|--------|-------------------|-------|
| MLX90640 SDA | GPIO13 | I2C @ 400 kHz |
| MLX90640 SCL | GPIO16 | shared bus |
| Door reed (NC) | GPIO34 | 10 k pull-up to 3V3, switch to GND |
| Leak probe (BZ only) | GPIO35 | active-low; 10 k pull-up, conductive bar to GND |
| 3V3 / GND | sensor power | I2C only — no 5 V devices |

Power: 802.3af PoE via the board's built-in regulator. No local PSU.

## Mount geometry

See [../plan/MASTER_PLAN.md](../plan/MASTER_PLAN.md) section 6.3 and the
`privacy_room_sensors` block in
[../camera_placement_plan/output/cameras_config.json](../camera_placement_plan/output/cameras_config.json).

| Room | Mount (x_mm, y_mm, z_mm) |
|------|--------------------------|
| SZ thermal ceiling | (12150, 6500, 3000) |
| BZ thermal ceiling | centroid of polygon, z = 3000 |
| BZ leak probe floor | (7450, 8900, 0) — verify after site survey |

## Soak test (Phase B exit criterion)

```bash
mosquitto_sub -h 192.168.1.10 -t "home/+/thermal/frame" | head -n 10
mosquitto_sub -h 192.168.1.10 -t "home/+/door/state"
mosquitto_sub -h 192.168.1.10 -t "home/+/leak/state"
mosquitto_sub -h 192.168.1.10 -t "home/+/node/heartbeat"
```

Expectations recorded in `docs/soak_test_phase_b.txt`:

- 8 Hz thermal frames for ≥ 30 minutes uninterrupted on both rooms.
- Door state toggles within 250 ms of physical movement.
- BZ leak probe transitions within 500 ms of wetting.
- No "offline" heartbeat in a 6-hour soak (auto-published via MQTT will).
