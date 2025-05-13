import asyncio
import logging
import socket

import pynmea2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# ── 1) Grid layout ─────────────────────────────────────────────────────────
# Each tuple is (cell_key, span_units)
LAYOUT = [
    [("a", 2), ("b", 2)],
    [("c", 4)],
    [("d", 1), ("e", 1), ("f", 1), ("g", 1)],
]

# ── 2) Per-cell display config ─────────────────────────────────────────────
# Python %-format string per cell, e.g. "%0.1fkn", "%0.0f°"
CELL_DISPLAY = {
    "a": {"top": "Water Speed",  "format": "%0.1fkn", "bottom": ""},
    "b": {"top": "True Heading", "format": "%0.0f°",  "bottom": ""},
    "c": {"top": "Mag Dir",      "format": "%0.0f°",  "bottom": ""},
    "d": {"top": "Lat",          "format": "%0.5f°",  "bottom": ""},
    "e": {"top": "Lon",          "format": "%0.5f°",  "bottom": ""},
    "f": {"top": "SOG",          "format": "%0.1fkn", "bottom": ""},
    "g": {"top": "COG",          "format": "%0.0f°",  "bottom": ""},
}

# ── 3) Appearance constants ─────────────────────────────────────────────────
PAGE_BG     = "rgb(20,32,48)"
CELL_BG     = "rgb(46,50,69)"
CELL_GAP    = 12  # px
CELL_RADIUS = 8   # px

# ── 4) Build initial cells HTML ─────────────────────────────────────────────
cells_html = ""
for row in LAYOUT:
    for key, span in row:
        ui = CELL_DISPLAY[key]
        placeholder = ui["format"] % 0
        cells_html += f'''
        <div class="cell span-{span}" data-key="{key}">
          <div class="top-line">{ui["top"]}</div>
          <div class="middle-line">{placeholder}</div>
          <div class="bottom-line">{ui["bottom"]}</div>
        </div>'''

# ── 5) Full HTML template ───────────────────────────────────────────────────
html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Live Grid</title>
  <style>
    * {{ box-sizing: border-box; }}
    html, body {{
      margin: 0; width: 100vw; height: 100vh; overflow: hidden;
      background: {PAGE_BG};
      font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI',
                   Roboto, 'Helvetica Neue', Arial, sans-serif;
    }}
    .grid {{
      display: grid; width: 100%; height: 100%;
      grid-template-columns: repeat(4, minmax(0,1fr));
      grid-auto-rows:    minmax(0,1fr);
      gap: {CELL_GAP}px; padding: {CELL_GAP}px;
    }}
    .cell {{
      background: {CELL_BG}; border-radius: {CELL_RADIUS}px;
      display: flex; flex-direction: column;
      align-items: center; justify-content: center;
      padding: 6px; color: #0f0; user-select: none;
    }}
    .top-line, .bottom-line {{
      font-size: 2.5vw; text-align: center; margin: 4px 0; line-height: 1;
    }}
    .middle-line {{
      font-size: 5vw; text-align: center; line-height: 1;
      font-variant-numeric: tabular-nums;
      font-feature-settings: 'tnum'; font-weight: bold;
    }}
    .span-1 {{ grid-column: span 1; }}
    .span-2 {{ grid-column: span 2; }}
    .span-4 {{ grid-column: span 4; }}
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
    const ws = new WebSocket("ws://" + location.host + "/ws");
    ws.addEventListener('open',  () => console.log("▶ WS connected"));
    ws.addEventListener('close', () => console.log("✖ WS disconnected"));
    ws.addEventListener('message', e => {{
      const [key, text] = e.data.split(':');
      const c = cellMap[key];
      if (c) c.querySelector('.middle-line').textContent = text;
    }});
  }})();
  </script>
</body>
</html>"""

# ── 6) FastAPI app & WebSocket state ────────────────────────────────────────
app = FastAPI()
clients: list[WebSocket] = []

def broadcast(key: str, text: str):
    payload = f"{key}:{text}"
    for ws in clients.copy():
        asyncio.create_task(ws.send_text(payload))

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.append(ws)
    try:
        while True:
            await ws.receive_text()  # ignore
    except WebSocketDisconnect:
        clients.remove(ws)

@app.get("/")
async def get_page():
    return HTMLResponse(html)

# ── 7) NMEA handler functions ───────────────────────────────────────────────
def handle_vhw(msg):
    return [
        ("a", CELL_DISPLAY["a"]["format"] % msg.water_speed_knots),
        ("b", CELL_DISPLAY["b"]["format"] % msg.heading_true),
    ]

def handle_mwd(msg):
    return [
        ("c", CELL_DISPLAY["c"]["format"] % msg.direction_magnetic),
    ]

def handle_vtg(msg):
    return [
        ("f", CELL_DISPLAY["f"]["format"] % msg.spd_over_grnd_kts),
        ("g", CELL_DISPLAY["g"]["format"] % msg.mag_track),
    ]

# Map NMEA message types to handlers
HANDLERS = {
    pynmea2.types.talker.VHW: handle_vhw,
    pynmea2.types.talker.MWD: handle_mwd,
    pynmea2.types.talker.VTG: handle_vtg,
    # add more mappings here...
}

# ── 8) UDP listener using handler map ───────────────────────────────────────
async def udp_listener():
    loop = asyncio.get_running_loop()
    class Proto(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr):
            raw = data.decode().strip()
            logging.info(f"⚡️ UDP recv {raw!r} from {addr}")
            try:
                msg = pynmea2.parse(raw)
            except pynmea2.ParseError:
                return
            for msg_type, handler in HANDLERS.items():
                if isinstance(msg, msg_type):
                    for key, text in handler(msg):
                        broadcast(key, text)
                    break

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