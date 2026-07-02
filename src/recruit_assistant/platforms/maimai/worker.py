# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

if __package__:
    from . import bridge as maimai_bridge
else:
    src_dir = Path(__file__).resolve().parents[3]
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    from recruit_assistant.platforms.maimai import bridge as maimai_bridge


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: python -m recruit_assistant.platforms.maimai_worker <config.json> <result.json>", flush=True)
        return 2

    config_path = Path(sys.argv[1])
    result_path = Path(sys.argv[2])
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        worker_stdout = sys.stdout
        result = maimai_bridge.run_pipeline(
            config,
            lambda message: print(message, flush=True, file=worker_stdout),
        )
        result_path.write_text(
            json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return 0
    except Exception as exc:
        print(f"Maimai workflow failed: {exc}", flush=True)
        traceback.print_exc()
        result_path.write_text(
            json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


