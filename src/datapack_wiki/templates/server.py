import argparse
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import os
import threading
import webbrowser

parser = argparse.ArgumentParser(description="Serve the local datapack wiki.")
parser.add_argument(
    "--timeout-minutes",
    type=float,
    default=30,
    help="Automatically stop the server after this many minutes (default: 30).",
)
parser.add_argument("--no-browser", action="store_true", help="Do not open a browser.")
args = parser.parse_args()
if args.timeout_minutes <= 0:
    parser.error("--timeout-minutes must be greater than zero")

os.chdir(Path(__file__).parent)
server = ThreadingHTTPServer(("127.0.0.1", 0), SimpleHTTPRequestHandler)
url = f"http://127.0.0.1:{server.server_port}/"
print(f"Datapack Wiki: {url}", flush=True)
print(f"The server will stop after {args.timeout_minutes:g} minutes. Press Ctrl+C to stop sooner.")


def timeout_server():
    print(f"\n{args.timeout_minutes:g}-minute timeout reached. Stopping the wiki server.")
    server.shutdown()


timeout = threading.Timer(args.timeout_minutes * 60, timeout_server)
timeout.daemon = True
timeout.start()
if not args.no_browser:
    browser = threading.Timer(0.5, lambda: webbrowser.open(url))
    browser.daemon = True
    browser.start()
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
finally:
    timeout.cancel()
    server.server_close()
