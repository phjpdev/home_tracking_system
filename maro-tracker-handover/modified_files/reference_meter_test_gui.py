"""
Meter-Test GUI — Wie viele Meter LED sollen leuchten?
Rechnet automatisch um: Meter → LEDs → Kanäle → Universen.

WS2814 RGBW: 60 LEDs/m, 4 Kanäle/LED (GBWR)
Pixelator MK2: 16 Ports × 8 Universen = 128 Universen
  Port N = Uni (N-1)*8 … (N-1)*8+7
"""

import colorsys
import http.server
import json
import os
import socket
import struct
import threading
import time
import webbrowser

CONFIG_DIR = os.path.join(os.path.dirname(__file__), "..", "config")
MAPPING_FILE = os.path.join(CONFIG_DIR, "artnet_mapping.json")
STRIPS_FILE = os.path.join(CONFIG_DIR, "strips.json")


def load_artnet_mapping():
    with open(MAPPING_FILE, "r") as f:
        return json.load(f)


def save_artnet_mapping(mapping):
    with open(MAPPING_FILE, "w") as f:
        json.dump(mapping, f, indent=2, ensure_ascii=False)


def load_argb_strips():
    """Return list of ARGB strip names from strips.json."""
    with open(STRIPS_FILE, "r") as f:
        strips = json.load(f)
    return [s["name"] for s in strips if s.get("type", "").lower() == "argb"]

PIXELATOR_IP = "192.168.178.10"
ARTNET_PORT = 6454
CHANNELS_PER_UNIVERSE = 512
LEDS_PER_METER = 60
CHANNELS_PER_LED = 4  # RGBW
pixel_grouping = 6    # How many physical LEDs per logical pixel
HTTP_PORT = 8424

# DMX nodes: each receives the same RGBW values simultaneously
dmx_nodes = [
    {"name": "ODE Mk3", "ip": "192.168.178.11", "universe": 0, "start_ch": 1, "enabled": False},
]

PORTS = [
    {"name": "Output 1",  "start_uni": 0,  "max_uni": 1},
    {"name": "Output 2",  "start_uni": 1,  "max_uni": 2},  # Uni 1–2, 256 Pixel
    {"name": "Output 3",  "start_uni": 3,  "max_uni": 1},
    {"name": "Output 4",  "start_uni": 4,  "max_uni": 1},
    {"name": "Output 5",  "start_uni": 5,  "max_uni": 1},
    {"name": "Output 6",  "start_uni": 6,  "max_uni": 1},
    {"name": "Output 7",  "start_uni": 7,  "max_uni": 1},
    {"name": "Output 8",  "start_uni": 8,  "max_uni": 1},
    {"name": "Output 9",  "start_uni": 48, "max_uni": 6},
    {"name": "Output 10", "start_uni": 54, "max_uni": 6},
    {"name": "Output 11", "start_uni": 60, "max_uni": 6},
    {"name": "Output 12", "start_uni": 66, "max_uni": 6},
    {"name": "Output 13", "start_uni": 72, "max_uni": 6},
    {"name": "Output 14", "start_uni": 78, "max_uni": 6},
    {"name": "Output 15", "start_uni": 84, "max_uni": 6},
    {"name": "Output 16", "start_uni": 90, "max_uni": 6},
]

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def make_artdmx(universe, data):
    header = b"Art-Net\x00"
    opcode = struct.pack("<H", 0x5000)
    proto = struct.pack(">H", 14)
    seq = b"\x00"
    phys = b"\x00"
    univ = struct.pack("<H", universe)
    length = struct.pack(">H", len(data))
    return header + opcode + proto + seq + phys + univ + length + data


def send_channels(start_universe, channel_data):
    """Send raw channel bytes starting at given universe."""
    offset = 0
    uni = start_universe
    first = True
    while offset < len(channel_data):
        chunk = channel_data[offset:offset + CHANNELS_PER_UNIVERSE]
        data = bytes(chunk).ljust(CHANNELS_PER_UNIVERSE, b"\x00")
        pkt = make_artdmx(uni, data)
        sock.sendto(pkt, (PIXELATOR_IP, ARTNET_PORT))
        if first:
            # Show exactly what goes on the wire
            nonzero = sum(1 for b in data if b > 0)
            hex24 = ' '.join(f'{b:02x}' for b in data[:24])
            print(f"  Uni {uni}: {nonzero} non-zero bytes | first 24: {hex24}")
            first = False
        elif any(b > 0 for b in data):
            nonzero = sum(1 for b in data if b > 0)
            print(f"  Uni {uni}: {nonzero} non-zero bytes")
        uni += 1
        offset += CHANNELS_PER_UNIVERSE


def send_all_dmx(r, g, b, w):
    """Send RGBW to all enabled DMX nodes simultaneously.
    Nodes sharing the same IP+universe are merged into one packet."""
    buffers = {}  # (ip, universe) → bytearray(512)
    for node in dmx_nodes:
        if not node.get("enabled"):
            continue
        key = (node["ip"], node["universe"])
        if key not in buffers:
            buffers[key] = bytearray(512)
        ch = node["start_ch"] - 1
        if 0 <= ch and ch + 3 < 512:
            buffers[key][ch]   = max(0, min(255, r))
            buffers[key][ch+1] = max(0, min(255, g))
            buffers[key][ch+2] = max(0, min(255, b))
            buffers[key][ch+3] = max(0, min(255, w))
    for (ip, universe), data in buffers.items():
        pkt = make_artdmx(universe, bytes(data))
        sock.sendto(pkt, (ip, ARTNET_PORT))


def led_to_channels(r, g, b, w):
    """Convert RGBW values to channel bytes (4ch RGBW)."""
    return [r, g, b, w]


def hsv_to_rgbw(h, s, v):
    """Hue 0-360, s/v 0-1 → RGBW (W=0, pure colors)."""
    r, g, b = colorsys.hsv_to_rgb(h / 360, s, v)
    return int(r * 255), int(g * 255), int(b * 255), 0


# --- Animation ---
_anim_thread = None
_anim_running = False


def _anim_setup(port_idx, meters):
    """Common setup: returns (port, logical_pixels, max_pixels)."""
    cpl = 4
    grp = pixel_grouping
    port = PORTS[port_idx]
    physical_leds = round(meters * LEDS_PER_METER)
    logical_pixels = (physical_leds + grp - 1) // grp
    max_pixels = port["max_uni"] * CHANNELS_PER_UNIVERSE // cpl
    return port, min(logical_pixels, max_pixels), max_pixels


def run_animation_fade(port_idx, meters, colors, seconds_per_fill):
    """Global crossfade: all pixels transition simultaneously, ease-in-out."""
    global _anim_running
    port, logical_pixels, max_pixels = _anim_setup(port_idx, meters)
    cpl = 4
    fps = 30
    frames = max(1, int(seconds_per_fill * fps))
    color_idx = 0
    frame = 0

    while _anim_running:
        t0 = time.time()
        t = frame / frames
        # ease-in-out
        t = t * t * (3 - 2 * t)
        c1 = colors[color_idx % len(colors)]
        c2 = colors[(color_idx + 1) % len(colors)]
        r = int(c1['r'] + (c2['r'] - c1['r']) * t)
        g = int(c1['g'] + (c2['g'] - c1['g']) * t)
        b = int(c1['b'] + (c2['b'] - c1['b']) * t)
        w = int(c1['w'] + (c2['w'] - c1['w']) * t)

        pixel = [r, g, b, w]
        channels = pixel * logical_pixels + [0] * cpl * (max_pixels - logical_pixels)
        send_channels(port["start_uni"], channels)
        send_all_dmx(r, g, b, w)

        frame += 1
        if frame >= frames:
            color_idx = (color_idx + 1) % len(colors)
            frame = 0

        time.sleep(max(0.0, 1 / fps - (time.time() - t0)))

    blackout_port(port_idx)


