import asyncio
import logging
import socket

import pynmea2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# ── 1) Central cell configuration ──────────────────────────────────────────
# Defines span (1/2/4), top label, bottom label, format string for value
CELLS = {
    "a": {"span": 2, "top": "Water Speed",  "format": "%0.1fkn", "bottom": ""},
    "b": {"span": 2, "top": "True Heading", "format": "%0.0f°",  "bottom": ""},
    "c": {"span": 4, "top": "Mag Dir",      "format": "%0.0f°",  "bottom": ""},
    "d": {"span": 1, "top": "Lat",          "format": "%0.5f°",  "bottom": ""},
    "e": {"span": 1, "top": "Lon",          "format": "%0.5f°",  "bottom": ""},
    "f": {"span": 1, "top": "SOG",          "format": "%0.1fkn", "bottom": ""},
    "g": {"span": 1, "top": "COG",          "format": "%0.0f°",  "bottom": ""},
}

# ── 2) Layout: rows of cell keys ───────────────────────────────────────────
LAYOUT = [
    ["a", "b"],
    ["c"],
    ["d", "e", "f", "g"],
]

# ── 3) Appearance constants ─────────────────────────────────────────────────
PAGE_BG     = "rgb(20,32,48)"
CELL_BG     = "rgb(46,50,69)"
CELL_GAP    = 12  # px
CELL_RADIUS = 8   # px

# ── 4) Build initial HTML ───────────────────────────────────────────────────
cells_html = ""
for row in LAYOUT:
    for key in row:
        cfg = CELLS[key]
        placeholder = cfg["format"] % 0
        cells_html += f'''
        <div class="cell span-{cfg["span"]}" data-key="{key}">
          <div class="top-line">{cfg["top"]}</div>
          <div class="middle-line">{placeholder}</div>
          <div class="bottom-line">{cfg["bottom"]}</div>
        </div>'''

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

# ── 5) FastAPI app & WebSocket state ────────────────────────────────────────
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

# ── 6) UDP listener with simple if‐statements ───────────────────────────────
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

            # identify message type via if‐elif
            if isinstance(msg, pynmea2.types.talker.VHW):
                # update 'a' and 'b'
                text_a = CELLS["a"]["format"] % msg.water_speed_knots
                broadcast("a", text_a)
                text_b = CELLS["b"]["format"] % msg.heading_true
                broadcast("b", text_b)

            elif isinstance(msg, pynmea2.types.talker.MWD):
                text = CELLS["c"]["format"] % msg.direction_magnetic
                broadcast("c", text)

            elif isinstance(msg, pynmea2.types.talker.VTG):
                text_f = CELLS["f"]["format"] % msg.spd_over_grnd_kts
                broadcast("f", text_f)
                text_g = CELLS["g"]["format"] % msg.mag_track
                broadcast("g", text_g)

            # extend with more if/elif blocks as needed...

    # bind UDP socket on port 2002
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