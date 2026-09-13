from __future__ import annotations

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os
import threading
import webbrowser


def serve(site: Path, timeout_minutes: float = 30, open_browser: bool = True) -> None:
    if timeout_minutes <= 0:
        raise ValueError("timeout_minutes must be greater than zero")
    index = site / "index.html"
    if not index.is_file():
        raise FileNotFoundError(f"Wiki entry point not found: {index}")

    os.chdir(site)
    server = ThreadingHTTPServer(("127.0.0.1", 0), SimpleHTTPRequestHandler)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Wiki: {url}", flush=True)
    print(
        f"The server will stop after {timeout_minutes:g} minutes. "
        "Press Ctrl+C to stop sooner.",
        flush=True,
    )

    def stop() -> None:
        print(f"\n{timeout_minutes:g}-minute timeout reached. Stopping.", flush=True)
        server.shutdown()

    timeout = threading.Timer(timeout_minutes * 60, stop)
    timeout.daemon = True
    timeout.start()
    if open_browser:
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

