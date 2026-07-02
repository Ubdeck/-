# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

if "--maimai-worker" in sys.argv:
    from recruit_assistant.platforms.maimai_worker import main as maimai_worker_main

    index = sys.argv.index("--maimai-worker")
    sys.argv = [sys.argv[0], *sys.argv[index + 1 :]]
    raise SystemExit(maimai_worker_main())

from recruit_assistant.desktop_app import main


if __name__ == "__main__":
    main()
