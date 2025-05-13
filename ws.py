import asyncio
import logging
import socket

import pynmea2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# ── 1) Grid Layout ─────────────────────────────────────────────────────────
# Each tuple is (cell_key, span_units)
LAYOUT = [
    [("a", 2), ("b", 2)],
    [("c", 4)],
    [("d", 1), ("e", 1), ("f", 1), ("g", 1)],
]

# ── 2) Per-cell display config ─────────────────────────────────────────────
# You can adjust the Python %-format string per cell.
CELL_DISPLAY = {
    "a": {"top": "Water Speed",  "format": "%0.1fkn", "bottom": ""},
    "b": {"top": "True Heading", "format": "%0.0f°",  "bottom": ""},
    "c": {"top": "Mag Dir",      "format": "%0.0f°",  "bottom": ""},
    "d": {"top": "Lat",          "format": "%0.5f",   "bottom": "°"},
    "e": {"top": "Lon",          "format": "%0.5f",   "bottom": "°"},
    "f": {"top": "SOG",          "format": "%0.1fkn", "bottom": ""},
    "g": {"top": "COG",          "format": "%0.0f°",  "bottom": ""},
}

PAGE_BG     = "rgb(20,32,48)"
CELL_BG     = "rgb(46,50,69)"
CELL_GAP    = 12  # px
CELL_RADIUS = 8   # px

# ── 3) Build initial HTML ──────────────────────────────────────────────────
cells_html = ""
for row in LAYOUT:
    for key, span in row:
        ui = CELL_DISPLAY[key]
        # Placeholder using format % 0
        placeholder = ui["format"] % 0
        cells_html += f'''
        <div class="cell span-{span}" data-key="{key}">
          <div class="top-line">{ui["top"]}</div>
          <div class="middle-line">{placeholder}</div>
          <div class="bottom-line">{ui["bottom"]}</div>
        </div>'''

html = f"""
<!DOCTYPE html>
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
      font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
    }}
    .grid {{
      display: grid;
      width: 100%; height: 100%;
      grid-template-columns: repeat(4, minmax(0,1fr));
      grid-auto-rows:    minmax(0,1fr);
      gap: {CELL_GAP}px; padding: {CELL_GAP}px;
    }}
    .cell {{
      background: {CELL_BG};
      border-radius: {CELL_RADIUS}px;
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
    ws.addEventListener('open', () => console.log("WS connected"));
    ws.addEventListener('close', () => console.log("WS disconnected"));
    ws.addEventListener('message', e => {{
      const [key, text] = e.data.split(':');
      const c = cellMap[key];
      if (c) c.querySelector('.middle-line').textContent = text;
    }});
  }})();
  </script>
</body>
</html>
"""

# ── 4) FastAPI & WebSocket state ───────────────────────────────────────────
app = FastAPI()
clients: list[WebSocket] = []

def broadcast(key: str, text: str):
    payload = f"{key}:{text}"
    for ws in clients.copy():
        # fire‐and‐forget
        asyncio.create_task(ws.send_text(payload))

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

# ── 5) UDP listener & direct if/elif parsing ───────────────────────────────
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

            # Now use plain if/elif on msg types:
            if isinstance(msg, pynmea2.types.talker.VHW):
                # VHW gives water_speed_knots → cell 'a'
                text_a = CELL_DISPLAY["a"]["format"] % msg.water_speed_knots
                broadcast("a", text_a)
                # VHW.heading_true → cell 'b'
                text_b = CELL_DISPLAY["b"]["format"] % msg.heading_true
                broadcast("b", text_b)

            elif isinstance(msg, pynmea2.types.talker.MWD):
                # MWD direction_magnetic → cell 'c'
                text = CELL_DISPLAY["c"]["format"] % msg.direction_magnetic
                broadcast("c", text)

            elif isinstance(msg, pynmea2.types.talker.VTG):
                # VTG.spd_over_grnd_kts → cell 'f'
                text_f = CELL_DISPLAY["f"]["format"] % msg.spd_over_grnd_kts
                broadcast("f", text_f)
                # VTG.mag_track → cell 'g'
                text_g = CELL_DISPLAY["g"]["format"] % msg.mag_track
                broadcast("g", text_g)

            # …and so on for each sentence type you care about:
            # elif isinstance(msg, pynmea2.types.talker.RMC): …
            # elif isinstance(msg, pynmea2.types.talker.GLL): …

    # bind UDP on port 2002
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", 2002))
    await loop.create_datagram_endpoint(lambda: Proto(), sock=sock)

@app.on_event("startup")
async def on_startup():
    asyncio.create_task(udp_listener())

# ── Entrypoint ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)