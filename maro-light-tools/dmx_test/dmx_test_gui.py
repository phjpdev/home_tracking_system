"""
DMX Test GUI — Web-based.
ODE MK3 via Art-Net. Einzelne Kanäle durchklicken, Universe wählbar.
"""

import http.server
import json
import socket
import struct
import webbrowser
import threading

ODE_IP = "192.168.178.11"
ARTNET_PORT = 6454
HTTP_PORT = 8422
NUM_UNIVERSES = 4  # Universes 0–3 verfügbar

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def send_dmx(channels, universe=0):
    data = bytes(channels).ljust(512, b"\x00")
    header = b"Art-Net\x00"
    opcode = struct.pack("<H", 0x5000)
    proto = struct.pack(">H", 14)
    pkt = header + opcode + proto + b"\x00\x00" + struct.pack("<H", universe) + struct.pack(">H", len(data)) + data
    sock.sendto(pkt, (ODE_IP, ARTNET_PORT))


HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>DMX Test — ODE MK3</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #1a1a2e; color: #fff; font-family: -apple-system, system-ui, sans-serif;
    display: flex; flex-direction: column; align-items: center; padding: 16px; min-height: 100vh;
  }
  h1 { font-size: 20px; margin-bottom: 2px; }
  .sub { color: #666; font-size: 12px; margin-bottom: 10px; }
  .uni-row {
    display: flex; gap: 6px; margin-bottom: 12px; flex-wrap: wrap; justify-content: center;
  }
  .uni-btn {
    padding: 5px 16px; border: 2px solid #2a2a4a; border-radius: 6px;
    background: #1e1e38; color: #666; font-size: 13px; font-weight: 700; cursor: pointer;
    transition: all 0.1s;
  }
  .uni-btn:hover { border-color: #555; color: #aaa; }
  .uni-btn.active { border-color: #00b4d8; background: #0a2a3a; color: #00b4d8; }
  .controls {
    display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
    justify-content: center; margin-bottom: 12px; width: 100%; max-width: 800px;
  }
  .slider-row { display: flex; align-items: center; gap: 8px; }
  .slider-row label { font-size: 12px; color: #888; }
  .slider-row input[type=range] { width: 160px; accent-color: #00b4d8; }
  .slider-row .val { font-size: 13px; color: #00b4d8; min-width: 32px; }
  .jump-row { display: flex; align-items: center; gap: 6px; }
  .jump-row label { font-size: 12px; color: #888; }
  .jump-row input[type=number] {
    width: 64px; background: #222; border: 1px solid #444; border-radius: 4px;
    color: #fff; font-size: 13px; padding: 4px 6px; text-align: center;
  }
  .jump-row button {
    padding: 4px 10px; border: 1px solid #555; border-radius: 4px;
    background: #222; color: #aaa; font-size: 12px; cursor: pointer;
  }
  .jump-row button:hover { border-color: #00b4d8; color: #00b4d8; }
  .solo-toggle {
    display: flex; align-items: center; gap: 6px; font-size: 12px; color: #888; cursor: pointer;
  }
  .solo-toggle input { accent-color: #e94560; width: 15px; height: 15px; cursor: pointer; }
  .solo-toggle.active { color: #e94560; }
  .ch-grid {
    display: grid; grid-template-columns: repeat(16, 1fr); gap: 3px;
    width: 100%; max-width: 800px; max-height: 60vh; overflow-y: auto;
    border: 1px solid #2a2a4a; border-radius: 6px; padding: 6px;
    background: #12121e;
  }
  .ch-btn {
    padding: 7px 0; border: 1px solid #2a2a4a; border-radius: 4px; background: #1e1e38;
    color: #666; font-size: 11px; font-weight: 600; cursor: pointer; transition: all 0.08s;
    line-height: 1.2; text-align: center;
  }
  .ch-btn:hover { border-color: #555; color: #fff; background: #2a2a4a; }
  .ch-btn.on { border-color: #00b4d8; background: #0a2a3a; color: #00b4d8; }
  .ch-btn.solo-on { border-color: #e94560; background: #2a0a14; color: #e94560; }
  .ch-btn.highlight { outline: 2px solid #f4a261; }
  .bottom { display: flex; gap: 12px; align-items: center; margin-top: 12px; flex-wrap: wrap; justify-content: center; }
  .blackout-btn {
    padding: 10px 32px; border: 2px solid #555; border-radius: 8px;
    background: #222; color: #ff4444; font-size: 15px; font-weight: 700;
    cursor: pointer; transition: all 0.15s; letter-spacing: 1px;
  }
  .blackout-btn:hover { border-color: #ff4444; background: #2a1515; }
  .all-btn {
    padding: 10px 20px; border: 2px solid #555; border-radius: 8px;
    background: #222; color: #aaa; font-size: 13px; font-weight: 600;
    cursor: pointer; transition: all 0.15s;
  }
  .all-btn:hover { border-color: #888; color: #fff; background: #333; }
  #status { margin-top: 10px; color: #666; font-size: 12px; min-height: 18px; }
  #status.active { color: #00b4d8; }
</style>
</head>
<body>
  <h1>DMX Test — ODE MK3</h1>
  <div class="sub" id="sub-info">ODE_IP · Universe 0 · 512 Kanäle</div>

  <div class="uni-row" id="uni-row"></div>

  <div class="controls">
    <div class="slider-row">
      <label>Wert</label>
      <input type="range" id="value" min="0" max="255" value="255"
             oninput="document.getElementById('val-display').textContent=this.value">
      <span class="val" id="val-display">255</span>
    </div>
    <div class="jump-row">
      <label>Ch</label>
      <input type="number" id="jump-ch" min="1" max="512" placeholder="1–512">
      <button onclick="jumpTo()">→</button>
    </div>
    <label class="solo-toggle" id="solo-label">
      <input type="checkbox" id="solo-mode" onchange="updateSoloLabel()">
      Solo-Modus
    </label>
  </div>

  <div class="controls" style="margin-bottom:10px">
    <div class="jump-row">
      <label>Bereich</label>
      <input type="number" id="range-from" min="1" max="512" placeholder="von" style="width:56px">
      <span style="color:#555">–</span>
      <input type="number" id="range-to" min="1" max="512" placeholder="bis" style="width:56px">
      <button onclick="doRange(true)" style="background:#0a2a3a;border-color:#00b4d8;color:#00b4d8">An</button>
      <button onclick="doRange(false)">Aus</button>
    </div>
  </div>

  <div class="ch-grid" id="ch-grid"></div>

  <div class="bottom">
    <button class="all-btn" onclick="doAllOn()">Alle An</button>
    <button class="blackout-btn" onclick="doBlackout()">BLACKOUT</button>
    <button class="all-btn" onclick="doBlackoutAll()" style="color:#ff8888;border-color:#663333">Alle Uni Aus</button>
  </div>

  <div id="status">Bereit</div>

<script>
const NUM_UNIVERSES = NUM_UNI_PH;
// Separate DMX state per universe
const dmxState = Array.from({length: NUM_UNIVERSES}, () => new Array(512).fill(0));
let currentUni = 0;

// Universe selector buttons
const uniRow = document.getElementById('uni-row');
for (let u = 0; u < NUM_UNIVERSES; u++) {
  const btn = document.createElement('button');
  btn.className = 'uni-btn' + (u === 0 ? ' active' : '');
  btn.textContent = 'Universe ' + u;
  btn.id = 'uni-btn-' + u;
  btn.onclick = () => switchUniverse(u);
  uniRow.appendChild(btn);
}

// Channel grid
const grid = document.getElementById('ch-grid');
for (let i = 1; i <= 512; i++) {
  const btn = document.createElement('button');
  btn.className = 'ch-btn';
  btn.textContent = i;
  btn.id = 'ch' + i;
  btn.dataset.ch = i;
  btn.onclick = () => clickCh(i);
  grid.appendChild(btn);
}

function switchUniverse(u) {
  currentUni = u;
  document.querySelectorAll('.uni-btn').forEach((b, i) => b.classList.toggle('active', i === u));
  document.getElementById('sub-info').textContent = `ODE_IP · Universe ${u} · 512 Kanäle`;
  // Refresh grid to show state of new universe
  const d = dmxState[u];
  for (let i = 1; i <= 512; i++) {
    const btn = document.getElementById('ch' + i);
    btn.classList.remove('on', 'solo-on');
    if (d[i-1] > 0) btn.classList.add('on');
  }
  showStatus('Universe ' + u);
}

function getVal() { return parseInt(document.getElementById('value').value); }
function isSolo() { return document.getElementById('solo-mode').checked; }

function updateSoloLabel() {
  document.getElementById('solo-label').classList.toggle('active', isSolo());
}

function clickCh(ch) {
  const d = dmxState[currentUni];
  if (isSolo()) {
    const wasOn = d[ch-1] > 0;
    d.fill(0);
    document.querySelectorAll('.ch-btn').forEach(b => b.classList.remove('on', 'solo-on'));
    if (!wasOn) {
      d[ch-1] = getVal();
      document.getElementById('ch'+ch).classList.add('solo-on');
      showStatus('Solo Ch' + ch + ' = ' + d[ch-1] + ' (Uni ' + currentUni + ')');
    } else {
      showStatus('Blackout Uni ' + currentUni);
    }
  } else {
    const btn = document.getElementById('ch'+ch);
    if (d[ch-1] > 0) {
      d[ch-1] = 0;
      btn.classList.remove('on');
    } else {
      d[ch-1] = getVal();
      btn.classList.add('on');
    }
    showStatus('Uni ' + currentUni + ' Ch' + ch + ' = ' + d[ch-1]);
  }
  sendDmx();
}

function sendDmx() {
  const d = dmxState[currentUni];
  fetch('/dmx', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({channels: Array.from(d), universe: currentUni})});
}

function doBlackout() {
  const d = dmxState[currentUni];
  d.fill(0);
  document.querySelectorAll('.ch-btn').forEach(b => b.classList.remove('on', 'solo-on'));
  sendDmx();
  showStatus('Blackout Uni ' + currentUni);
}

function doBlackoutAll() {
  for (let u = 0; u < NUM_UNIVERSES; u++) {
    dmxState[u].fill(0);
    fetch('/dmx', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({channels: new Array(512).fill(0), universe: u})});
  }
  document.querySelectorAll('.ch-btn').forEach(b => b.classList.remove('on', 'solo-on'));
  showStatus('Blackout alle Universen');
}

function doAllOn() {
  const v = getVal();
  const d = dmxState[currentUni];
  d.fill(v);
  document.querySelectorAll('.ch-btn').forEach(b => { b.classList.add('on'); b.classList.remove('solo-on'); });
  sendDmx();
  showStatus('Alle Kanäle Uni ' + currentUni + ' = ' + v);
}

function doRange(on) {
  const from = parseInt(document.getElementById('range-from').value);
  const to   = parseInt(document.getElementById('range-to').value);
  if (isNaN(from) || isNaN(to) || from < 1 || to > 512 || from > to) {
    showStatus('Ungültiger Bereich'); return;
  }
  const v = on ? getVal() : 0;
  const d = dmxState[currentUni];
  for (let ch = from; ch <= to; ch++) {
    d[ch-1] = v;
    const btn = document.getElementById('ch'+ch);
    btn.classList.toggle('on', v > 0);
    btn.classList.remove('solo-on');
  }
  sendDmx();
  showStatus(`Uni ${currentUni} Ch ${from}–${to} = ${v}`);
}

function jumpTo() {
  const ch = parseInt(document.getElementById('jump-ch').value);
  if (ch >= 1 && ch <= 512) {
    const btn = document.getElementById('ch'+ch);
    btn.scrollIntoView({behavior:'smooth', block:'center'});
    btn.classList.add('highlight');
    setTimeout(() => btn.classList.remove('highlight'), 1500);
  }
}

function showStatus(msg) {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.classList.add('active');
  setTimeout(() => el.classList.remove('active'), 2000);
}
</script>
</body>
</html>""".replace('ODE_IP', ODE_IP).replace('NUM_UNI_PH', str(NUM_UNIVERSES))


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(HTML.encode())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if self.path == "/dmx":
            channels = body.get("channels", [0]*512)
            universe = int(body.get("universe", 0))
            send_dmx(channels, universe)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    # Blackout alle Universen beim Start
    for u in range(NUM_UNIVERSES):
        send_dmx([0]*512, u)
    import socket as _socket
    local_ip = _socket.gethostbyname(_socket.gethostname())
    server = http.server.HTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    print(f"DMX Test GUI: http://127.0.0.1:{HTTP_PORT}")
    print(f"Im Netz: http://{local_ip}:{HTTP_PORT}")
    print(f"ODE MK3: {ODE_IP}:{ARTNET_PORT}, Universe 0–{NUM_UNIVERSES-1}")
    print("Ctrl+C -> Blackout + Stop\n")
    threading.Timer(0.5, lambda: webbrowser.open(f"http://127.0.0.1:{HTTP_PORT}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        for u in range(NUM_UNIVERSES):
            send_dmx([0]*512, u)
        print("\nBlackout. Tschuess!")
    finally:
        server.server_close()
        sock.close()
