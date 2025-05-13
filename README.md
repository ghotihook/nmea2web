# Live NMEA EMA Dashboard

A minimal FastAPI + WebSocket + UDP listener app that parses NMEA sentences, applies a small-time-constant exponential moving average (EMA) to each data field, and pushes live updates to a single-column, multi-row browser dashboard.

---

## ── Features

- **UDP listener** on port 2002, accepts raw NMEA datagrams  
- **Processor** coroutine with a simple `if isinstance(msg, …)` chain  
- **EMA smoothing** per metric (configurable time constant)  
- **WebSocket server** that streams `"KEY:VALUE"` messages to the browser  
- **HTML+JS client**: auto-reconnect, logs events, updates only selected cells  
- **Full configurability**: define any number of metrics in Python and choose a subset to display

---

## ── Requirements

- Python 3.8+  
- `fastapi`, `uvicorn`, `pynmea2`

```bash
pip install fastapi uvicorn pynmea2
```

---

## ── Installation

1. **Clone** your project folder (where `ws.py` lives).  
2. **Install** dependencies (see Requirements).  
3. **Run** the server:

   ```bash
   python ws.py
   ```

4. Open your browser to `http://<host>:8000/`.

---

## ── Configuration

All the “knobs” live at the top of `ws.py`:

```python
# EMA time constant (seconds)
EMA_WINDOW = 1.0

# Master list of all metrics collected & smoothed:
CELLS = {
  "BSP": { "top":"BSP (kt)",    "format":"%0.1f", … },
  "TWA": { "top":"TWA",         "format":"%0.0f°",… },
  "HDG": { "top":"HDG (mag)",   "format":"%0.0f°",… },
  "TWS": { … },  # even if not shown, it will collect data
  … 
}

# Subset of CELLS keys to actually render on the page:
SHOW_KEYS = ["BSP", "TWA", "HDG"]
```

- **Add / remove metrics** by editing the `CELLS` dict.  
- **Change labels or formats** via the `"top"` and `"format"` fields.  
- **Adjust smoothing** by tweaking `EMA_WINDOW`.  
- **Choose which cells appear** by modifying `SHOW_KEYS`.  

---

## ── How it Works

### 1) UDP Listener  
- Binds IPv4 UDP on port 2002  
- Parses each datagram with `pynmea2.parse(…)`  
- Enqueues the resulting `msg` in an `asyncio.Queue`

### 2) Processor  
- `await message_queue.get()` in a tight loop  
- Uses `if isinstance(msg, pynmea2.types.talker.XYZ): …`  
- Extracts raw field(s), calls `update_ema_and_state(key, value)`  
- `broadcast(key)` immediately pushes the new EMA via WebSocket

### 3) EMA State  
- Each `CELLS[key]` holds  
  - `ema`: current smoothed value  
  - `last_ts`: timestamp of last update  
- `update_ema_and_state` applies  

\[
  \alpha = 1 - e^{-\Delta t / \tau},\quad
  \mathrm{ema} \leftarrow \mathrm{ema} + \alpha\,(\mathrm{raw} - \mathrm{ema})
\]

### 4) WebSocket Endpoint  
- On client connect:  
  - `await ws.accept()`  
  - Send last-known EMA for every **`SHOW_KEYS`**  
- Then keep the connection alive and stream live `"KEY:VALUE"` messages

### 5) Browser Client  
- HTML grid built from **`SHOW_KEYS`**  
- JS opens a WebSocket, logs `open`, `message`, `error`, `close`  
- Automatically reconnects after 1 s on any drop  
- Splits each incoming `"KEY:VALUE"` and updates the matching cell

---

## ── Modifying the Processor

Inside `async def processor()` you’ll find a series of `if/elif` blocks:

```python
if isinstance(msg, pynmea2.types.talker.VHW):
    bsp = float(msg.water_speed_knots)
    update_ema_and_state("BSP", bsp); broadcast("BSP")
elif isinstance(msg, pynmea2.types.talker.VTG):
    twa = float(msg.mag_track)
    update_ema_and_state("TWA", twa); broadcast("TWA")
…
```

- **Add new sentence types** by copying one of these blocks.  
- **Extract additional attributes** via `msg.some_field`.  
- **Decide** whether to call `broadcast(key)` to update the UI.

---

## ── Troubleshooting

- **No updates in browser?**  
  - Check server logs (run with `logging.basicConfig(level=logging.INFO)`)  
  - Open browser console: you should see `"▶ WS open"`, `"◀ WS msg …"`  
- **Sudden disconnects?**  
  - Client auto-reconnect kicks in after 1 s  
- **Slow smoothing?**  
  - Lower `EMA_WINDOW` for snappier, noisier updates  
  - Raise `EMA_WINDOW` for smoother, more laggy behavior

---

> Enjoy your real-time NMEA dashboard! Feel free to tweak the layout, labels, or add charts in the browser as needed.
"""