#hi 

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

# ── Configurable Layout ────────────────────────────────────────────────────
# Each tuple is (cellKey, spanUnits), where spanUnits must be 1, 2 or 4
LAYOUT = [
    [("a", 4)],                        # row 1: a spans all 4 cols
    [("b", 1), ("c", 1), ("d", 2)],    # row 2: b=1/4, c=1/4, d=2/4
    [("e", 2), ("f", 2)],              # row 3: two half-widths
    [("g", 1), ("h", 1), ("i", 1), ("j", 1)],  # row 4: four quarter-widths
]

# Appearance
PAGE_BG     = "rgb(20,32,48)"
CELL_BG     = "rgb(46,50,69)"
CELL_GAP    = 12   # px
CELL_RADIUS = 8    # px corner radius

# ── Build HTML ─────────────────────────────────────────────────────────────
# flatten LAYOUT into a single string of <div> cells
cells_html = "".join(
    f'<div class="cell span-{span}" data-key="{key}">–</div>'
    for row in LAYOUT for key, span in row
)

html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Live Grid</title>
  <style>
    html, body {{ margin: 0; padding: {CELL_GAP}px; height: 100%; background: {PAGE_BG}; box-sizing: border-box; }}
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
      align-items: center; 
      justify-content: center; 
      font-size: 2.5vw; 
      color: #0f0; 
      user-select: none; 
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
    // build a map key→cellElement
    const cellMap = Object.fromEntries(
      Array.from(document.querySelectorAll('.cell'))
           .map(el => [el.dataset.key, el])
    );
    // open WS
    const ws = new WebSocket(`ws://${{location.host}}/ws`);
    ws.onopen = () => console.log("▶ WS connected");
    ws.onclose = () => console.log("✖ WS disconnected");
    // on "key:value", update the matching cell
    ws.onmessage = e => {{
      const [key, val] = e.data.split(":");
      if (cellMap[key]) cellMap[key].textContent = val;
    }};
  </script>
</body>
</html>
"""

# ── FastAPI App ────────────────────────────────────────────────────────────
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
            await ws.receive_text()  # ignore, just keep alive
    except WebSocketDisconnect:
        clients.remove(ws)

# ── UDP → WebSocket Bridge ─────────────────────────────────────────────────
async def udp_listener():
    loop = asyncio.get_running_loop()

    class UDPProtocol(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr):
            text = data.decode().strip()
            logging.info(f"⚡️ UDP recv {text!r} from {addr}")
            for ws in clients.copy():
                asyncio.create_task(ws.send_text(text))

    # manually create IPv4 socket with SO_REUSEADDR
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