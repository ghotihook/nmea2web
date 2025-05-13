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

# ── 1) Cell definitions & EMA state ─────────────────────────────────────────
EMA_WINDOW = 5.0  # seconds time-constant for EMA

CELLS = {
    "BSP": {
        "top":     "BSP (kt)",
        "format":  "%0.1f",
        "ema":     0.0,
        "last_ts": None,
    },
    "TWA": {
        "top":     "TWA",
        "format":  "%0.0f°",
        "ema":     0.0,
        "last_ts": None,
    },
    "HDG": {
        "top":     "HDG (mag)",
        "format":  "%0.0f°",
        "ema":     0.0,
        "last_ts": None,
    },
}

def update_ema_and_state(key: str, raw_value: float):
    """Update the EMA for `key` in CELLS."""
    now = time.time()
    cell = CELLS[key]
    prev_ts = cell["last_ts"]
    if prev_ts is None:
        cell["ema"] = raw_value
    else:
        dt = now - prev_ts
        alpha = 1 - math.exp(-dt / EMA_WINDOW)
        cell["ema"] += alpha * (raw_value - cell["ema"])
    cell["last_ts"] = now

# ── 2) Shared queue & WebSocket state ───────────────────────────────────────
message_queue = asyncio.Queue()
app = FastAPI()
clients: list[WebSocket] = []

def broadcast(key: str):
    """Format the EMA value and send to all WebSocket clients."""
    cell = CELLS[key]
    text = cell["format"] % cell["ema"]
    payload = f"{key}:{text}"
    for ws in clients.copy():
        asyncio.create_task(ws.send_text(payload))

# ── 3) Build the HTML page ──────────────────────────────────────────────────
PAGE_BG     = "rgb(20,32,48)"
CELL_BG     = "rgb(46,50,69)"
CELL_GAP    = 12  # px
CELL_RADIUS = 8   # px

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
  <meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Live Dashboard</title>
  <style>
    * {{ box-sizing: border-box; }}
    html, body {{
      margin: 0; width:100vw; height:100vh; overflow:hidden;
      background: {PAGE_BG};
      font-family: system-ui, -apple-system, BlinkMacSystemFont,
                   'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
    }}
    .grid {{
      display: grid; width:100%; height:100%;
      grid-template-rows: repeat({len(CELLS)}, minmax(0,1fr));
      gap: {CELL_GAP}px; padding: {CELL_GAP}px;
    }}
    .cell {{
      background: {CELL_BG}; border-radius: {CELL_RADIUS}px;
      display: flex; flex-direction: column;
      align-items: center; justify-content: center;
      padding: 6px; color: #0f0; user-select: none;
    }}
    .top-line {{ font-size: 2.5vw; text-align: center; margin: 4px 0; line-height: 1; }}
    .middle-line {{
      font-size: 15vh; max-height: 100%; text-align: center; line-height: 1;
      font-variant-numeric: tabular-nums; font-feature-settings:'tnum'; font-weight:bold;
    }}
  </style>
</head>
<body>
  <div class="grid">
    {cells_html}
  </div>
  <script>
  (function(){{
    const cellMap = {{}};
    document.querySelectorAll('.cell').forEach(el => {{
      cellMap[el.dataset.key] = el;
    }});
    function connect() {{
      const ws = new WebSocket("ws://" + location.host + "/ws");
      ws.onmessage = e => {{
        const [key, text] = e.data.split(':');
        const c = cellMap[key];
        if (c) c.querySelector('.middle-line').textContent = text;
      }};
      ws.onclose = () => setTimeout(connect, 1000);
      ws.onerror = () => ws.close();
    }}
    connect();
  }})();
  </script>
</body>
</html>"""

# ── 4) FastAPI endpoints ────────────────────────────────────────────────────
@app.get("/")
async def get_page():
    return HTMLResponse(html)

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.append(ws)

    # ← NEW: send the last-known value for each cell
    for key, cfg in CELLS.items():
        text = cfg["format"] % cfg["ema"]
        await ws.send_text(f"{key}:{text}")

    try:
        while True:
            await ws.receive_text()  # keep-alive
    except WebSocketDisconnect:
        clients.remove(ws)

# ── 5) UDP listener enqueues raw messages ───────────────────────────────────
async def udp_listener():
    loop = asyncio.get_running_loop()
    class Proto(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr):
            raw = data.decode().strip()
            logging.info(f"UDP recv {raw!r}")
            try:
                msg = pynmea2.parse(raw)
            except pynmea2.ParseError:
                return
            message_queue.put_nowait(msg)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", 2002))
    await loop.create_datagram_endpoint(lambda: Proto(), sock=sock)

# ── 6) Processor: capture all data, update dict & EMA ──────────────────────
async def processor():
    while True:
        msg = await message_queue.get()

        if isinstance(msg, pynmea2.types.talker.VHW):
            bsp = float(msg.water_speed_knots)
            update_ema_and_state("BSP", bsp); broadcast("BSP")

        elif isinstance(msg, pynmea2.types.talker.HDG):
            hdg = float(msg.heading_true)
            update_ema_and_state("HDG", hdg); broadcast("HDG")

        elif isinstance(msg, pynmea2.types.talker.VTG):
            twa = float(msg.mag_track)
            update_ema_and_state("TWA", twa); broadcast("TWA")

@app.on_event("startup")
async def startup():
    asyncio.create_task(udp_listener())
    asyncio.create_task(processor())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)