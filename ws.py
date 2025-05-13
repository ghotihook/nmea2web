import asyncio
import logging
import socket
import time
import math

import pynmea2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# ── 1) Cell definitions ─────────────────────────────────────────────────────
CELLS = {
    "a": {"top": "BSP (kt)",    "format": "%0.1f"},
    "b": {"top": "Target (kt)", "format": "%0.1f"},
    "c": {"top": "TWA",         "format": "%0.0f°"},
    "d": {"top": "HDG (mag)",   "format": "%0.0f°"},
}

# ── 2) EMA configuration ────────────────────────────────────────────────────
EMA_WINDOW = 5.0  # seconds time constant for smoothing

# Initialize EMA state and last‐update timestamps
ema_values = { key: 0.0 for key in CELLS }
last_ts    = { key: None for key in CELLS }

def update_ema(key: str, value: float):
    """Update the EMA for `key` given a new raw `value`."""
    now = time.time()
    prev = last_ts[key]
    if prev is None:
        # first sample → set EMA to the sample
        ema_values[key] = value
    else:
        dt = now - prev
        # α = 1 – exp(–dt/τ)
        alpha = 1 - math.exp(-dt / EMA_WINDOW)
        ema_values[key] = ema_values[key] + alpha * (value - ema_values[key])
    last_ts[key] = now

# ── 3) Appearance constants ─────────────────────────────────────────────────
PAGE_BG     = "rgb(20,32,48)"
CELL_BG     = "rgb(46,50,69)"
CELL_GAP    = 12  # px
CELL_RADIUS = 8   # px

# ── 4) Build the HTML page ────────────────────────────────────────────────
cells_html = ""
for key, cfg in CELLS.items():
    placeholder = cfg["format"] % 0
    cells_html += f'''
    <div class="cell" data-key="{key}">
      <div class="top-line">{cfg["top"]}</div>
      <div class="middle-line">{placeholder}</div>
    </div>'''

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Live Single-Column Dashboard (EMA)</title>
  <style>
    * {{ box-sizing: border-box; }}
    html, body {{
      margin: 0; width:100vw; height:100vh; overflow:hidden;
      background: {PAGE_BG};
      font-family: system-ui, -apple-system, BlinkMacSystemFont,
                   'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
    }}
    .grid {{
      display: grid;
      width:100%; height:100%;
      grid-template-rows: repeat({len(CELLS)}, minmax(0,1fr));
      gap: {CELL_GAP}px;
      padding: {CELL_GAP}px;
    }}
    .cell {{
      background: {CELL_BG};
      border-radius: {CELL_RADIUS}px;
      display: flex; flex-direction: column;
      align-items: center; justify-content: center;
      padding: 6px; color: #0f0; user-select: none;
    }}
    .top-line {{
      font-size: 2.5vw; text-align: center;
      margin: 4px 0; line-height: 1;
    }}
    .middle-line {{
      font-size: 15vh; max-height: 100%;
      text-align: center; line-height: 1;
      font-variant-numeric: tabular-nums;
      font-feature-settings: 'tnum'; font-weight: bold;
    }}
  </style>
</head>
<body>
  <div class="grid">
    {cells_html}
  </div>
  <script>
  (function() {{
    const cellMap = {{}};
    document.querySelectorAll('.cell').forEach(el => {{
      cellMap[el.dataset.key] = el;
    }});

    let ws;
    function connect() {{
      ws = new WebSocket("ws://" + location.host + "/ws");
      ws.addEventListener('open',  () => console.log("▶ WS connected"));
      ws.addEventListener('message', e => {{
        const [key, text] = e.data.split(':');
        const c = cellMap[key];
        if (c) c.querySelector('.middle-line').textContent = text;
      }});
      ws.addEventListener('close', () => {{
        console.log("✖ WS disconnected, retrying in 1s");
        setTimeout(connect,1000);
      }});
      ws.addEventListener('error', err => {{ console.warn("WS error:", err); ws.close(); }});
    }}
    connect();
  }})();
  </script>
</body>
</html>"""

# ── 5) FastAPI & WebSocket setup ───────────────────────────────────────────
app = FastAPI()
clients: list[WebSocket] = []

def broadcast(key: str, text: str):
    msg = f"{key}:{text}"
    for ws in clients.copy():
        asyncio.create_task(ws.send_text(msg))

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.append(ws)
    try:
        while True:
            await ws.receive_text()  # keep-alive
    except WebSocketDisconnect:
        clients.remove(ws)

@app.get("/")
async def get_page():
    return HTMLResponse(html)

# ── 6) UDP listener with simple routing & EMA update ───────────────────────
async def udp_listener():
    loop = asyncio.get_running_loop()

    class Proto(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr):
            raw = data.decode().strip()
            logging.info(f"UDP recv {raw!r} from {addr}")
            try:
                msg = pynmea2.parse(raw)
            except pynmea2.ParseError:
                return

            # Example mapping & smoothing:
            if isinstance(msg, pynmea2.types.talker.VHW):
                # BSP → 'a'
                bsp = float(msg.water_speed_knots)
                update_ema("a", bsp)
                val = ema_values["a"]
                broadcast("a", CELLS["a"]["format"] % val)
                # reuse bsp for 'b'
                update_ema("b", bsp)
                val = ema_values["b"]
                broadcast("b", CELLS["b"]["format"] % val)
                # heading → 'd'
                hdg = float(msg.heading_true)
                update_ema("d", hdg)
                val = ema_values["d"]
                broadcast("d", CELLS["d"]["format"] % val)

            elif isinstance(msg, pynmea2.types.talker.VTG):
                sog = float(msg.spd_over_grnd_kts)
                update_ema("c", sog)
                val = ema_values["c"]
                broadcast("c", CELLS["c"]["format"] % val)
                cog = float(msg.mag_track)
                update_ema("d", cog)
                val = ema_values["d"]
                broadcast("d", CELLS["d"]["format"] % val)

            # extend with more cases as needed...

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", 2002))
    await loop.create_datagram_endpoint(lambda: Proto(), sock=sock)

@app.on_event("startup")
async def startup():
    asyncio.create_task(udp_listener())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)