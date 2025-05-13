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

# ── Config ─────────────────────────────────────────────────────────────────
GRID_ROWS   = 4   # ← number of rows
GRID_COLS   = 1   # ← number of columns
PAGE_BG     = "rgb(20,32,48)"
CELL_BG     = "rgb(46,50,69)"
CELL_GAP    = 12  # px between cells & screen edge
CELL_RADIUS = 8   # px corner radius

# ── App & State ───────────────────────────────────────────────────────────
app = FastAPI()
clients: list[WebSocket] = []

# ── HTML Page ─────────────────────────────────────────────────────────────
html = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Live Grid</title>
  <style>
    html, body {
      margin: 0;
      padding: %dpx;
      height: 100%%;
      background: %s;
      box-sizing: border-box;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(%d, 1fr);
      grid-auto-rows: minmax(60px, auto);
      gap: %dpx;
      height: 100%%;
    }
    .cell {
      background: %s;
      border-radius: %dpx;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 2.5vw;
      color: #0f0;
      user-select: none;
    }
  </style>
</head>
<body>
  <div class="grid">
    <!-- cells will be injected by Python -->
    %s
  </div>
  <script>
    const cells = Array.from(document.querySelectorAll(".cell"));
    const ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onopen = () => console.log("▶ WS connected");
    ws.onclose = () => console.log("✖ WS disconnected");
    ws.onmessage = e => {
      // incoming format "idx:value"
      const [rawIdx, rawVal] = e.data.split(":");
      const idx = Number(rawIdx);
      if (!isNaN(idx) && cells[idx]) {
        cells[idx].textContent = rawVal;
      }
    };
  </script>
</body>
</html>
""" % (
    CELL_GAP, PAGE_BG,
    GRID_COLS,
    CELL_GAP,
    CELL_BG, CELL_RADIUS,
    "".join(f'<div class="cell" id="cell-{i}">–</div>' for i in range(GRID_ROWS * GRID_COLS))
)

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
            await ws.receive_text()  # keep-alive pings
    except WebSocketDisconnect:
        clients.remove(ws)

# ── UDP Listener ───────────────────────────────────────────────────────────
async def udp_listener():
    loop = asyncio.get_running_loop()

    class UDPProtocol(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr):
            text = data.decode().strip()
            logging.info(f"⚡️ UDP recv {text!r} from {addr}")
            for ws in clients.copy():
                asyncio.create_task(ws.send_text(text))

    # IPv4 only
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