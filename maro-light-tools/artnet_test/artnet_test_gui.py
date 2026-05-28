"""
Art-Net Test GUI — Web-based.
Strip-Auswahl, Einzel-LED, Chase, Fill, Farben, Byte-Test.

Strip-Layout:
  Strip 1 = PLINK 1 (Port 1): 300 LEDs, Uni 0-2 (Data A+B mirrored)
  Strip 2 = PLINK 2 (Port 2): 300 LEDs, Uni 8-10 (Data A+B mirrored)
"""

import http.server
import json
import socket
import struct
import threading
import time
import webbrowser

PIXELATOR_IP = "192.168.178.10"
ARTNET_PORT = 6454
CHANNELS_PER_UNIVERSE = 512
LEDS_PER_STRIP = 600  # 2x 5m daisy-chained, 60 LED/m
HTTP_PORT = 8421

# 2 Strips: each PLINK = 600 LEDs daisy-chained
STRIPS = [
    {"name": "Strip 1", "label": "PLINK 1 (Port 1)", "start_uni": 0, "leds": 600},
    {"name": "Strip 2", "label": "PLINK 2 (Port 2)", "start_uni": 8, "leds": 600},
]

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
chase_thread = None
chase_stop = threading.Event()


def make_artdmx(universe, data):
    header = b"Art-Net\x00"
    opcode = struct.pack("<H", 0x5000)
    proto = struct.pack(">H", 14)
    seq = b"\x00"
    phys = b"\x00"
    univ = struct.pack("<H", universe)
    length = struct.pack(">H", len(data))
    return header + opcode + proto + seq + phys + univ + length + data


def send_raw_pixels(start_universe, pixels):
    """Send list of (r,g,b,w) tuples starting at given universe."""
    channels = []
    for r, g, b, w in pixels:
        channels.extend([r, g, b, w])
    offset = 0
    uni = start_universe
    while offset < len(channels):
        chunk = channels[offset:offset + CHANNELS_PER_UNIVERSE]
        data = bytes(chunk).ljust(CHANNELS_PER_UNIVERSE, b"\x00")
        pkt = make_artdmx(uni, data)
        sock.sendto(pkt, (PIXELATOR_IP, ARTNET_PORT))
        uni += 1
        offset += CHANNELS_PER_UNIVERSE


def send_strip_pixels(strip, pixels):
    """Send 600 pixels to a strip as one continuous block."""
    send_raw_pixels(strip["start_uni"], pixels)


def fill_strip(strip, r, g, b, w):
    """Fill entire strip with one color."""
    pixels = [(r, g, b, w)] * strip["leds"]
    send_raw_pixels(strip["start_uni"], pixels)


def blackout():
    for s in STRIPS:
        fill_strip(s, 0, 0, 0, 0)


def get_strip_indices(strip_idx):
    """Return list of strip indices based on selection (0/1 or -1 for all)."""
    if strip_idx == -1:
        return list(range(len(STRIPS)))
    return [strip_idx]


def stop_chase():
    global chase_thread
    chase_stop.set()
    if chase_thread and chase_thread.is_alive():
        chase_thread.join(timeout=2)
    chase_thread = None