def run_animation(port_idx, meters, colors, seconds_per_fill):
    """Lauflicht: wipes color by color across the strip."""
    global _anim_running
    cpl = 4
    port, logical_pixels, max_pixels = _anim_setup(port_idx, meters)

    fps = 30
    pixels_per_frame = logical_pixels / max(seconds_per_fill * fps, 1)
    color_idx = 0
    progress = 0.0
    prev_ch = [0, 0, 0, 0]

    while _anim_running:
        t0 = time.time()
        c = colors[color_idx % len(colors)]
        cur_ch = [c['r'], c['g'], c['b'], c['w']]
        p = int(progress)

        channels = []
        for i in range(logical_pixels):
            channels.extend(cur_ch if i < p else prev_ch)
        channels.extend([0] * cpl * (max_pixels - logical_pixels))
        send_channels(port["start_uni"], channels)
        send_all_dmx(*cur_ch)

        progress += pixels_per_frame
        if progress >= logical_pixels:
            prev_ch = cur_ch[:]
            color_idx = (color_idx + 1) % len(colors)
            progress = 0.0

        sleep_time = max(0.0, 1 / fps - (time.time() - t0))
        time.sleep(sleep_time)

    blackout_port(port_idx)


def fill_pattern(port_idx, meters, pattern, r, g, b, w):
    """Send a named pattern to a port."""
    cpl = 4
    grp = pixel_grouping
    port = PORTS[port_idx]
    physical_leds = round(meters * LEDS_PER_METER)
    logical_pixels = (physical_leds + grp - 1) // grp
    max_pixels = port["max_uni"] * CHANNELS_PER_UNIVERSE // cpl
    logical_pixels = min(logical_pixels, max_pixels)

    channels = []
    if pattern == 'rainbow':
        for i in range(logical_pixels):
            h = (i / max(logical_pixels - 1, 1)) * 360
            pr, pg, pb, pw = hsv_to_rgbw(h, 1.0, 1.0)
            channels.extend([pr, pg, pb, pw])
    elif pattern == 'warm_cold':
        for i in range(logical_pixels):
            t = i / max(logical_pixels - 1, 1)
            # warm (W) → cold (B)
            wv = int((1 - t) * 255)
            bv = int(t * 200)
            channels.extend([0, 0, bv, wv])
    elif pattern == 'gradient_w':
        for i in range(logical_pixels):
            t = i / max(logical_pixels - 1, 1)
            channels.extend([0, 0, 0, int(t * w)])
    elif pattern == 'gradient_color':
        for i in range(logical_pixels):
            t = i / max(logical_pixels - 1, 1)
            channels.extend([int(t * r), int(t * g), int(t * b), int(t * w)])
    elif pattern == 'alternating':
        c1 = [r, g, b, w]
        c2 = [w, r, g, b]  # shifted
        for i in range(logical_pixels):
            channels.extend(c1 if i % 2 == 0 else c2)
    elif pattern == 'thirds':
        for i in range(logical_pixels):
            t = i / max(logical_pixels - 1, 1)
            if t < 1/3:
                channels.extend([255, 0, 0, 0])
            elif t < 2/3:
                channels.extend([0, 255, 0, 0])
            else:
                channels.extend([0, 0, 255, 0])

    # Pad to full port size
    off = [0] * cpl
    channels.extend(off * (max_pixels - logical_pixels))
    send_channels(port["start_uni"], channels)
    send_all_dmx(r, g, b, w)
    return physical_leds


def fill_meters(port_idx, meters, r, g, b, w):
    """Fill exactly `meters` worth of LEDs on a port."""
    cpl = 4  # RGBW
    grp = pixel_grouping
    port = PORTS[port_idx]
    physical_leds = round(meters * LEDS_PER_METER)
    logical_pixels = (physical_leds + grp - 1) // grp  # Round up
    max_pixels = port["max_uni"] * CHANNELS_PER_UNIVERSE // cpl
    logical_pixels = min(logical_pixels, max_pixels)
    on_ch = led_to_channels(r, g, b, w)
    off_ch = [0] * cpl
    channels = on_ch * logical_pixels + off_ch * (max_pixels - logical_pixels)
    send_channels(port["start_uni"], channels)
    send_all_dmx(r, g, b, w)
    return physical_leds


def blackout_port(port_idx):
    port = PORTS[port_idx]
    for uni in range(port["start_uni"], port["start_uni"] + port["max_uni"]):
        data = bytes(CHANNELS_PER_UNIVERSE)
        pkt = make_artdmx(uni, data)
        sock.sendto(pkt, (PIXELATOR_IP, ARTNET_PORT))


def blackout_all():
    for i in range(len(PORTS)):
        blackout_port(i)
    send_all_dmx(0, 0, 0, 0)


def fill_all_ports(r, g, b, w):
    """Fill all ports at maximum pixels with a single color."""
    for port in PORTS:
        max_pixels = port["max_uni"] * CHANNELS_PER_UNIVERSE // CHANNELS_PER_LED
        channels = [r, g, b, w] * max_pixels
        send_channels(port["start_uni"], channels)
    send_all_dmx(r, g, b, w)


def fill_gradient(port_idx, meters, w_max):
    """Gradient fill: lights up meters worth, fading from bright to dark."""
    cpl = 4  # RGBW
    grp = pixel_grouping
    port = PORTS[port_idx]
    physical_leds = round(meters * LEDS_PER_METER)
    logical_pixels = (physical_leds + grp - 1) // grp
    max_pixels = port["max_uni"] * CHANNELS_PER_UNIVERSE // cpl
    logical_pixels = min(logical_pixels, max_pixels)
    channels = []
    for i in range(logical_pixels):
        frac = 1.0 - (i / max(logical_pixels - 1, 1))
        wv = round(w_max * frac)
        channels.extend(led_to_channels(0, 0, 0, wv))
    channels.extend([0] * cpl * (max_pixels - logical_pixels))
    send_channels(port["start_uni"], channels)
    send_all_dmx(0, 0, 0, w_max)
    return physical_leds


def send_raw_test(port_idx, raw_bytes):
    """Send exact raw bytes to a port, pad rest with zeros."""
    port = PORTS[port_idx]
    total = port["max_uni"] * CHANNELS_PER_UNIVERSE
    channel_data = list(raw_bytes) + [0] * (total - len(raw_bytes))
    print(f"\n  RAW TEST: {len(raw_bytes)} bytes")
    print(f"  Hex: {' '.join(f'{b:02x}' for b in raw_bytes[:48])}")
    send_channels(port["start_uni"], channel_data)


def fill_n_leds(port_idx, num_leds, r, g, b, w):
    """Fill exactly num_leds physical LEDs, rest black."""
    cpl = 4  # RGBW
    grp = pixel_grouping
    port = PORTS[port_idx]
    logical_pixels = (num_leds + grp - 1) // grp
    max_pixels = port["max_uni"] * CHANNELS_PER_UNIVERSE // cpl
    logical_pixels = min(logical_pixels, max_pixels)
    on_ch = led_to_channels(r, g, b, w)
    off_ch = [0] * cpl
    channels = on_ch * logical_pixels + off_ch * (max_pixels - logical_pixels)
    print(f"\n  fill_n_leds: {num_leds} phys LEDs ÷ {grp} grouping = {logical_pixels} logical pixels × {cpl} ch")
    print(f"  Pattern per pixel: {on_ch}")
    print(f"  Total bytes: {logical_pixels * cpl}, universes: {(logical_pixels * cpl + 511) // 512}")
    send_channels(port["start_uni"], channels)
    send_all_dmx(r, g, b, w)
    return num_leds


