# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def relaunch_without_console() -> None:
    if os.name != "nt" or getattr(sys, "frozen", False):
        return
    if os.environ.get("RECRUIT_ASSISTANT_NO_CONSOLE") == "1":
        return
    executable = Path(sys.executable)
    if executable.name.lower() != "python.exe":
        return
    pythonw = executable.with_name("pythonw.exe")
    if not pythonw.exists():
        return
    env = os.environ.copy()
    env["RECRUIT_ASSISTANT_NO_CONSOLE"] = "1"
    creationflags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    subprocess.Popen([str(pythonw), str(Path(__file__).resolve())], cwd=str(ROOT_DIR), env=env, creationflags=creationflags)
    raise SystemExit(0)


relaunch_without_console()

from recruit_assistant.desktop_app import main


if __name__ == "__main__":
    main()
