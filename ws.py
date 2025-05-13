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

# ── Appearance ─────────────────────────────────────────────────────────────
PAGE_BG     = "rgb(20,32,48)"
CELL_BG     = "rgb(46,50,69)"
CELL_GAP    = 12   # px
CELL_RADIUS = 8    # px

# ── Build HTML ──────────────────────────────────────────────────────────────
cells_html = ""
for row in LAYOUT:
    for key, span in row:
        ui   = CELL_DISPLAY.get(key, {"top": key, "unit": "", "bottom": ""})
        cells_html += f'''
        <div class="cell span-{span}" data-key="{key}" data-unit="{ui["unit"]}">
          <div class="top-line">{ui["top"]}</div>
          <div class="middle-line">
            <span class="sign-line"></span>
            <span class="value-line">–</span>
            <span class="unit-line">{ui["unit"]}</span>
          </div>
          <div class="bottom-line">{ui["bottom"]}</div>
        </div>'''

html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Live Grid</title>
  <style>
    html, body {{
      margin: 0;
      padding: {CELL_GAP}px;
      height: 100%;
      background: {PAGE_BG};
      box-sizing: border-box;
      font-family: system-ui, -apple-system, BlinkMacSystemFont,
                   'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
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
      justify-content: space-between;
      padding: 4px;
      color: #0f0;
      user-select: none;
    }}
    .top-line, .bottom-line {{
      font-size: 2.5vw;
      line-height: 1;
      margin: 2px 0;
    }}
    .middle-line {{
      display: flex;
      justify-content: center;
      align-items: baseline;
      width: 100%;
    }}
    .sign-line {{
      font-size: 5vw;
      line-height: 1;
      /* no margin so sign hugs value */
    }}
    .value-line {{
      font-size: 5vw;
      line-height: 1;
      margin: 0 0.1ch;
      font-variant-numeric: tabular-nums;
      font-feature-settings: 'tnum';
      font-weight: bold;
    }}
    .unit-line {{
      font-size: 3vw;
      line-height: 1;
      /* no margin so unit hugs value */
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
    // build map key→cellElem
    const cellMap = Object.fromEntries(
      Array.from(document.querySelectorAll('.cell'))
           .map(el => [el.dataset.key, el])
    );

    const ws = new WebSocket(`ws://${{location.host}}/ws`);
    ws.onopen  = () => console.log("▶ WS connected");
    ws.onclose = () => console.log("✖ WS disconnected");
    ws.onmessage = e => {{
      const [key, raw] = e.data.split(":");
      const cell = cellMap[key];
      if (!cell) return;
      const isNeg = raw.startsWith("-");
      const sign  = isNeg ? "-" : "";
      const num   = isNeg ? raw.slice(1) : raw;
      // update sign, value (centered), unit (from data-unit)
      cell.querySelector('.sign-line').textContent = sign;
      cell.querySelector('.value-line').textContent = num;
      // unit-line already contains the unit text
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
            await ws.receive_text()  # keep-alive
    except WebSocketDisconnect:
        clients.remove(ws)

# ── UDP → WebSocket Bridge (port 2002) ──────────────────────────────────────
async def udp_listener():
    loop = asyncio.get_running_loop()

    class UDPProtocol(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr):
            raw = data.decode().strip()
            logging.info(f"⚡️ UDP recv {raw!r} from {addr}")
            try:
                msg = pynmea2.parse(raw)
            except pynmea2.ParseError:
                return

            stype = msg.sentence_type
            if stype in NMEA_TO_CELLS:
                for cell_key, attr in NMEA_TO_CELLS[stype]:
                    val = getattr(msg, attr, None)
                    if val is not None:
                        for ws in clients.copy():
                            asyncio.create_task(ws.send_text(f"{cell_key}:{val}"))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", 2002))
    await loop.create_datagram_endpoint(lambda: UDPProtocol(), sock=sock)

@app.on_event("startup")
async def on_startup():
    asyncio.create_task(udp_listener())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)