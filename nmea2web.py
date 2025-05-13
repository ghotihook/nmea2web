import argparse
# … other imports …

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
    "--display-data",
    nargs="+",
    default=["BSP", "TWA", "HDG"],
    metavar="KEY",
    help="Which CELLS keys to display (default: BSP TWA HDG)",
)
args = parser.parse_args()

# ── after you define CELLS …
# Substitute SHOW_KEYS with the CLI argument:
SHOW_KEYS = args.display_data

# ── then build your HTML grid using SHOW_KEYS ───────────────────────────────
cells_html = ""
for key in SHOW_KEYS:
    cfg = CELLS[key]
    placeholder = cfg["format"] % 0
    cells_html += f'''
    <div class="cell" data-key="{key}">
      <div class="top-line">{cfg["top"]}</div>
      <div class="middle-line">{placeholder}</div>
    </div>'''
# … rest of HTML and app setup …

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=args.web_port)