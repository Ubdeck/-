# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import argparse
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def run_update_installer_mode() -> None:
    if "--install-update" not in sys.argv:
        return
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--install-update", action="store_true")
    parser.add_argument("--target-pid", type=int, required=True)
    parser.add_argument("--target-exe", required=True)
    parser.add_argument("--log-path", required=True)
    args, _unknown = parser.parse_known_args()
    from recruit_assistant.updater import install_downloaded_update

    source_exe = Path(sys.executable if getattr(sys, "frozen", False) else __file__)
    exit_code = install_downloaded_update(
        source_exe=source_exe,
        target_exe=Path(args.target_exe),
        target_pid=args.target_pid,
        log_path=Path(args.log_path),
    )
    raise SystemExit(exit_code)


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


run_update_installer_mode()
relaunch_without_console()

from recruit_assistant.desktop_app import main


if __name__ == "__main__":
    main()
