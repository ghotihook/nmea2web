#Comment
import asyncio
import logging
import socket

import pynmea2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# ── Layout & NMEA Mapping ───────────────────────────────────────────────────
LAYOUT = [
    [("a", 2), ("b", 2)],
    [("c", 4)],
    [("d", 1), ("e", 1), ("f", 1), ("g", 1)],
]

CELL_NMEA_CONFIG = {
    "a": ("VHW", "water_speed_knots"),
    "b": ("VHW", "heading_true"),
    # add c–g here...
}
NMEA_TO_CELLS = {}
for key, (stype, attr) in CELL_NMEA_CONFIG.items():
    NMEA_TO_CELLS.setdefault(stype, []).append((key, attr))

CELL_DISPLAY = {
    "a": {"top": "Water Speed",  "unit": "kn", "bottom": ""},
    "b": {"top": "True Heading", "unit": "°T","bottom": ""},
    "c": {"top": "Cell C",       "unit": "",   "bottom": ""},
    "d": {"top": "Cell D",       "unit": "",   "bottom": ""},
    "e": {"top": "Cell E",       "unit": "",   "bottom": ""},
    "f": {"top": "Cell F",       "unit": "",   "bottom": ""},
    "g": {"top": "Cell G",       "unit": "",   "bottom": ""},
}

# ── Appearance ─────────────────────────────────────────────────────────────
PAGE_BG     = "rgb(20,32,48)"
CELL_BG     = "rgb(46,50,69)"
CELL_GAP    = 12   # px
CELL_RADIUS = 8    # px

# ── Build the cells HTML ────────────────────────────────────────────────────
cells_html = ""
for row in LAYOUT:
    for key, span in row:
        ui = CELL_DISPLAY[key]
        # initial placeholder will be overwritten by WebSocket
        placeholder = f"–{ui['unit']}"
        cells_html += f'''
        <div class="cell span-{span}" data-key="{key}">
          <div class="top-line">{ui["top"]}</div>
          <div class="middle-line">{placeholder}</div>
          <div class="bottom-line">{ui["bottom"]}</div>
        </div>'''

# ── Full HTML Template ─────────────────────────────────────────────────────
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
      margin: 0;
      width: 100vw;
      height: 100vh;
      overflow: hidden;
      background: {PAGE_BG};
      font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI',
                   Roboto, 'Helvetica Neue', Arial, sans-serif;
    }}

    .grid {{
      display: grid;
      width: 100%;
      height: 100%;
      grid-template-columns: repeat(4, minmax(0,1fr));
      grid-auto-rows: minmax(0,1fr);
      gap: {CELL_GAP}px;
      padding: {CELL_GAP}px;
    }}

    .cell {{
      background: {CELL_BG};
      border-radius: {CELL_RADIUS}px;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 6px;
      color: #0f0;
      user-select: none;
    }}

    .top-line, .bottom-line {{
      font-size: 2.5vw;
      text-align: center;
      margin: 4px 0;
      line-height: 1;
    }}

    .middle-line {{
      font-size: 5vw;
      text-align: center;
      line-height: 1;
      font-variant-numeric: tabular-nums;
      font-feature-settings: 'tnum';
      font-weight: bold;
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
      const cell = cellMap[key];
      if (cell) {{
        cell.querySelector('.middle-line').textContent = text;
      }}
    }});
  }})();
  </script>
</body>
</html>
"""

# ── FastAPI App ─────────────────────────────────────────────────────────────
app = FastAPI()
clients: list[WebSocket] = []

@app.get("/")
async def get_page():
    return HTMLResponse(html)

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.append(ws)
    try:
        while True:
            await ws.receive_text()  # keep-alive
    except WebSocketDisconnect:
        clients.remove(ws)

# ── UDP → WebSocket Bridge ─────────────────────────────────────────────────
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

            for key, attr in NMEA_TO_CELLS.get(msg.sentence_type, []):
                val = getattr(msg, attr, None)
                if val is None:
                    continue
                unit = CELL_DISPLAY[key]["unit"]
                # simple Python formatting: value + unit
                payload = f"{key}:{val}{unit}"
                for ws in clients.copy():
                    asyncio.create_task(ws.send_text(payload))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", 2002))
    await loop.create_datagram_endpoint(lambda: Proto(), sock=sock)

@app.on_event("startup")
async def on_startup():
    asyncio.create_task(udp_listener())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)