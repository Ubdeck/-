from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip().strip('"').strip("'")
    return values


def load_env_values(extra_paths: Iterable[Path] = ()) -> dict[str, str]:
    root = application_root()
    candidates = [*extra_paths, root / "runtime" / ".env", root / ".env"]
    values: dict[str, str] = {}
    seen: set[Path] = set()
    for path in reversed(candidates):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            values.update(parse_env_file(resolved))
        except OSError:
            continue
    return values


def get_env_value(name: str, default: str = "", extra_paths: Iterable[Path] = ()) -> str:
    return os.getenv(name) or load_env_values(extra_paths).get(name, default)