def mark_at_meter(port_idx, meter_pos, r, g, b, w):
    """Mark LED at exact meter position."""
    cpl = 4  # RGBW
    grp = pixel_grouping
    port = PORTS[port_idx]
    physical_led = round(meter_pos * LEDS_PER_METER)
    pixel_idx = physical_led // grp
    max_pixels = port["max_uni"] * CHANNELS_PER_UNIVERSE // cpl
    if pixel_idx >= max_pixels:
        pixel_idx = max_pixels - 1
    channels = [0] * (max_pixels * cpl)
    on = led_to_channels(r, g, b, w)
    dim = led_to_channels(r // 3, g // 3, b // 3, w // 3)
    for j, v in enumerate(on):
        channels[pixel_idx * cpl + j] = v
    if pixel_idx > 0:
        for j, v in enumerate(dim):
            channels[(pixel_idx - 1) * cpl + j] = v
    if pixel_idx < max_pixels - 1:
        for j, v in enumerate(dim):
            channels[(pixel_idx + 1) * cpl + j] = v
    send_channels(port["start_uni"], channels)
    return physical_led


HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>Meter-Test</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #1a1a2e; color: #fff; font-family: -apple-system, system-ui, sans-serif;
    display: flex; flex-direction: column; align-items: center; padding: 20px; min-height: 100vh;
  }
  h1 { font-size: 22px; margin-bottom: 4px; }
  .sub { color: #666; font-size: 13px; margin-bottom: 18px; }
  .card {
    background: #222; border: 1px solid #333; border-radius: 10px;
    padding: 16px 20px; margin-bottom: 12px; width: 100%; max-width: 520px;
  }
  .card h2 { font-size: 13px; color: #888; margin-bottom: 10px; font-weight: 500; text-transform: uppercase; letter-spacing: 1px; }
  .port-row { display: flex; gap: 6px; margin-bottom: 12px; flex-wrap: wrap; }
  .port-btn {
    padding: 6px 4px; border: 2px solid #444; border-radius: 6px; background: #1a1a2e;
    color: #fff; font-size: 12px; font-weight: 600; cursor: pointer; text-align: center;
    min-width: 52px; flex: 1 0 52px;
  }
  .port-btn.active { border-color: #00b4d8; color: #00b4d8; background: #0a2a3a; }
  .port-btn .detail { font-size: 10px; color: #555; font-weight: 400; display: block; margin-top: 1px; }
  .port-btn.active .detail { color: #0088a8; }
  .grp-btn {
    flex: 1; padding: 8px; border: 2px solid #444; border-radius: 8px; background: #1a1a2e;
    color: #fff; font-size: 14px; font-weight: 600; cursor: pointer; text-align: center;
  }
  .grp-btn.active { border-color: #e94560; color: #e94560; background: #2a1520; }
  .grp-btn .detail { font-size: 11px; color: #555; font-weight: 400; display: block; margin-top: 2px; }
  .grp-btn.active .detail { color: #b83050; }
  .meter-input-row {
    display: flex; align-items: center; gap: 12px; margin-bottom: 12px;
  }
  .meter-slider { flex: 1; accent-color: #00b4d8; height: 6px; }
  .meter-val {
    font-size: 28px; font-weight: 700; color: #00b4d8; min-width: 80px; text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .meter-val span { font-size: 16px; font-weight: 400; color: #0088a8; }
  .calc-grid {
    display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin-bottom: 14px;
  }
  .calc-item {
    background: #1a1a2e; border-radius: 6px; padding: 8px; text-align: center;
  }
  .calc-item .num { font-size: 20px; font-weight: 700; color: #fff; }
  .calc-item .label { font-size: 11px; color: #666; margin-top: 2px; }
  .btn-row { display: flex; gap: 8px; flex-wrap: wrap; }
  .fill-btn {
    flex: 1; min-width: 80px; padding: 12px 8px; border: none; border-radius: 8px;
    font-size: 13px; font-weight: 600; cursor: pointer;
    box-shadow: 0 2px 8px rgba(0,0,0,0.3); transition: transform 0.1s;
  }
  .fill-btn:hover { transform: scale(1.03); }
  .fill-btn:active { transform: scale(0.97); }
  .mark-row {
    display: flex; align-items: center; gap: 8px; margin-top: 12px;
  }
  .mark-input {
    width: 70px; padding: 8px; border: 2px solid #444; border-radius: 6px;
    background: #1a1a2e; color: #fff; font-size: 14px; text-align: center;
  }
  .mark-input:focus { border-color: #00b4d8; outline: none; }
  .mark-btn {
    padding: 8px 14px; border: 2px solid #555; border-radius: 6px;
    background: #222; color: #fff; font-size: 13px; cursor: pointer;
  }
  .mark-btn:hover { border-color: #888; }
  .blackout-btn {
    width: 100%; max-width: 520px; padding: 14px; border: 2px solid #555; border-radius: 10px;
    background: #222; color: #ff4444; font-size: 16px; font-weight: 700;
    cursor: pointer; letter-spacing: 1px; margin-top: 4px;
  }
  .blackout-btn:hover { border-color: #ff4444; background: #2a1515; }
  .bri-row { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
  .bri-row label { font-size: 12px; color: #888; }
  .bri-row input { flex: 1; accent-color: #00b4d8; }
  .bri-row .val { color: #00b4d8; font-size: 14px; min-width: 30px; text-align: right; }
  .color-preview { height: 44px; border-radius: 8px; border: 1px solid #333; margin-bottom: 8px; transition: background 0.1s; }
  .ch-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  .ch-row .ch-label { font-size: 12px; font-weight: 700; min-width: 18px; }
  .ch-row input[type=range] { flex: 1; height: 4px; }
  .ch-row .ch-val { font-size: 13px; min-width: 28px; text-align: right; font-variant-numeric: tabular-nums; }
  .preset-btn { padding: 6px 10px; border: 1px solid #444; border-radius: 6px; background: #1a1a2e; color: #fff; font-size: 12px; font-weight: 700; cursor: pointer; }
  .preset-btn:hover { border-color: #666; }
  .anim-preset { width: 28px; height: 28px; border: 2px solid #333; border-radius: 5px; cursor: pointer; padding: 0; transition: transform 0.1s, border-color 0.1s; }
  .anim-preset:hover { transform: scale(1.2); border-color: #fff; }
  #status { margin-top: 12px; color: #555; font-size: 12px; min-height: 18px; text-align: center; }
  #status.ok { color: #2ecc71; }
</style>
</head>
<body>

<h1>Meter-Test</h1>
<div class="sub">Pixelator MK2 · PIXELATOR_IP · WS2814 RGBW 60 LED/m</div>

<!-- Port-Auswahl -->
<div class="card">
  <h2>Port</h2>
  <div class="port-row">
    PORT_BUTTONS_PLACEHOLDER
  </div>

  <h2>Pixel-Grouping</h2>
  <div class="port-row" style="margin-bottom:12px">
    <button class="grp-btn" data-grp="1" onclick="setGrouping(1)">1:1<span class="detail">kein Grouping</span></button>
    <button class="grp-btn" data-grp="3" onclick="setGrouping(3)">1:3</button>
    <button class="grp-btn active" data-grp="6" onclick="setGrouping(6)">1:6<span class="detail">Standard</span></button>
  </div>

  <h2>DMX Nodes</h2>
  <div id="dmx-nodes-list"></div>
  <button onclick="addDmxNode()" style="margin-top:4px;padding:5px 12px;border:1px solid #444;border-radius:6px;background:#1a1a2e;color:#aaa;font-size:12px;cursor:pointer">+ Node hinzufügen</button>

  <h2>Helligkeit</h2>
  <div class="bri-row">
    <label>0</label>
    <input type="range" id="brightness" min="0" max="255" value="255" oninput="document.getElementById('bri-val').textContent=this.value">
    <span class="val" id="bri-val">255</span>
  </div>
</div>

<!-- Farbe -->
<div class="card">
  <h2>Farbe</h2>
  <div class="color-preview" id="color-preview"></div>
  <div class="ch-row">
    <span class="ch-label" style="color:#e74c3c">R</span>
    <input type="range" id="ch-r" min="0" max="255" value="0" oninput="onColorChange()" style="accent-color:#e74c3c">
    <span class="ch-val" id="val-r" style="color:#e74c3c">0</span>
  </div>
  <div class="ch-row">
    <span class="ch-label" style="color:#2ecc71">G</span>
    <input type="range" id="ch-g" min="0" max="255" value="0" oninput="onColorChange()" style="accent-color:#2ecc71">
    <span class="ch-val" id="val-g" style="color:#2ecc71">0</span>
  </div>
  <div class="ch-row">
    <span class="ch-label" style="color:#3498db">B</span>
    <input type="range" id="ch-b" min="0" max="255" value="0" oninput="onColorChange()" style="accent-color:#3498db">
    <span class="ch-val" id="val-b" style="color:#3498db">0</span>
  </div>
  <div class="ch-row" style="margin-top:4px;padding-top:8px;border-top:1px solid #2a2a3a">
    <span class="ch-label" style="color:#f5e6c8">W</span>
    <input type="range" id="ch-w" min="0" max="255" value="255" oninput="onColorChange()" style="accent-color:#f5e6c8">
    <span class="ch-val" id="val-w" style="color:#f5e6c8">255</span>
  </div>
  <div style="display:flex;gap:6px;margin-top:10px;flex-wrap:wrap">
    <button class="preset-btn" style="color:#f5e6c8" onclick="setPreset(0,0,0,255)">Warm W</button>
    <button class="preset-btn" style="color:#ddeeff" onclick="setPreset(0,0,0,255);setWarmth(6500)">Kalt W</button>
    <button class="preset-btn" style="color:#e74c3c" onclick="setPreset(255,0,0,0)">R</button>
    <button class="preset-btn" style="color:#2ecc71" onclick="setPreset(0,255,0,0)">G</button>
    <button class="preset-btn" style="color:#3498db" onclick="setPreset(0,0,255,0)">B</button>
    <button class="preset-btn" style="color:#fff" onclick="setPreset(255,255,255,255)">All</button>
    <button class="preset-btn" style="color:#555" onclick="setPreset(0,0,0,0)">Off</button>
  </div>
  <div style="margin-top:10px">
    <input type="color" id="rgb-picker" value="#000000" oninput="onPickerChange(this.value)"
      style="width:100%;height:36px;border:none;border-radius:6px;cursor:pointer;background:none;padding:0">
  </div>
</div>

<!-- Meter-Eingabe -->
<div class="card">
  <h2>Wie viele Meter?</h2>
  <div class="meter-input-row">
    <input type="range" class="meter-slider" id="meter-slider" min="0" max="100" step="0.1" value="20" oninput="updateMeter(this.value)">
    <div class="meter-val" id="meter-display">20.0 <span>m</span></div>
  </div>

  <div class="calc-grid">
    <div class="calc-item">
      <div class="num" id="calc-leds">600</div>
      <div class="label">LEDs</div>
    </div>
    <div class="calc-item">
      <div class="num" id="calc-channels">2400</div>
      <div class="label">Kanäle</div>
    </div>
    <div class="calc-item">
      <div class="num" id="calc-universes">4.7</div>
      <div class="label">Universen</div>
    </div>
  </div>

  <div class="btn-row">
    <button class="fill-btn" style="background:#00b4d8;color:#fff" onclick="doFillColor()">Fill Farbe</button>
    <button class="fill-btn" style="background:linear-gradient(90deg,#f5e6c8,#333);color:#fff" onclick="doGradient()">Gradient</button>
  </div>

  <div class="mark-row">
    <span style="font-size:12px;color:#888">Markiere Meter:</span>
    <input type="number" class="mark-input" id="mark-meter" min="0" max="100" step="0.1" value="5">
    <button class="mark-btn" onclick="doMark()">Markieren</button>
    <span style="font-size:11px;color:#555" id="mark-info"></span>
  </div>
</div>

<!-- LED-für-LED -->
<div class="card">
  <h2>Fill bis LED Nr.</h2>
  <div class="meter-input-row">
    <input type="range" class="meter-slider" id="led-slider" min="1" max="6000" step="1" value="1200" oninput="updateLedSlider(this.value)">
    <div class="meter-val" id="led-display">1200</div>
  </div>
  <div class="calc-grid" style="grid-template-columns:1fr 1fr 1fr 1fr">
    <div class="calc-item"><div class="num" id="led-meter">10.0</div><div class="label">Meter</div></div>
    <div class="calc-item"><div class="num" id="led-ch">2400</div><div class="label">Kanäle</div></div>
    <div class="calc-item"><div class="num" id="led-uni">4.7</div><div class="label">Universen</div></div>
    <div class="calc-item"><div class="num" id="led-byte">2400</div><div class="label">Bytes total</div></div>
  </div>
  <div class="btn-row">
    <button class="fill-btn" style="background:#00b4d8;color:#fff" onclick="doLedFillColor()">Fill Farbe</button>
  </div>
</div>

<!-- Lauflicht -->
<div class="card">
  <h2>Lauflicht</h2>
  <div id="anim-color-list" style="display:flex;flex-wrap:wrap;gap:6px;min-height:32px;margin-bottom:10px;align-items:center">
    <span style="color:#555;font-size:11px">Keine Farben – unten hinzufügen</span>
  </div>
  <div style="display:flex;flex-wrap:wrap;gap:5px;margin-bottom:8px">
    <button class="anim-preset" style="background:#ff0000" onclick="addAnimPreset(255,0,0,0)" title="Rot"></button>
    <button class="anim-preset" style="background:#ff5000" onclick="addAnimPreset(255,80,0,0)" title="Orange-Rot"></button>
    <button class="anim-preset" style="background:#ff8c00" onclick="addAnimPreset(255,140,0,0)" title="Orange"></button>
    <button class="anim-preset" style="background:#ffcc00" onclick="addAnimPreset(255,200,0,0)" title="Gelb"></button>
    <button class="anim-preset" style="background:#aaff00" onclick="addAnimPreset(170,255,0,0)" title="Gelbgrün"></button>
    <button class="anim-preset" style="background:#00ff00" onclick="addAnimPreset(0,255,0,0)" title="Grün"></button>
    <button class="anim-preset" style="background:#00ff88" onclick="addAnimPreset(0,255,136,0)" title="Mintgrün"></button>
    <button class="anim-preset" style="background:#00ffcc" onclick="addAnimPreset(0,255,200,0)" title="Türkis"></button>
    <button class="anim-preset" style="background:#00ccff" onclick="addAnimPreset(0,200,255,0)" title="Cyan"></button>
    <button class="anim-preset" style="background:#0088ff" onclick="addAnimPreset(0,136,255,0)" title="Hellblau"></button>
    <button class="anim-preset" style="background:#0000ff" onclick="addAnimPreset(0,0,255,0)" title="Blau"></button>
    <button class="anim-preset" style="background:#5500ff" onclick="addAnimPreset(85,0,255,0)" title="Indigo"></button>
    <button class="anim-preset" style="background:#aa00ff" onclick="addAnimPreset(170,0,255,0)" title="Violett"></button>
    <button class="anim-preset" style="background:#ff00ff" onclick="addAnimPreset(255,0,255,0)" title="Magenta"></button>
    <button class="anim-preset" style="background:#ff0088" onclick="addAnimPreset(255,0,136,0)" title="Pink"></button>
    <button class="anim-preset" style="background:#ff3c1e" onclick="addAnimPreset(255,60,30,0)" title="Koralle"></button>
    <button class="anim-preset" style="background:#9650ff" onclick="addAnimPreset(150,80,255,0)" title="Lavendel"></button>
    <button class="anim-preset" style="background:#c8dcff" onclick="addAnimPreset(200,220,255,0)" title="Kaltweiß"></button>
    <button class="anim-preset" style="background:#fff8f0;border-color:#888" onclick="addAnimPreset(0,0,0,255)" title="Warmweiß (W)"></button>
    <button class="anim-preset" style="background:#ffffff;border-color:#888" onclick="addAnimPreset(255,255,255,255)" title="Vollweiß"></button>
  </div>
  <div style="display:flex;gap:6px;margin-bottom:10px">
    <button class="preset-btn" style="flex:1" onclick="addAnimColor()">+ Aktuelle Farbe</button>
    <button class="preset-btn" onclick="clearAnimColors()" style="color:#e94560">✕ Leeren</button>
  </div>
  <div style="display:flex;gap:6px;margin-bottom:10px">
    <button id="mode-wipe" class="preset-btn active-mode" style="flex:1;border-color:#e94560;color:#e94560" onclick="setAnimMode('wipe')">Lauflicht</button>
    <button id="mode-fade" class="preset-btn" style="flex:1" onclick="setAnimMode('fade')">Fade</button>
  </div>
  <div class="bri-row" style="margin-bottom:12px">
    <label>Sekunden / Farbe</label>
    <input type="range" id="anim-speed" min="0.5" max="30" step="0.5" value="3" oninput="document.getElementById('anim-speed-val').textContent=this.value+'s'">
    <span class="val" id="anim-speed-val">3s</span>
  </div>
  <button id="anim-btn" class="fill-btn" style="width:100%;background:#e94560;color:#fff;font-size:15px" onclick="toggleAnimation()">▶ Start</button>
</div>

<!-- Muster -->
<div class="card">
  <h2>Muster</h2>
  <div class="btn-row" style="flex-wrap:wrap;gap:6px">
    <button class="fill-btn" style="background:linear-gradient(90deg,#e74c3c,#f39c12,#2ecc71,#3498db,#9b59b6);color:#fff" onclick="doPattern('rainbow')">Regenbogen</button>
    <button class="fill-btn" style="background:linear-gradient(90deg,#f5e6c8,#fff,#f5e6c8);color:#333" onclick="doPattern('warm_cold')">Warm→Kalt</button>
    <button class="fill-btn" style="background:linear-gradient(90deg,#000,#fff);color:#fff" onclick="doPattern('gradient_w')">Gradient W</button>
    <button class="fill-btn" style="background:linear-gradient(90deg,#000,#e74c3c);color:#fff" onclick="doPattern('gradient_color')">Gradient Farbe</button>
    <button class="fill-btn" style="background:#1a1a2e;border:1px solid #444;color:#fff" onclick="doPattern('alternating')">Abwechselnd</button>
    <button class="fill-btn" style="background:#1a1a2e;border:1px solid #444;color:#fff" onclick="doPattern('thirds')">Drittel RGB</button>
  </div>
</div>

<!-- Raw Byte Tests -->
<div class="card">
  <h2>Raw Byte Diagnostik</h2>
  <p style="font-size:11px;color:#666;margin-bottom:10px">Sendet exakte Byte-Muster um Pixelator-Mapping zu verstehen</p>
  <div class="btn-row" style="margin-bottom:8px">
    <button class="fill-btn" style="background:#444;color:#fff;min-width:auto;font-size:11px" onclick="rawTest('single_bytes')">1× 0xFF</button>
    <button class="fill-btn" style="background:#444;color:#fff;min-width:auto;font-size:11px" onclick="rawTest('byte_positions')">FF 00 00 00</button>
    <button class="fill-btn" style="background:#444;color:#fff;min-width:auto;font-size:11px" onclick="rawTest('color_id')">4× FF @0,12,24,36</button>
    <button class="fill-btn" style="background:#444;color:#fff;min-width:auto;font-size:11px" onclick="rawTest('staircase')">Treppe (FF + 5×00)</button>
  </div>
  <div class="btn-row">
    <button class="fill-btn" style="background:#555;color:#fff;min-width:auto;font-size:11px" onclick="rawTest('every_nth',{n:1})">Jedes Byte FF</button>
    <button class="fill-btn" style="background:#555;color:#fff;min-width:auto;font-size:11px" onclick="rawTest('every_nth',{n:3})">Jedes 3. Byte</button>
    <button class="fill-btn" style="background:#555;color:#fff;min-width:auto;font-size:11px" onclick="rawTest('every_nth',{n:4})">Jedes 4. Byte</button>
    <button class="fill-btn" style="background:#555;color:#fff;min-width:auto;font-size:11px" onclick="rawTest('every_nth',{n:6})">Jedes 6. Byte</button>
  </div>
</div>

<!-- Alle Ports -->
<div class="card" style="max-width:520px">
  <h2>Alle Ports — 100%</h2>
  <div class="btn-row">
    <button class="fill-btn" style="background:#fff8f0;color:#333" onclick="doAllPorts(0,0,0,255)">Weiß</button>
    <button class="fill-btn" style="background:#e74c3c;color:#fff" onclick="doAllPorts(255,0,0,0)">Rot</button>
    <button class="fill-btn" style="background:#3498db;color:#fff" onclick="doAllPorts(0,0,255,0)">Blau</button>
    <button class="fill-btn" style="background:#2ecc71;color:#fff" onclick="doAllPorts(0,255,0,0)">Grün</button>
  </div>
</div>

<!-- Strip → Port Mapping -->
<div class="card" style="max-width:520px">
  <h2>LED Strip → Port Zuordnung</h2>
  <p style="font-size:12px;color:#888;margin-bottom:10px">Welcher ARGB-Streifen hängt an welchem Pixelator-Port?<br>Speichern schreibt direkt in artnet_mapping.json.</p>
  <div id="strip-map-list"><span style="color:#666;font-size:12px">Lade…</span></div>
  <div style="margin-top:10px;display:flex;gap:8px">
    <button class="fill-btn" style="flex:1" onclick="saveStrips()">💾 Speichern</button>
  </div>
  <div id="strip-map-status" style="font-size:12px;color:#2ecc71;margin-top:6px;min-height:16px"></div>
</div>

<button class="blackout-btn" onclick="doBlackout()">BLACKOUT</button>
<div id="status">Bereit</div>

<script>
let activePort = 0;
let grp = 6;
const CH_PER_LED = 4;
const LEDS_PER_M = 60;
const CH_PER_UNI = 512;
const PORT_MAX_UNI = PORT_MAX_UNI_PLACEHOLDER;

function getCustomColor() {
  const bri = getBri() / 255;
  return {
    r: Math.round(parseInt(document.getElementById('ch-r').value) * bri),
    g: Math.round(parseInt(document.getElementById('ch-g').value) * bri),
    b: Math.round(parseInt(document.getElementById('ch-b').value) * bri),
    w: Math.round(parseInt(document.getElementById('ch-w').value) * bri),
  };
}

function onColorChange() {
  const r = parseInt(document.getElementById('ch-r').value);
  const g = parseInt(document.getElementById('ch-g').value);
  const b = parseInt(document.getElementById('ch-b').value);
  const w = parseInt(document.getElementById('ch-w').value);
  document.getElementById('val-r').textContent = r;
  document.getElementById('val-g').textContent = g;
  document.getElementById('val-b').textContent = b;
  document.getElementById('val-w').textContent = w;
  // Preview: RGB + white brightens all channels
  const pr = Math.min(255, r + w);
  const pg = Math.min(255, g + w);
  const pb = Math.min(255, b + w);
  document.getElementById('color-preview').style.background = `rgb(${pr},${pg},${pb})`;
  // Sync picker to RGB
  const hex = '#' + [r, g, b].map(v => v.toString(16).padStart(2, '0')).join('');
  document.getElementById('rgb-picker').value = hex;
}

function onPickerChange(hex) {
  document.getElementById('ch-r').value = parseInt(hex.slice(1, 3), 16);
  document.getElementById('ch-g').value = parseInt(hex.slice(3, 5), 16);
  document.getElementById('ch-b').value = parseInt(hex.slice(5, 7), 16);
  onColorChange();
}

function setPreset(r, g, b, w) {
  document.getElementById('ch-r').value = r;
  document.getElementById('ch-g').value = g;
  document.getElementById('ch-b').value = b;
  document.getElementById('ch-w').value = w;
  onColorChange();
}

function doFillColor() {
  const c = getCustomColor();
  post('fill', {port: activePort, meters: getMeters(), r: c.r, g: c.g, b: c.b, w: c.w});
}

function doLedFillColor() {
  const n = parseInt(document.getElementById('led-slider').value);
  const c = getCustomColor();
  post('fill_leds', {port: activePort, num_leds: n, r: c.r, g: c.g, b: c.b, w: c.w});
}

function setPort(p) {
  activePort = p;
  document.querySelectorAll('.port-btn').forEach(b => b.classList.toggle('active', parseInt(b.dataset.port) === p));
  updateCalc();
  showStatus('Port ' + (p + 1));
}

function setGrouping(g) {
  grp = g;
  document.querySelectorAll('.grp-btn').forEach(b => b.classList.toggle('active', parseInt(b.dataset.grp) === g));
  fetch('/set_grouping', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({grp: g})});
  updateCalc();
  showStatus('Pixel-Grouping: 1:' + g + ' → ' + Math.ceil(600/g) + ' logische Pixel für 600 LEDs', true);
}

function updateCalc() {
  const m = parseFloat(document.getElementById('meter-slider').value);
  updateMeter(m);
}

function updateMeter(val) {
  const m = parseFloat(val);
  document.getElementById('meter-display').innerHTML = m.toFixed(1) + ' <span>m</span>';
  const leds = Math.round(m * LEDS_PER_M);
  const pixels = Math.ceil(leds / grp);
  const maxPixels = Math.floor((PORT_MAX_UNI[activePort] || 1) * CH_PER_UNI / CH_PER_LED);
  const px = Math.min(pixels, maxPixels);
  const channels = px * CH_PER_LED;
  document.getElementById('calc-leds').textContent = Math.min(leds, maxPixels * grp);
  document.getElementById('calc-channels').textContent = channels;
  document.getElementById('calc-universes').textContent = (channels / CH_PER_UNI).toFixed(1);
}

function getMeters() { return parseFloat(document.getElementById('meter-slider').value); }
function getBri() { return parseInt(document.getElementById('brightness').value); }

function showStatus(msg, ok) {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.className = ok ? 'ok' : '';
}

function post(path, data) {
  fetch('/' + path, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(data)})
    .then(r => r.json()).then(d => {
      if (d.leds !== undefined) showStatus(d.leds + ' LEDs gesendet · ' + d.unis + ' Universen', true);
    });
}

function doFill(r, g, b, w) {
  const bri = getBri();
  post('fill', {port: activePort, meters: getMeters(), r: Math.round(r*bri), g: Math.round(g*bri), b: Math.round(b*bri), w: Math.round(w*bri)});
}

function doFillAll() {
  const bri = getBri();
  post('fill', {port: activePort, meters: getMeters(), r: bri, g: bri, b: bri, w: bri});
}

function doGradient() {
  post('gradient', {port: activePort, meters: getMeters(), w: getBri()});
}

function doMark() {
  const m = parseFloat(document.getElementById('mark-meter').value) || 0;
  const bri = getBri();
  post('mark', {port: activePort, meter: m, w: bri});
}

// --- DMX Nodes ---
let dmxNodes = DMX_NODES_PLACEHOLDER;

function saveDmxNodes() {
  fetch('/set_dmx_nodes', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({nodes: dmxNodes})});
}

function renderDmxNodes() {
  const el = document.getElementById('dmx-nodes-list');
  if (!dmxNodes.length) {
    el.innerHTML = '<div style="color:#555;font-size:11px;margin-bottom:6px">Keine Nodes</div>';
    return;
  }
  el.innerHTML = dmxNodes.map((n, i) => `
    <div style="display:flex;align-items:center;gap:5px;margin-bottom:5px;padding:5px 7px;background:#1a1a2e;border-radius:6px;border:1px solid ${n.enabled ? '#e9a820' : '#2a2a3a'}">
      <input type="checkbox" ${n.enabled ? 'checked' : ''} onchange="toggleDmxNode(${i},this.checked)" style="width:14px;height:14px;accent-color:#e9a820;flex-shrink:0">
      <input value="${n.name}" placeholder="Name" onchange="updateDmxNode(${i},'name',this.value)"
        style="width:68px;background:#0d0d1a;border:1px solid #333;color:#f5e6c8;border-radius:3px;padding:2px 4px;font-size:11px">
      <input value="${n.ip}" placeholder="IP" onchange="updateDmxNode(${i},'ip',this.value)"
        style="width:98px;background:#0d0d1a;border:1px solid #333;color:#aaa;border-radius:3px;padding:2px 4px;font-size:11px;font-family:monospace">
      <span style="color:#444;font-size:10px">U</span>
      <input type="number" value="${n.universe}" min="0" max="32767" onchange="updateDmxNode(${i},'universe',+this.value)"
        style="width:38px;background:#0d0d1a;border:1px solid #333;color:#aaa;border-radius:3px;padding:2px 4px;font-size:11px;text-align:center">
      <span style="color:#444;font-size:10px">Ch</span>
      <input type="number" value="${n.start_ch}" min="1" max="509" onchange="updateDmxNode(${i},'start_ch',+this.value)"
        style="width:38px;background:#0d0d1a;border:1px solid #333;color:#aaa;border-radius:3px;padding:2px 4px;font-size:11px;text-align:center">
      <span style="cursor:pointer;color:#e94560;font-size:14px;margin-left:auto;padding:0 2px" onclick="removeDmxNode(${i})" title="Entfernen">×</span>
    </div>`).join('');
}

function toggleDmxNode(i, enabled) {
  dmxNodes[i].enabled = enabled;
  saveDmxNodes();
  renderDmxNodes();
}

function updateDmxNode(i, field, value) {
  dmxNodes[i][field] = value;
  saveDmxNodes();
}

function addDmxNode() {
  dmxNodes.push({name: 'Node ' + (dmxNodes.length + 1), ip: '192.168.0.', universe: 0, start_ch: 1, enabled: false});
  saveDmxNodes();
  renderDmxNodes();
}

function removeDmxNode(i) {
  dmxNodes.splice(i, 1);
  saveDmxNodes();
  renderDmxNodes();
}

// --- Lauflicht ---
let animColors = [{r:255,g:0,b:0,w:0},{r:0,g:255,b:0,w:0},{r:0,g:0,b:255,w:0}];
let animRunning = false;
let animMode = 'wipe';

function setAnimMode(m) {
  animMode = m;
  document.getElementById('mode-wipe').style.borderColor = m === 'wipe' ? '#e94560' : '#444';
  document.getElementById('mode-wipe').style.color = m === 'wipe' ? '#e94560' : '#fff';
  document.getElementById('mode-fade').style.borderColor = m === 'fade' ? '#00b4d8' : '#444';
  document.getElementById('mode-fade').style.color = m === 'fade' ? '#00b4d8' : '#fff';
}

function renderAnimColors() {
  const el = document.getElementById('anim-color-list');
  if (!animColors.length) {
    el.innerHTML = '<span style="color:#555;font-size:11px">Keine Farben – oben hinzufügen</span>';
    return;
  }
  el.innerHTML = animColors.map((c, i) => {
    const bg = `rgb(${Math.min(255,c.r+c.w)},${Math.min(255,c.g+c.w)},${Math.min(255,c.b+c.w)})`;
    return `<div style="width:32px;height:32px;background:${bg};border-radius:5px;border:1px solid #444;cursor:pointer;position:relative" onclick="removeAnimColor(${i})" title="R:${c.r} G:${c.g} B:${c.b} W:${c.w}&#10;Klick zum Entfernen">
      <span style="position:absolute;top:-1px;right:2px;font-size:9px;color:rgba(255,255,255,0.5)">${i+1}</span>
    </div>`;
  }).join('');
}

function addAnimPreset(r, g, b, w) {
  animColors.push({r, g, b, w});
  renderAnimColors();
}

function addAnimColor() {
  const r = +document.getElementById('ch-r').value;
  const g = +document.getElementById('ch-g').value;
  const b = +document.getElementById('ch-b').value;
  const w = +document.getElementById('ch-w').value;
  animColors.push({r, g, b, w});
  renderAnimColors();
}

function removeAnimColor(i) {
  animColors.splice(i, 1);
  renderAnimColors();
}

function clearAnimColors() {
  animColors = [];
  renderAnimColors();
}

function toggleAnimation() {
  animRunning ? stopAnimation() : startAnimation();
}

function startAnimation() {
  if (!animColors.length) { showStatus('Keine Farben definiert'); return; }
  const speed = parseFloat(document.getElementById('anim-speed').value);
  const bri = getBri() / 255;
  const scaled = animColors.map(c => ({
    r: Math.round(c.r * bri), g: Math.round(c.g * bri),
    b: Math.round(c.b * bri), w: Math.round(c.w * bri)
  }));
  fetch('/start_animation', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({port: activePort, meters: getMeters(), colors: scaled, seconds_per_fill: speed, mode: animMode})})
    .then(r => r.json()).then(() => {
      animRunning = true;
      const btn = document.getElementById('anim-btn');
      btn.textContent = '■ Stop';
      btn.style.background = '#555';
      showStatus('Lauflicht läuft', true);
    });
}

function stopAnimation() {
  fetch('/stop_animation', {method:'POST', headers:{'Content-Type':'application/json'}, body: '{}'})
    .then(r => r.json()).then(() => {
      animRunning = false;
      const btn = document.getElementById('anim-btn');
      btn.textContent = '▶ Start';
      btn.style.background = '#e94560';
      showStatus('Lauflicht gestoppt');
    });
}

function doPattern(name) {
  const c = getCustomColor();
  post('pattern', {port: activePort, meters: getMeters(), pattern: name, r: c.r, g: c.g, b: c.b, w: c.w});
}

// --- Strip → Port Mapping ---
async function loadStripMapping() {
  const d = await fetch('/strip_mapping').then(r => r.json());
  const list = document.getElementById('strip-map-list');
  const portOpts = d.ports.map((p, i) =>
    `<option value="${i}">${p.name} (Uni ${p.start_uni})</option>`
  ).join('');
  list.innerHTML = d.strips.map((name, idx) => {
    const cur = d.current[name];
    const sel = d.ports.map((p, i) =>
      `<option value="${i}" ${cur === i ? 'selected' : ''}>${p.name} (Uni ${p.start_uni})</option>`
    ).join('');
    return `<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
      <span style="flex:1;font-size:13px;color:#eee">${name}</span>
      <select data-strip="${name}" style="background:#0f172a;border:1px solid #334;color:#eee;border-radius:6px;padding:5px 8px;font-size:12px">
        <option value="">— kein —</option>
        ${sel}
      </select>
    </div>`;
  }).join('');
}

async function saveStrips() {
  const list = document.getElementById('strip-map-list');
  const selects = list.querySelectorAll('select');
  const body = {};
  selects.forEach(sel => {
    const name = sel.dataset.strip;
    if (sel.value !== '') body[name] = parseInt(sel.value);
  });
  const res = await fetch('/save_strips', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  }).then(r => r.json());
  const st = document.getElementById('strip-map-status');
  st.textContent = res.ok ? `✓ ${Object.keys(body).length} Strips gespeichert` : '✗ Fehler';
  st.style.color = res.ok ? '#2ecc71' : '#e74c3c';
  setTimeout(() => st.textContent = '', 3000);
}

loadStripMapping();

function doAllPorts(r, g, b, w) {
  fetch('/all_color', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({r,g,b,w})})
    .then(res => res.json()).then(() => showStatus('Alle Ports · R='+r+' G='+g+' B='+b+' W='+w, true));
}

function doBlackout() {
  post('blackout', {});
  showStatus('Blackout');
}

function rawTest(pattern, extra) {
  const data = {port: activePort, pattern: pattern, ...(extra||{})};
  fetch('/raw_test', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(data)});
  showStatus('Raw: ' + pattern, true);
}

function updateLedSlider(val) {
  const n = parseInt(val);
  const pixels = Math.ceil(n / grp);
  const channels = pixels * CH_PER_LED;
  document.getElementById('led-display').textContent = n;
  document.getElementById('led-meter').textContent = (n / LEDS_PER_M).toFixed(1);
  document.getElementById('led-ch').textContent = channels;
  document.getElementById('led-uni').textContent = (channels / CH_PER_UNI).toFixed(1);
  document.getElementById('led-byte').textContent = channels;
}

function doLedFill(r, g, b, w) {
  const n = parseInt(document.getElementById('led-slider').value);
  const bri = getBri();
  post('fill_leds', {port: activePort, num_leds: n, r: Math.round(r*bri), g: Math.round(g*bri), b: Math.round(b*bri), w: Math.round(w*bri)});
}

// Init
onColorChange();
renderAnimColors();
renderDmxNodes();
updateMeter(20);
updateLedSlider(1200);
</script>
</body>
</html>"""

_port_buttons = '\n    '.join(
    f'<button class="port-btn{"  active" if i == 0 else ""}" data-port="{i}" onclick="setPort({i})">'
    f'P{i+1}<span class="detail">Uni {p["start_uni"]}{"–"+str(p["start_uni"]+p["max_uni"]-1) if p["max_uni"]>1 else ""}</span></button>'
    for i, p in enumerate(PORTS)
)
_port_max_uni = '[' + ','.join(str(p['max_uni']) for p in PORTS) + ']'
HTML = (HTML
    .replace('PIXELATOR_IP', PIXELATOR_IP)
    .replace('PORT_BUTTONS_PLACEHOLDER', _port_buttons)
    .replace('PORT_MAX_UNI_PLACEHOLDER', _port_max_uni)
    .replace('DMX_NODES_PLACEHOLDER', json.dumps(dmx_nodes))
)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/strip_mapping":
            mapping = load_artnet_mapping()
            strip_names = load_argb_strips()
            existing = mapping.get("strip_mappings", {})
            # For each strip: find current port index by matching start_universe to PORTS
            uni_to_port = {p["start_uni"]: i for i, p in enumerate(PORTS)}
            current = {}
            for name in strip_names:
                m = existing.get(name, {})
                uni = m.get("start_universe", -1)
                current[name] = uni_to_port.get(uni, None)
            pixelator_ctrl = next((c for c in mapping.get("controllers", []) if c.get("type") == "pixelator"), None)
            body = json.dumps({
                "strips": strip_names,
                "current": current,
                "ports": PORTS,
                "pixelator_id": pixelator_ctrl["id"] if pixelator_ctrl else "",
            }, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(HTML.encode())

    def do_POST(self):
        global pixel_grouping
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        resp = {"ok": True}

        if self.path == "/fill":
            port_idx = body["port"]
            meters = body["meters"]
            num_leds = fill_meters(port_idx, meters, body["r"], body["g"], body["b"], body["w"])
            channels = num_leds * CHANNELS_PER_LED
            unis = (channels + CHANNELS_PER_UNIVERSE - 1) // CHANNELS_PER_UNIVERSE
            resp = {"ok": True, "leds": num_leds, "channels": channels, "unis": unis}

        elif self.path == "/gradient":
            port_idx = body["port"]
            meters = body["meters"]
            num_leds = fill_gradient(port_idx, meters, body["w"])
            channels = num_leds * CHANNELS_PER_LED
            unis = (channels + CHANNELS_PER_UNIVERSE - 1) // CHANNELS_PER_UNIVERSE
            resp = {"ok": True, "leds": num_leds, "channels": channels, "unis": unis}

        elif self.path == "/mark":
            port_idx = body["port"]
            meter = body["meter"]
            led_idx = mark_at_meter(port_idx, meter, 0, 0, 0, body["w"])
            resp = {"ok": True, "leds": 1, "led_idx": led_idx, "unis": 1}

        elif self.path == "/raw_test":
            # Send specific test patterns
            pattern = body["pattern"]
            port_idx = body["port"]
            if pattern == "single_bytes":
                # 1 byte ff, rest 00 — welche LED leuchtet?
                send_raw_test(port_idx, [0xff])
            elif pattern == "byte_positions":
                # ff at positions 0,1,2,3 — which colors appear?
                send_raw_test(port_idx, [0xff, 0x00, 0x00, 0x00])
            elif pattern == "color_id":
                # R, G, B, W each 10 pixels apart to identify byte order
                raw = [0] * 48
                raw[0] = 0xff   # byte 0 = ff
                raw[12] = 0xff  # byte 12 = ff
                raw[24] = 0xff  # byte 24 = ff
                raw[36] = 0xff  # byte 36 = ff
                send_raw_test(port_idx, raw)
            elif pattern == "every_nth":
                # ff on every Nth byte to count physical LEDs per byte
                n = body.get("n", 1)
                raw = []
                for i in range(600):
                    raw.append(0xff if i % n == 0 else 0x00)
                send_raw_test(port_idx, raw)
            elif pattern == "staircase":
                # Byte 0=ff, then 6 zeros, then ff, then 6 zeros...
                # If 1 byte = 6 LEDs, each "step" should be exactly 1 LED
                raw = []
                for i in range(20):
                    raw.append(0xff)
                    raw.extend([0x00] * 5)
                send_raw_test(port_idx, raw)

        elif self.path == "/fill_leds":
            port_idx = body["port"]
            num_leds = fill_n_leds(port_idx, body["num_leds"], body["r"], body["g"], body["b"], body["w"])
            channels = num_leds * CHANNELS_PER_LED
            unis = (channels + CHANNELS_PER_UNIVERSE - 1) // CHANNELS_PER_UNIVERSE
            resp = {"ok": True, "leds": num_leds, "channels": channels, "unis": unis}

        elif self.path == "/pattern":
            port_idx = body["port"]
            meters = body["meters"]
            num_leds = fill_pattern(port_idx, meters, body["pattern"],
                                    body.get("r", 255), body.get("g", 255),
                                    body.get("b", 255), body.get("w", 255))
            resp = {"ok": True, "leds": num_leds, "unis": 1}

        elif self.path == "/start_animation":
            global _anim_thread, _anim_running
            _anim_running = False
            if _anim_thread and _anim_thread.is_alive():
                _anim_thread.join(timeout=0.5)
            _anim_running = True
            fn = run_animation_fade if body.get('mode') == 'fade' else run_animation
            _anim_thread = threading.Thread(
                target=fn,
                args=(body['port'], body['meters'], body['colors'], body['seconds_per_fill']),
                daemon=True
            )
            _anim_thread.start()

        elif self.path == "/stop_animation":
            _anim_running = False

        elif self.path == "/set_dmx_nodes":
            global dmx_nodes
            dmx_nodes = body.get("nodes", dmx_nodes)
            enabled_count = sum(1 for n in dmx_nodes if n.get("enabled"))
            print(f"  → DMX Nodes: {len(dmx_nodes)} konfiguriert, {enabled_count} aktiv")

        elif self.path == "/set_grouping":
            pixel_grouping = body["grp"]
            print(f"  → Pixel-Grouping: 1:{pixel_grouping}")

        elif self.path == "/all_color":
            fill_all_ports(body["r"], body["g"], body["b"], body["w"])

        elif self.path == "/blackout":
            blackout_all()

        elif self.path == "/save_strips":
            # body: { "Waschbecke": port_idx, "Dusche": port_idx, ... }
            mapping = load_artnet_mapping()
            pixelator_ctrl = next((c for c in mapping.get("controllers", []) if c.get("type") == "pixelator"), None)
            ctrl_id = pixelator_ctrl["id"] if pixelator_ctrl else ""
            valid_names = set(load_argb_strips())
            for strip_name, port_idx in body.items():
                if strip_name not in valid_names:
                    continue
                port = PORTS[port_idx]
                sm = mapping.setdefault("strip_mappings", {}).setdefault(strip_name, {})
                sm["controller_id"] = ctrl_id
                sm["start_universe"] = port["start_uni"]
                sm["num_universes"] = port["max_uni"]
                sm["start_channel"] = sm.get("start_channel", 1)
                sm["pixel_grouping"] = sm.get("pixel_grouping", pixel_grouping)
            save_artnet_mapping(mapping)
            print(f"  → Strip-Mappings gespeichert: {list(body.keys())}")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(resp).encode())

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    max_leds = PORTS[0]["max_uni"] * CHANNELS_PER_UNIVERSE // CHANNELS_PER_LED
    max_meters = max_leds / LEDS_PER_METER
    print(f"Meter-Test GUI: http://127.0.0.1:{HTTP_PORT}")
    print(f"Pixelator MK2: {PIXELATOR_IP}:{ARTNET_PORT} · {len(PORTS)} Ports × 8 Uni")
    print(f"60 LED/m · 4 ch/LED · max {max_leds} LEDs = {max_meters:.1f}m pro Port")
    print("Ctrl+C → Blackout + Stop\n")
    threading.Timer(0.5, lambda: webbrowser.open(f"http://127.0.0.1:{HTTP_PORT}")).start()
    server = http.server.HTTPServer(("127.0.0.1", HTTP_PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        blackout_all()
        print("\nBlackout. Tschüss!")
    finally:
        server.server_close()
        sock.close()
