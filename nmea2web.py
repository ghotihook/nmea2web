import argparse
import asyncio
import logging
import socket
import time
import math
import sys

import pynmea2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

# ── 0) Parse command-line arguments ─────────────────────────────────────────
parser = argparse.ArgumentParser(description="Live NMEA EMA Dashboard")
parser.add_argument(
    "--udp-port", type=int, default=2002,
    help="UDP port to listen for NMEA sentences (default: 2002)",
)
parser.add_argument(
    "--web-port", type=int, default=8000,
    help="HTTP/WebSocket server port (default: 8000)",
)
parser.add_argument(
    "--log-level",
    choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    default="ERROR",
    help="Logging level (default: ERROR)",
)
parser.add_argument(
    "--ema-smoothing-window", type=float, default=2.0,
    help="EMA smoothing time constant in seconds (default: 2.0)",
)
parser.add_argument(
    "--display-data", nargs="+", metavar="KEY",
    default=["BSP", "TWA", "HDG"],
    help="Which CELLS keys to display (default: BSP TWA HDG)",
)


parser.add_argument(
    "--page-color",
    type=str,
    default="#171F2F",     # rgb(23,32,47)
    help="Page background color (hex, e.g. #171F2F or #ffffff)",
)
parser.add_argument(
    "--cell-color",
    type=str,
    default="#2A2D3C",     # rgb(42,45,60)
    help="Cell background color (hex, e.g. #2A2D3C or #ffffff)",
)
parser.add_argument(
    "--text-color",
    type=str,
    default="#75FB4C",     # rgb(117,251,76)
    help="Text color (hex, e.g. #75FB4C or #ffffff)",
)
args = parser.parse_args()


# ── 1) Define and validate cell keys ─────────────────────────────────────────
CELLS = {
    "BSP": {"top":"BSP (kt)",    "format":"%2.1f", "ema":0.0, "last_ts":None},
    "TWA": {"top":"TWA",         "format":" %3.0f°","ema":0.0, "last_ts":None},
    "HDG": {"top":"HDG (mag)",   "format":" %3.0f°","ema":0.0, "last_ts":None},
    "TWS": {"top":"TWS (kt)",    "format":"%2.1f", "ema":0.0, "last_ts":None},
    "AWA": {"top":"AWA",         "format":" %3.0f°","ema":0.0, "last_ts":None},
    "AWS": {"top":"AWS (kt)",    "format":"%2.1f", "ema":0.0, "last_ts":None},
    "SOG": {"top":"SOG (kt)",    "format":"%2.1f", "ema":0.0, "last_ts":None},
    "COG": {"top":"COG",         "format":" %3.0f°","ema":0.0, "last_ts":None},
    "TWD": {"top":"TWD",         "format":" %3.0f°","ema":0.0, "last_ts":None},
}

invalid = [k for k in args.display_data if k not in CELLS]
if invalid:
    print(f"Error: invalid --display-data key(s): {', '.join(invalid)}", file=sys.stderr)
    print(f"Valid keys are: {', '.join(CELLS.keys())}", file=sys.stderr)
    sys.exit(1)

SHOW_KEYS = args.display_data

# ── 2) Logging ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, args.log_level),
    format="%(asctime)s %(levelname)s: %(message)s",
)

# ── 3) EMA configuration ────────────────────────────────────────────────────
EMA_WINDOW = args.ema_smoothing_window

def update_ema_and_state(key: str, raw_value: float):
    now = time.time()
    cell = CELLS[key]
    prev = cell["last_ts"]
    if prev is None:
        cell["ema"] = raw_value
    else:
        dt = now - prev
        alpha = 1 - math.exp(-dt / EMA_WINDOW)
        cell["ema"] += alpha * (raw_value - cell["ema"])
    cell["last_ts"] = now

# ── 4) Shared queue & WebSocket state ──────────────────────────────────────
message_queue = asyncio.Queue()
app = FastAPI()
clients: list[WebSocket] = []


last_sent: dict[str, str] = {} 

async def _send_safe(ws: WebSocket, payload: str):
    try:
        await ws.send_text(payload)
    except WebSocketDisconnect:
        # client disconnected cleanly
        if ws in clients:
            clients.remove(ws)
    except Exception:
        # any other network error
        if ws in clients:
            clients.remove(ws)

            

def broadcast(key: str):
    cell = CELLS[key]
    text = cell["format"] % cell["ema"]

    # only send if changed
    if last_sent.get(key) == text:
        return

    last_sent[key] = text
    payload = f"{key}:{text}"
    for ws in clients.copy():
        asyncio.create_task(_send_safe(ws, payload))

# ── 5) Build HTML (with dynamic sizing & centering) ─────────────────────────
#PAGE_BG     = "#ffffff"
#CELL_BG     = "#ffffff"
#FONT_COLOR  = "#000000"      # ← new!

PAGE_BG     = args.page_color
CELL_BG     = args.cell_color
FONT_COLOR  = args.text_color

CELL_GAP    = 12
CELL_RADIUS = 8

