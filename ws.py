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
    # add mappings for c–g as needed...
}
NMEA_TO_CELLS = {}
for key, (stype, attr) in CELL_NMEA_CONFIG.items():
    NMEA_TO_CELLS.setdefault(stype, []).append((key, attr))

# ── Cell static labels/units/bottom text ────────────────────────────────────
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
PAGE_BG        = "rgb(20,32,48)"
CELL_BG        = "rgb(46,50,69)"
CELL_GAP       = 12   # px
CELL_RADIUS    = 8    # px
MIDDLE_WIDTH   = 11   # characters in middle line, adjust to taste

# ── Build HTML ──────────────────────────────────────────────────────────────
cells_html = ""
for row in LAYOUT:
    for key, span in row:
        ui = CELL_DISPLAY[key]
        cells_html += f'''
        <div class="cell span-{span}" data-key="{key}">
          <div class="top-line">{ui["top"]}</div>
          <div class="middle-line">{' ' * MIDDLE_WIDTH}</div>
          <div class="bottom-line">{ui["bottom"]}</div>
        </div>'''

html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"/><title>Live Grid</title>
  <style>
    html, body {{ margin:0; padding:{CELL_GAP}px; height:100%; background:{PAGE_BG}; box-sizing:border-box; }}
    .grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:{CELL_GAP}px; height:100%; }}
    .cell {{
      background:{CELL_BG}; border-radius:{CELL_RADIUS}px;
      display:flex; flex-direction:column; justify-content:space-between;
      padding:4px; color:#0f0; user-select:none;
    }}
    .top-line, .bottom-line {{
      text-align:center; font-size:2.5vw; line-height:1; margin:2px 0;
    }}
    .middle-line {{
      text-align:center; 
      font-family:monospace;     /* ensure fixed-width spaces */
      white-space:pre;           /* preserve spaces */
      font-size:5vw; line-height:1; 
      font-variant-numeric:tabular-nums;
      font-feature-settings:'tnum'; font-weight:bold;
    }}
    .span-1 {{ grid-column:span 1; }}
    .span-2 {{ grid-column:span 2; }}
    .span-4 {{ grid-column:span 4; }}
  </style>
</head>
<body>
  <div class="grid">{cells_html}</div>
  <script>
    const cellMap = Object.fromEntries(
      Array.from(document.querySelectorAll('.cell')).map(el => [el.dataset.key, el])
    );
    const ws = new WebSocket("ws://" + location.host + "/ws");
    ws.onopen    = () => console.log("▶ WS connected");
    ws.onclose   = () => console.log("✖ WS disconnected");
    ws.onmessage = e => {{
      const [key, raw] = e.data.split(':');
      const cell = cellMap[key];
      if (!cell) return;
      // compute padded middle string in JS as fallback
      const unit = "{{}"};  // not used; Python padding includes unit
      const padded = raw.padStart(({MIDDLE_WIDTH} + raw.length) / 2).padEnd({MIDDLE_WIDTH});
      cell.querySelector('.middle-line').textContent = padded;
    }};
  </script>
</body>
</html>
"""

# ── FastAPI App ─────────────────────────────────────────────────────────────
app = FastAPI()
clients: list[WebSocket] = []

@app.get("/")
async def page():
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

# ── UDP → WebSocket Bridge (port 2002) ──────────────────────────────────────
async def udp_listener():
    loop = asyncio.get_running_loop()
    class Proto(asyncio.DatagramProtocol):
        def datagram_received(self, data, addr):
            raw = data.decode().strip()
            logging.info(f"⚡️ UDP recv {raw!r} from {addr}")
            try:
                msg = pynmea2.parse(raw)
            except pynmea2.ParseError:
                return
            for key, attr in NMEA_TO_CELLS.get(msg.sentence_type, []):
                val = getattr(msg, attr, None)
                if val is not None:
                    unit = CELL_DISPLAY[key]["unit"]
                    txt = f"{val}{unit}"
                    # pad in Python
                    middle = txt.center(MIDDLE_WIDTH)
                    for ws in clients.copy():
                        asyncio.create_task(ws.send_text(f"{key}:{middle}"))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", 2002))
    await loop.create_datagram_endpoint(lambda: Proto(), sock=sock)

@app.on_event("startup")
async def startup():
    asyncio.create_task(udp_listener())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)