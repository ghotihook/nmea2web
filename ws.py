import asyncio
import logging
import socket

import pynmea2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# ── Layout (4-column grid) ─────────────────────────────────────────────────
LAYOUT = [
    [("a", 2), ("b", 2)],
    [("c", 4)],
    [("d", 1), ("e", 1), ("f", 1), ("g", 1)],
]

# ── NMEA → Cell mapping ─────────────────────────────────────────────────────
CELL_NMEA_CONFIG = {
    "a": ("VHW", "water_speed_knots"),
    "b": ("VHW", "heading_true"),
    # …add mappings for c–g here…
}
NMEA_TO_CELLS = {}
for key, (stype, attr) in CELL_NMEA_CONFIG.items():
    NMEA_TO_CELLS.setdefault(stype, []).append((key, attr))

# ── Static labels/units/bottom text ────────────────────────────────────────
CELL_DISPLAY = {
    "a": {"top": "Water Speed",  "unit": "kn", "bottom": ""},
    "b": {"top": "True Heading", "unit": "°T","bottom": ""},
    "c": {"top": "Cell C",       "unit": "",   "bottom": ""},
    "d": {"top": "Cell D",       "unit": "",   "bottom": ""},
    "e": {"top": "Cell E",       "unit": "",   "bottom": ""},
    "f": {"top": "Cell F",       "unit": "",   "bottom": ""},
    "g": {"top": "Cell G",       "unit": "",   "bottom": ""},
}

# ── Appearance & Padding ───────────────────────────────────────────────────
PAGE_BG      = "rgb(20,32,48)"
CELL_BG      = "rgb(46,50,69)"
CELL_GAP     = 12      # px
CELL_RADIUS  = 8       # px
MIDDLE_WIDTH = 11      # characters wide for padded value+unit

# ── Build the cell HTML ─────────────────────────────────────────────────────
cells_html = ""
for row in LAYOUT:
    for key, span in row:
        ui = CELL_DISPLAY[key]
        placeholder = " " * MIDDLE_WIDTH
        cells_html += f'''
        <div class="cell span-{span}" data-key="{key}">
          <div class="top-line">{ui["top"]}</div>
          <div class="middle-line">{placeholder}</div>
          <div class="bottom-line">{ui["bottom"]}</div>
        </div>'''

# ── Full HTML with proper scaling ────────────────────────────────────────────
html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Live Grid</title>
  <style>
    :root {{
      --page-bg: {PAGE_BG};
      --cell-bg: {CELL_BG};
      --cell-gap: {CELL_GAP}px;
      --cell-radius: {CELL_RADIUS}px;
      --font-sans: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
    }}

    /* Fill the viewport exactly */
    html, body {{
      margin: 0;
      height: 100vh;
    }}
    body {{
      background: var(--page-bg);
      font-family: var(--font-sans);
    }}

    /* Grid container now holds the padding */
    .grid {{
      display: grid;
      grid-template-columns: repeat(4,1fr);
      grid-auto-rows: 1fr;
      gap: var(--cell-gap);
      padding: var(--cell-gap);
      box-sizing: border-box;
      height: 100%;
    }}

    .cell {{
      background: var(--cell-bg);
      border-radius: var(--cell-radius);
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
      font-family: monospace;
      white-space: pre;
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
    ws.addEventListener('message', ({{ data }}) => {{
      const [key, padded] = data.split(':');
      const cell = cellMap[key];
      if (cell) {{
        cell.querySelector('.middle-line').textContent = padded;
      }}
    }});
  }})();
  </script>
</body>
</html>
"""

# ── FastAPI app & endpoints ─────────────────────────────────────────────────
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
            await ws.receive_text()  # ignore keep-alive
    except WebSocketDisconnect:
        clients.remove(ws)

# ── UDP → WebSocket bridge ──────────────────────────────────────────────────
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
                core = f"{val}{unit}"

                if core.startswith("-"):
                    core += " "

                core = " " * len(unit) + core
                middle = core.center(MIDDLE_WIDTH)

                payload = f"{key}:{middle}"
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