cells_html = ""
for key in SHOW_KEYS:
    cfg = CELLS[key]
    ph = cfg["format"] % 0
    cells_html += f'''
    <div class="cell" data-key="{key}">
      <div class="top-line">{cfg["top"]}</div>
      <div class="middle-line">{ph}</div>
    </div>'''

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Live EMA Dashboard</title>
  <style>
    * {{ box-sizing: border-box; }}
    html,body {{
      margin:0; width:100vw; height:100vh; overflow:hidden;
      background:{PAGE_BG}; font-family:system-ui,sans-serif;
    }}
    .grid {{
      display:grid; width:100%; height:100%;
      grid-template-rows:repeat({len(SHOW_KEYS)},minmax(0,1fr));
      gap:{CELL_GAP}px; padding:{CELL_GAP}px;
    }}
    .cell {{
      background:{CELL_BG}; border-radius:{CELL_RADIUS}px;
      display:flex; flex-direction:column;
      align-items:center; justify-content:center;
      padding:6px; color:{FONT_COLOR}; user-select:none;
    }}
    .top-line {{ flex:0 0 25%; display:flex; align-items:center; justify-content:center; white-space:nowrap;opacity: 0.7; }}
    .middle-line {{ flex:0 0 50%; display:flex; align-items:center; justify-content:center;
                    font-variant-numeric:tabular-nums; font-feature-settings:'tnum';
                    font-weight:bold; white-space:pre; }}
  </style>
</head>
<body>
  <div class="grid">{cells_html}</div>
  <script>
    function resizeFonts() {{
      document.querySelectorAll('.cell').forEach(cell => {{
        const h = cell.clientHeight;
        cell.querySelector('.top-line').style.fontSize = `${{0.15 * h}}px`;
        cell.querySelector('.middle-line').style.fontSize = `${{0.65 * h}}px`;
      }});
    }}
    ;(function(){{
      const cellMap = {{}};
      document.querySelectorAll('.cell').forEach(el=>{{cellMap[el.dataset.key]=el;}});
      function connect() {{
        const ws = new WebSocket("ws://"+location.host+"/ws");
        ws.onopen    = ()=>console.log("▶ WS open");
        ws.onmessage = e=>{{
          const [k,txt] = e.data.split(':');
          const c = cellMap[k];
          if(c) c.querySelector('.middle-line').textContent = txt;
        }};
        ws.onclose   = ()=>setTimeout(connect,1000);
        ws.onerror   = ()=>ws.close();
      }}
      connect();
      window.addEventListener('load', resizeFonts);
      window.addEventListener('resize', resizeFonts);
    }})();
  </script>
</body>
</html>"""


# ── 6) FastAPI endpoints ────────────────────────────────────────────────────
@app.get("/")
async def get_page():
    return HTMLResponse(html)

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.append(ws)
    for key in SHOW_KEYS:
        cfg = CELLS[key]
        await ws.send_text(f"{key}:{cfg['format']%cfg['ema']}")
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        clients.remove(ws)

# ── 7) UDP listener → enqueue messages ──────────────────────────────────────
async def udp_listener():
    loop = asyncio.get_running_loop()
    class Proto(asyncio.DatagramProtocol):
        def datagram_received(self, data, addr):
            raw = data.decode().strip()
            try:
                msg = pynmea2.parse(raw)
            except pynmea2.ParseError:
                return
            message_queue.put_nowait(msg)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR,1)
    sock.bind(("0.0.0.0", args.udp_port))
    await loop.create_datagram_endpoint(lambda:Proto(), sock=sock)

# ── 8) Processor: parse → EMA → broadcast ──────────────────────────────────
async def processor():
    while True:
        msg = await message_queue.get()
        if isinstance(msg, pynmea2.types.talker.VHW):
            bsp = float(msg.water_speed_knots)
            update_ema_and_state("BSP", bsp); broadcast("BSP")
        elif isinstance(msg, pynmea2.types.talker.MWV):
            angle_180 = (float(msg.wind_angle)+180)%360 - 180
            if msg.reference == "R":
                update_ema_and_state("AWA", angle_180); broadcast("AWA")
                update_ema_and_state("AWS", float(msg.wind_speed)); broadcast("AWS")
            else:
                update_ema_and_state("TWA", angle_180); broadcast("TWA")
                update_ema_and_state("TWS", float(msg.wind_speed)); broadcast("TWS")
        elif isinstance(msg, pynmea2.types.talker.HDG):
            hdg = float(msg.heading)
            update_ema_and_state("HDG", hdg); broadcast("HDG")
        elif isinstance(msg, pynmea2.types.talker.VTG):
            update_ema_and_state("SOG", float(msg.spd_over_grnd_kts)); broadcast("SOG")
            update_ema_and_state("COG", float(msg.mag_track)); broadcast("COG")
        elif isinstance(msg, pynmea2.types.talker.MWD):
            update_ema_and_state("TWD", float(msg.direction_magnetic)); broadcast("TWD")

@app.on_event("startup")
async def startup():
    asyncio.create_task(udp_listener())
    asyncio.create_task(processor())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=args.web_port)