#required
#pip install "uvicorn[standard]" fastapi

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

# ── App & State ───────────────────────────────────────────────────────────
app = FastAPI()
clients: list[WebSocket] = []

# ── HTML Page ─────────────────────────────────────────────────────────────
html = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Live Stat</title>
  <style>
    body {
      margin: 0;
      height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      background: #222;
      color: #0f0;
      font-family: sans-serif;
    }
    #stat {
      font-size: 12vw;
    }
  </style>
</head>
<body>
  <div id="stat">–</div>
  <script>
    const ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onmessage = e => {
      document.getElementById("stat").textContent = e.data;
    };
    ws.onopen = () => console.log("▶ WS connected");
    ws.onclose = () => console.log("✖ WS disconnected");
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
            # keep the connection alive (we ignore any messages from the client)
            await ws.receive_text()
    except WebSocketDisconnect:
        clients.remove(ws)

# ── UDP Listener ───────────────────────────────────────────────────────────
async def udp_listener():
    loop = asyncio.get_running_loop()

    class UDPProtocol(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr):
            msg = data.decode().strip()
            logging.info(f"⚡️ UDP received {msg!r} from {addr}")
            for ws in clients.copy():
                asyncio.create_task(ws.send_text(msg))

    # IPv4 with reuse
    await loop.create_datagram_endpoint(
        lambda: UDPProtocol(),
        local_addr=("0.0.0.0", 9999),
        reuse_address=True,    # sets SO_REUSEADDR
        reuse_port=True,       # sets SO_REUSEPORT (Unix only)
    )

    # (Optional) IPv6 dual-stack with reuse
    try:
        await loop.create_datagram_endpoint(
            lambda: UDPProtocol(),
            local_addr=("::", 9999),
            family=socket.AF_INET6,
            reuse_address=True,
            reuse_port=True,
        )
    except Exception as e:
        logging.warning(f"Could not bind IPv6 UDP socket: {e}")


        
@app.on_event("startup")
async def on_startup():
    # fire-and-forget UDP listener
    asyncio.create_task(udp_listener())

# ── Entrypoint ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000, 
        reload=False  # disable auto-reload so `python ws.py` stays in foreground
    )