def run_chase(strip_indices, color, speed_ms):
    """Chase pattern on selected strips."""
    stop_chase()
    chase_stop.clear()

    def _chase():
        r, g, b, w = color
        delay = speed_ms / 1000.0
        num_leds = LEDS_PER_STRIP
        while not chase_stop.is_set():
            # Forward
            for i in range(num_leds):
                if chase_stop.is_set():
                    break
                for si in strip_indices:
                    pixels = [(0,0,0,0)] * num_leds
                    pixels[i] = (r, g, b, w)
                    if i > 0: pixels[i-1] = (r//3, g//3, b//3, w//3)
                    if i > 1: pixels[i-2] = (r//8, g//8, b//8, w//8)
                    send_strip_pixels(STRIPS[si], pixels)
                time.sleep(delay)
            # Backward
            for i in range(num_leds - 2, -1, -1):
                if chase_stop.is_set():
                    break
                for si in strip_indices:
                    pixels = [(0,0,0,0)] * num_leds
                    pixels[i] = (r, g, b, w)
                    if i < num_leds-1: pixels[i+1] = (r//3, g//3, b//3, w//3)
                    if i < num_leds-2: pixels[i+2] = (r//8, g//8, b//8, w//8)
                    send_strip_pixels(STRIPS[si], pixels)
                time.sleep(delay)
        # Clean up
        for si in strip_indices:
            fill_strip(STRIPS[si], 0, 0, 0, 0)

    global chase_thread
    chase_thread = threading.Thread(target=_chase, daemon=True)
    chase_thread.start()


HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>Art-Net Test</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #1a1a2e; color: #fff; font-family: -apple-system, system-ui, sans-serif;
    display: flex; flex-direction: column; align-items: center; padding: 16px 20px; min-height: 100vh;
  }
  h1 { font-size: 22px; margin-bottom: 2px; }
  .sub { color: #666; font-size: 13px; margin-bottom: 14px; }
  .section { margin-bottom: 14px; text-align: center; width: 100%; max-width: 560px; }
  .section h2 { font-size: 13px; color: #888; margin-bottom: 6px; font-weight: 500; text-transform: uppercase; letter-spacing: 1px; }
  .row { display: flex; gap: 6px; justify-content: center; flex-wrap: wrap; }
  .strip-btn {
    padding: 8px 16px; border: 2px solid #444; border-radius: 6px; background: #222;
    color: #fff; font-size: 13px; cursor: pointer; transition: all 0.15s;
  }
  .strip-btn.active { border-color: #00b4d8; background: #0a2a3a; color: #00b4d8; }
  .strip-btn:hover { border-color: #666; }
  .strip-btn .uni { color: #555; font-size: 10px; display: block; }
  .strip-btn.active .uni { color: #0088a8; }
  .color-btn {
    width: 80px; height: 42px; border: none; border-radius: 8px; cursor: pointer;
    font-size: 12px; font-weight: 600; transition: transform 0.1s, box-shadow 0.15s;
    box-shadow: 0 2px 6px rgba(0,0,0,0.3);
  }
  .color-btn:hover { transform: scale(1.05); }
  .color-btn:active { transform: scale(0.97); }
  .action-btn {
    padding: 8px 16px; border: 2px solid #555; border-radius: 8px;
    background: #222; color: #fff; font-size: 13px; font-weight: 600;
    cursor: pointer; transition: all 0.15s;
  }
  .action-btn:hover { border-color: #888; background: #333; }
  .action-btn:active { transform: scale(0.97); }
  .action-btn.running { border-color: #e94560; color: #e94560; }
  .blackout-btn {
    padding: 10px 32px; border: 2px solid #555; border-radius: 8px;
    background: #222; color: #ff4444; font-size: 15px; font-weight: 700;
    cursor: pointer; transition: all 0.15s; letter-spacing: 1px;
  }
  .blackout-btn:hover { border-color: #ff4444; background: #2a1515; }
  .slider-row { display: flex; align-items: center; gap: 10px; justify-content: center; }
  .slider-row label { font-size: 12px; color: #888; min-width: 70px; text-align: right; }
  .slider-row input[type=range] { width: 180px; accent-color: #00b4d8; }
  .slider-row .val { font-size: 13px; color: #00b4d8; min-width: 40px; }
  .led-input {
    width: 70px; padding: 6px 8px; border: 2px solid #444; border-radius: 6px;
    background: #222; color: #fff; font-size: 14px; text-align: center;
  }
  .led-input:focus { border-color: #00b4d8; outline: none; }
  #status { margin-top: 10px; color: #666; font-size: 12px; min-height: 18px; }
  #status.active { color: #00b4d8; }
  .divider { width: 100%; max-width: 560px; border-top: 1px solid #2a2a4a; margin: 4px 0 14px; }
</style>
</head>
<body>
  <h1>Art-Net Test</h1>
  <div class="sub">PIXELATOR_IP · 600 LEDs/Strip (daisy-chain)</div>

  <!-- Strip-Auswahl -->
  <div class="section">
    <h2>Strip</h2>
    <div class="row">
      <button class="strip-btn active" data-strip="0" onclick="setStrip(0)">Strip 1 — PLINK 1<span class="uni">600 LEDs · Uni 0-4</span></button>
      <button class="strip-btn" data-strip="1" onclick="setStrip(1)">Strip 2 — PLINK 2<span class="uni">600 LEDs · Uni 8-12</span></button>
      <button class="strip-btn" data-strip="-1" onclick="setStrip(-1)">Beide</button>
    </div>
  </div>

  <!-- Helligkeit -->
  <div class="section">
    <div class="slider-row">
      <label>Helligkeit</label>
      <input type="range" id="brightness" min="0" max="255" value="255" oninput="updateVal('brightness','bri-val')">
      <span class="val" id="bri-val">255</span>
    </div>
  </div>

  <div class="divider"></div>

  <!-- Fill -->
  <div class="section">
    <h2>Fill (ganzer Strip)</h2>
    <div class="row">
      <button class="color-btn" style="background:#e74c3c;color:#fff" onclick="fill(255,0,0,0)">Rot</button>
      <button class="color-btn" style="background:#2ecc71;color:#fff" onclick="fill(0,255,0,0)">Gruen</button>
      <button class="color-btn" style="background:#3498db;color:#fff" onclick="fill(0,0,255,0)">Blau</button>
      <button class="color-btn" style="background:#f5e6c8;color:#333" onclick="fill(0,0,0,255)">Warmweiss</button>
      <button class="color-btn" style="background:#fff;color:#333" onclick="fill(255,255,255,255)">Weiss</button>
    </div>
  </div>

  <div class="divider"></div>

  <!-- Einzel-LED -->
  <div class="section">
    <h2>Einzelne LED (1-600)</h2>
    <div class="row" style="align-items:center">
      <label style="font-size:13px;color:#aaa">LED Nr:</label>
      <input type="number" id="led-nr" class="led-input" min="1" max="600" value="1">
      <button class="color-btn" style="background:#e74c3c;color:#fff;width:60px;height:36px;font-size:11px" onclick="singleLed(255,0,0,0)">Rot</button>
      <button class="color-btn" style="background:#2ecc71;color:#fff;width:60px;height:36px;font-size:11px" onclick="singleLed(0,255,0,0)">Gruen</button>
      <button class="color-btn" style="background:#3498db;color:#fff;width:60px;height:36px;font-size:11px" onclick="singleLed(0,0,255,0)">Blau</button>
      <button class="color-btn" style="background:#f5e6c8;color:#333;width:60px;height:36px;font-size:11px" onclick="singleLed(0,0,0,255)">W</button>
    </div>
  </div>

  <div class="divider"></div>

  <!-- Chase -->
  <div class="section">
    <h2>Chase (LED 1-600)</h2>
    <div class="slider-row" style="margin-bottom:8px">
      <label>Speed (ms)</label>
      <input type="range" id="chase-speed" min="2" max="100" value="10" oninput="updateVal('chase-speed','speed-val')">
      <span class="val" id="speed-val">10</span>
    </div>
    <div class="row">
      <button class="action-btn" id="chase-btn" onclick="toggleChase()">Chase Start</button>
      <button class="color-btn" style="background:#e74c3c;color:#fff;width:50px;height:36px;font-size:11px" onclick="setChaseColor(255,0,0,0)">R</button>
      <button class="color-btn" style="background:#2ecc71;color:#fff;width:50px;height:36px;font-size:11px" onclick="setChaseColor(0,255,0,0)">G</button>
      <button class="color-btn" style="background:#3498db;color:#fff;width:50px;height:36px;font-size:11px" onclick="setChaseColor(0,0,255,0)">B</button>
      <button class="color-btn" style="background:#f5e6c8;color:#333;width:50px;height:36px;font-size:11px" onclick="setChaseColor(0,0,0,255)">W</button>
    </div>
  </div>

  <div class="divider"></div>

  <!-- Byte-Test -->
  <div class="section">
    <h2>Byte-Test (Kanal-Reihenfolge)</h2>
    <div class="row">
      <button class="action-btn" onclick="sendByte(1)">Byte 1</button>
      <button class="action-btn" onclick="sendByte(2)">Byte 2</button>
      <button class="action-btn" onclick="sendByte(3)">Byte 3</button>
      <button class="action-btn" onclick="sendByte(4)">Byte 4</button>
    </div>
  </div>

  <div class="divider"></div>

  <div class="section">
    <button class="blackout-btn" onclick="doBlackout()">BLACKOUT</button>
  </div>

  <div id="status">Bereit</div>

<script>
let activeStrip = 0;
let chaseRunning = false;
let chaseColor = [0, 0, 0, 255];

function setStrip(s) {
  activeStrip = s;
  document.querySelectorAll('.strip-btn').forEach(b => {
    b.classList.toggle('active', parseInt(b.dataset.strip) === s);
  });
  showStatus('Strip: ' + (s === -1 ? 'Beide' : (s + 1)));
}

function updateVal(sliderId, valId) {
  document.getElementById(valId).textContent = document.getElementById(sliderId).value;
}

function bri() { return parseInt(document.getElementById('brightness').value); }

function applyBri(r, g, b, w) {
  const m = bri() / 255;
  return [Math.round(r*m), Math.round(g*m), Math.round(b*m), Math.round(w*m)];
}

function showStatus(msg) {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.classList.add('active');
  setTimeout(() => el.classList.remove('active'), 2000);
}

function post(path, data) {
  fetch('/' + path, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(data)});
}

function fill(r, g, b, w) {
  stopChaseUI();
  const [cr,cg,cb,cw] = applyBri(r,g,b,w);
  post('fill', {strip: activeStrip, r:cr, g:cg, b:cb, w:cw});
  showStatus('Fill: R='+cr+' G='+cg+' B='+cb+' W='+cw);
}

function singleLed(r, g, b, w) {
  stopChaseUI();
  const nr = parseInt(document.getElementById('led-nr').value) || 1;
  const [cr,cg,cb,cw] = applyBri(r,g,b,w);
  post('single', {strip: activeStrip, led: nr, r:cr, g:cg, b:cb, w:cw});
  showStatus('LED ' + nr + ': R='+cr+' G='+cg+' B='+cb+' W='+cw);
}

function setChaseColor(r,g,b,w) { chaseColor = [r,g,b,w]; if (chaseRunning) startChase(); }

function toggleChase() {
  if (chaseRunning) { stopChase(); } else { startChase(); }
}

function startChase() {
  chaseRunning = true;
  document.getElementById('chase-btn').textContent = 'Chase Stop';
  document.getElementById('chase-btn').classList.add('running');
  const speed = parseInt(document.getElementById('chase-speed').value);
  const [cr,cg,cb,cw] = applyBri(...chaseColor);
  post('chase', {strip: activeStrip, r:cr, g:cg, b:cb, w:cw, speed: speed});
  showStatus('Chase laeuft...');
}

function stopChase() {
  chaseRunning = false;
  document.getElementById('chase-btn').textContent = 'Chase Start';
  document.getElementById('chase-btn').classList.remove('running');
  post('chase_stop', {});
}

function stopChaseUI() {
  if (chaseRunning) stopChase();
}

function sendByte(pos) {
  stopChaseUI();
  const vals = [0, 0, 0, 0];
  vals[pos - 1] = bri();
  post('fill', {strip: activeStrip, r:vals[0], g:vals[1], b:vals[2], w:vals[3]});
  showStatus('Byte ' + pos + ' = ' + bri() + ' — welche Farbe?');
}

function doBlackout() {
  stopChaseUI();
  post('blackout', {});
  showStatus('Blackout');
}
</script>
</body>
</html>""".replace('PIXELATOR_IP', PIXELATOR_IP)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(HTML.encode())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if self.path == "/fill":
            stop_chase()
            strips = get_strip_indices(body["strip"])
            for si in strips:
                fill_strip(STRIPS[si], body["r"], body["g"], body["b"], body["w"])

        elif self.path == "/single":
            stop_chase()
            led = max(1, min(600, body["led"])) - 1  # 0-indexed
            strips = get_strip_indices(body["strip"])
            for si in strips:
                pixels = [(0,0,0,0)] * STRIPS[si]["leds"]
                pixels[led] = (body["r"], body["g"], body["b"], body["w"])
                send_strip_pixels(STRIPS[si], pixels)

        elif self.path == "/chase":
            strips = get_strip_indices(body["strip"])
            color = (body["r"], body["g"], body["b"], body["w"])
            run_chase(strips, color, body.get("speed", 10))

        elif self.path == "/chase_stop":
            stop_chase()
            blackout()

        elif self.path == "/blackout":
            stop_chase()
            blackout()

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    server = http.server.HTTPServer(("127.0.0.1", HTTP_PORT), Handler)
    print(f"Art-Net Test GUI: http://127.0.0.1:{HTTP_PORT}")
    print(f"Pixelator: {PIXELATOR_IP}:{ARTNET_PORT}")
    print(f"Strip 1: PLINK 1, 600 LEDs, Uni 0-4 (daisy-chain)")
    print(f"Strip 2: PLINK 2, 600 LEDs, Uni 8-12 (daisy-chain)")
    print("Ctrl+C -> Blackout + Stop\n")
    threading.Timer(0.5, lambda: webbrowser.open(f"http://127.0.0.1:{HTTP_PORT}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        stop_chase()
        blackout()
        print("\nBlackout. Tschuess!")
    finally:
        server.server_close()
        sock.close()
