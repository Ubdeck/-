# -*- coding: utf-8 -*-
from __future__ import annotations

import threading

import webview

from .app_backend import APP_NAME, STATE, find_free_local_port, start_server


def main() -> None:
    port = find_free_local_port(8765)
    server, url = start_server("127.0.0.1", port, open_browser=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        webview.create_window(
            APP_NAME,
            url,
            width=1440,
            height=920,
            min_size=(1180, 760),
            text_select=True,
        )
        webview.start(debug=False)
    finally:
        STATE.stop_event.set()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
