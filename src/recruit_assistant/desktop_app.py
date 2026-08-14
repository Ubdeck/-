# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import threading

import webview

from .app_backend import APP_NAME, STATE, find_free_local_port, start_server


def main() -> None:
    port = find_free_local_port(8765)
    server, url = start_server("127.0.0.1", port, open_browser=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    shutdown_started = threading.Event()

    def shutdown_app() -> None:
        if shutdown_started.is_set():
            return
        shutdown_started.set()
        STATE.task_stop_event.set()
        STATE.stop_event.set()

        def shutdown_server() -> None:
            try:
                server.shutdown()
            finally:
                server.server_close()

        threading.Thread(target=shutdown_server, daemon=True).start()
        threading.Timer(1.0, lambda: os._exit(0)).start()

    try:
        window = webview.create_window(
            APP_NAME,
            url,
            width=1440,
            height=920,
            min_size=(1180, 760),
            text_select=True,
        )
        window.events.closed += shutdown_app
        webview.start(debug=False)
    finally:
        shutdown_app()
        os._exit(0)


if __name__ == "__main__":
    main()
