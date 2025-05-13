# Live NMEA  Dashboard

A minimal FastAPI + WebSocket + UDP listener app that parses NMEA sentences, applies a small-time-constant exponential moving average to each data field, and pushes live updates to a single-column, multi-row browser dashboard.

---

## ── Features

- **UDP listener** on configurable port, accepts raw NMEA datagrams  
- **Processor** coroutine with a simple `if isinstance(msg, …)` chain  
- **EMA smoothing** per metric with configurable time constant  
- **WebSocket server** that streams `"KEY:VALUE"` messages to the browser  
- **HTML+JS client**: auto-reconnect, logs events, updates only selected cells  
- **Command-line args** to control ports, log level, smoothing window, and displayed metrics

---

## ── Requirements

- Python 3.8+  
- `fastapi`, `uvicorn`, `pynmea2`

```bash
pip install fastapi uvicorn pynmea2
```

---

## ── Installation & Usage

1. **Clone** your project folder (where `nmea2web.py` lives).  
2. **Install** dependencies.  
3. **Run** the server with any combination of:

   ```bash
   python nmea2web.py \
     --udp-port 2002 \
     --web-port 8000 \
     --log-level INFO \
     --ema-smoothing-window 2.0 \
     --display-data BSP TWA HDG
   ```

4. Open your browser to `http://<host>:<web-port>/`.

---

## ── Command-Line Arguments

| Flag                         | Default                     | Description                                                          |
|------------------------------|-----------------------------|----------------------------------------------------------------------|
| `--udp-port <port>`          | `2002`                      | UDP port to listen for NMEA sentences                                |
| `--web-port <port>`          | `8000`                      | HTTP/WebSocket server port                                           |
| `--log-level <LEVEL>`        | `ERROR`                     | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) |
| `--ema-smoothing-window <s>` | `2.0`                       | EMA time constant in seconds                                         |
| `--display-data <KEY> [...]` | `BSP TWA HDG`               | Which metrics (CELLS keys) to render                                 |

---

## ── Configuration

All the configuration lives at the top of `nmea2web.py` and via the CLI flags:

- **CELLS** dict defines every metric collected:
  ```python
  CELLS = {
    "BSP": { "top":"BSP (kt)", "format":"%2.1f", … },
    "TWA": { "top":"TWA", "format":" %3.0f°", … },
    "HDG": { "top":"HDG (mag)", "format":" %3.0f°", … },
    # etc.
  }
  ```
- **SHOW_KEYS** is set from `--display-data` to choose which cells appear.
- **EMA_WINDOW** is set from `--ema-smoothing-window`.

---

## ── How it Works

1. **UDP Listener**  
   - Binds UDP on the specified `--udp-port`  
   - Parses datagrams with `pynmea2.parse`  
   - Enqueues messages in an `asyncio.Queue`

2. **Processor**  
   - Loops `await message_queue.get()`  
   - `if isinstance(msg, …)` to extract fields  
   - Calls `update_ema_and_state(key, value)`  
   - `broadcast(key)` sends `"KEY:VALUE"` via WebSocket

3. **WebSocket Endpoint**  
   - On connect, streams last-known EMAs for all `SHOW_KEYS`  
   - Streams live updates as `"KEY:formatted_value"`

4. **Browser Client**  
   - Builds a CSS grid for each key in `SHOW_KEYS`  
   - Auto-reconnects, logs events, and updates DOM on each message  
   - Dynamically sizes fonts to ~15%/65% of cell height for label/value

---

## ── Troubleshooting

- **Invalid `--display-data` key**  
  The server will exit with an error listing valid CELLS keys.

- **No updates in browser?**  
  - Check logs by raising `--log-level` to `INFO` or `DEBUG`  
  - Open browser console for WebSocket logs

- **Appearance tweaks**  
  - Modify CSS sizing factors in the HTML template  
  - Change colors or opacity via the CSS rules in `nmea2web.py`

---