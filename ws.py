import asyncio
import logging
import socket

import pynmea2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)

# ── Layout (4-column grid) ─────────────────────────────────────────────────
# Each tuple is (cellKey, spanUnits) where spanUnits ∈ {1,2,4}
LAYOUT = [
    [("a", 4)],
    [("b", 1), ("c", 1), ("d", 2)],
    [("e", 2), ("f", 2)],
    [("g", 1), ("h", 1), ("i", 1), ("j", 1)],
]

# ── NMEA → Cell mapping ─────────────────────────────────────────────────────
# cellKey → (sentence_type, attribute_name)
CELL_NMEA_CONFIG = {
    "a": ("VHW", "water_speed_knots"),
    "b": ("VHW", "heading_true"),
    # add more as needed...
}
# invert for quick lookup by sentence_type
NMEA_TO_CELLS = {}
for key, (stype, attr) in CELL_NMEA_CONFIG.items():
    NMEA_TO_CELLS.setdefault(stype, []).append((key, attr))

# ── Cell display text: top_line, unit, bottom_line ─────────────────────────
# Fill in your labels/units as desired
CELL_DISPLAY = {
    "a": {"top": "Water Speed", "unit": "kn", "bottom": ""},
    "b": {"top": "True Heading", "unit": "°T", "bottom": ""},
    "c": {"top": "…", "unit": "", "bottom": ""},
    "d": {"top": "…", "unit": "", "bottom": ""},
    "e": {"top": "…", "unit": "", "bottom": ""},
    "f": {"top": "…", "unit": "", "bottom": ""},
    "g": {"top": "…", "unit": "", "bottom": ""},
    "h": {"top": "…", "unit": "", "bottom": ""},
    "i": {"top": "…", "unit": "", "bottom": ""},
    "j": {"top": "…", "unit": "", "bottom": ""},
}

# ── Appearance ─────────────────────────────────────────────────────────────
PAGE_BG     = "rgb(20,32,48)"
CELL_BG     = "rgb(46,50,69)"
CELL_GAP    = 12   # px
CELL_RADIUS = 8    # px

# ── Build the HTML with 4 lines per cell ───────────────────────────────────
cells_html = ""
for row in LAYOUT:
    for key, span in row:
        ui = CELL_DISPLAY.get(key, {"top": key, "unit": "", "bottom": ""})
        top    = ui["top"]
        unit   = ui["unit"]
        bottom = ui["bottom"]
        cells_html += f'''
        <div class="cell span-{span}" data-key="{key}">
          <div class="top-line">{top}</div>
          <div class="value-line">–</div>
          <div class="unit-line">{unit}</div>
          <div class="bottom-line">{bottom}</div>
        </div>'''

html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Live Grid</title>
  <style>
    html, body {{
      margin: 0; padding: {CELL_GAP}px; height: 100%; 
      background: {PAGE_BG}; box-sizing: border-box;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: {CELL_GAP}px;
      height: 100%;
    }}
    .cell {{
      background: {CELL_BG};
      border-radius: {CELL_RADIUS}px;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      color: #0f0;
      user-select: none;
    }}
    .top-line, .unit-line, .bottom-line {{
      font-size: 1.2vw;
      margin: 2px 0;
    }}
    .value-line {{
      font-size: 3vw;
      margin: 2px 0;
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
    // Map cellKey → <div class="cell">
    const cellMap = Object.fromEntries(
      Array.from(document.querySelectorAll('.cell'))
           .map(el => [el.dataset.key, el])
    );

    const ws = new WebSocket(`ws://${{location.host}}/ws`);
    ws.onopen  = () => console.log("▶ WS connected");
    ws.onclose = () => console.log("✖ WS disconnected");
    ws.onmessage = e => {{
      // expecting "key:value"
      const [key, val] = e.data.split(":");
      const cell = cellMap[key];
      if (cell) {{
        cell.querySelector('.value-line').textContent = val;
      }}
    }};
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
            # ignore client pings
            await ws.receive_text()
    except WebSocketDisconnect:
        clients.remove(ws)

# ── UDP → WebSocket Bridge ─────────────────────────────────────────────────
async def udp_listener():
    loop = asyncio.get_running_loop()

    class UDPProtocol(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr):
            raw = data.decode().strip()
            logging.info(f"⚡️ UDP recv {raw!r} from {addr}")

            # parse NMEA
            try:
                msg = pynmea2.parse(raw)
            except pynmea2.ParseError:
                logging.warning(f"Bad NMEA: {raw!r}")
                return

            stype = msg.sentence_type  # e.g. "VHW"
            if stype in NMEA_TO_CELLS:
                for cell_key, attr in NMEA_TO_CELLS[stype]:
                    val = getattr(msg, attr, None)
                    if val is not None:
                        # broadcast "key:value"
                        for ws in clients.copy():
                            asyncio.create_task(ws.send_text(f"{cell_key}:{val}"))

    # manual IPv4 socket with SO_REUSEADDR
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", 9999))
    await loop.create_datagram_endpoint(lambda: UDPProtocol(), sock=sock)

@app.on_event("startup")
async def on_startup():
    asyncio.create_task(udp_listener())

# ── Entrypoint ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)