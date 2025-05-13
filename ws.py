import asyncio
import logging
import socket

import pynmea2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# ── 1) Cell definitions ─────────────────────────────────────────────────────
# Exactly four cells in a single column
CELLS = {
    "a": {"top": "BSP (kt)",     "format": "%0.1f"},
    "b": {"top": "TWA",          "format": "%0.0f°"},
    "c": {"top": "HDG (mag)",    "format": "%0.0f°"},
}

# ── 2) Appearance constants ─────────────────────────────────────────────────
PAGE_BG     = "rgb(20,32,48)"
CELL_BG     = "rgb(46,50,69)"
CELL_GAP    = 12  # px
CELL_RADIUS = 8   # px

# ── 3) Build the HTML page ────────────────────────────────────────────────
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
  <title>Live Single-Column Dashboard</title>
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
      font-size: 5vw;
      text-align: center;
      margin: 4px 0;
      line-height: 1;
    }}
    .middle-line {{
      font-size: 20vh;       /* as large as practical */
      max-height: 100%;
      text-align: center;
      line-height: 1;
      font-variant-numeric: tabular-nums;
      font-feature-settings: 'tnum';
      font-weight: bold;
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
        setTimeout(connect, 1000);
      }});
      ws.addEventListener('error', err => {{
        console.warn("WS error:", err);
        ws.close();
      }});
    }}
    connect();
  }})();
  </script>
</body>
</html>"""

# ── 4) FastAPI & WebSocket setup ───────────────────────────────────────────
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

# ── 5) UDP listener with simple if/elif routing ───────────────────────────
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

            if isinstance(msg, pynmea2.types.talker.VHW):
                broadcast("a", CELLS["a"]["format"] % msg.water_speed_knots)

            elif isinstance(msg, pynmea2.types.talker.MWV):
                if msg.reference == "T":
                    angle_180 = (float(msg.wind_angle) + 180) % 360 - 180
                    broadcast("b", CELLS["b"]["format"] % angle_180)

            elif isinstance(msg, pynmea2.types.talker.HDG):
                broadcast("c", CELLS["c"]["format"] % msg.heading)

            # add more cases if needed...

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