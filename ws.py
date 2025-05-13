import asyncio
import logging
import socket
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)

# ── Config (your “magic numbers”) ──────────────────────────────────────────
GRID_ROWS = 4   # ← change this
GRID_COLS = 6   # ← or this
PAGE_BG   = "rgb(20,32,48)"
CELL_BG   = "rgb(46,50,69)"
CELL_GAP  = 12  # px between cells & screen edge
CELL_RADIUS = 8 # px corner radius

# ── App & State ───────────────────────────────────────────────────────────
app = FastAPI()
clients: list[WebSocket] = []

# ── HTML Page ─────────────────────────────────────────────────────────────
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
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat({GRID_COLS}, 1fr);
      grid-auto-rows: minmax(60px, auto);
      gap: {CELL_GAP}px;
      height: 100%;
    }}
    .cell {{
      background: {CELL_BG};
      border-radius: {CELL_RADIUS}px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 2.5vw;
      color: #0f0;
      user-select: none;
    }}
  </style>
</head>
<body>
  <div class="grid">
    {"".join(f'<div class="cell" id="cell-{i}">–</div>' 
             for i in range(GRID_ROWS * GRID_COLS))}
  </div>
  <script>
    const cells = Array.from(document.querySelectorAll(".cell"));

    const ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onopen = () => console.log("▶ WS connected");
    ws.onclose = () => console.log("✖ WS disconnected");
    ws.onmessage = e => {{
      // Expecting plain text "idx:value", e.g. "5:42.7"
      const [rawIdx, rawVal] = e.data.split(":");
      const idx = Number(rawIdx);
      if (!isNaN(idx) && cells[idx]) {{
        cells[idx].textContent = rawVal;
      }}
    }};
  </script>
</body>
</html>
"""

# ── Routes ─────────────────────────────────────────────────────────────────
@app.get("/")
async def get_page():
    return HTMLResponse(html)

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.append(ws)
    try:
        while True:
            # keep-alive ping from browser (ignored)
            await ws.receive_text()
    except WebSocketDisconnect:
        clients.remove(ws)

# ── UDP Listener ───────────────────────────────────────────────────────────
async def udp_listener():
    loop = asyncio.get_running_loop()

    class UDPProtocol(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr):
            text = data.decode().strip()
            logging.info(f"⚡️ UDP recv {text!r} from {addr}")
            # broadcast "index:value" to all clients
            for ws in clients.copy():
                asyncio.create_task(ws.send_text(text))

    # IPv4 socket
    sock4 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock4.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock4.bind(("0.0.0.0", 9998))
    await loop.create_datagram_endpoint(lambda: UDPProtocol(), sock=sock4)



@app.on_event("startup")
async def on_startup():
    asyncio.create_task(udp_listener())

# ── Entrypoint ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)