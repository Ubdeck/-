from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .paths import runtime_root


CONTACTED_FILE_NAME = "contacted_candidates.json"


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def contacted_candidates_path() -> Path:
    return runtime_root() / CONTACTED_FILE_NAME


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def load_contacted_candidates() -> dict:
    payload = _load_json(
        contacted_candidates_path(),
        {
            "contacted_candidates": [],
            "updated_at": "",
        },
    )
    payload.setdefault("contacted_candidates", [])
    payload.setdefault("updated_at", "")
    return payload


def save_contacted_candidates(payload: dict) -> Path:
    payload["updated_at"] = now_text()
    path = contacted_candidates_path()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def reset_contacted_candidates() -> Path:
    return save_contacted_candidates({"contacted_candidates": [], "updated_at": ""})


def upsert_contacted_candidate(record: dict) -> Path:
    payload = load_contacted_candidates()
    items = payload.get("contacted_candidates", [])

    name = str(record.get("name", "") or "").strip()
    page_number = int(record.get("page_number", 0) or 0)
    page_list_index = int(record.get("page_list_index", record.get("list_index", 0)) or 0)
    key = (name, page_number, page_list_index)

    normalized = dict(record)
    normalized["name"] = name
    normalized["page_number"] = page_number
    normalized["page_list_index"] = page_list_index
    normalized["list_index"] = int(record.get("list_index", page_list_index) or page_list_index)
    normalized["updated_at"] = now_text()

    replaced = False
    merged = []
    for item in items:
        item_key = (
            str(item.get("name", "") or "").strip(),
            int(item.get("page_number", 0) or 0),
            int(item.get("page_list_index", item.get("list_index", 0)) or 0),
        )
        if item_key == key:
            merged.append({**item, **normalized})
            replaced = True
        else:
            merged.append(item)

    if not replaced:
        merged.append(normalized)

    merged.sort(
        key=lambda item: (
            int(item.get("page_number", 0) or 0),
            int(item.get("page_list_index", item.get("list_index", 0)) or 0),
            str(item.get("name", "") or ""),
        )
    )
    payload["contacted_candidates"] = merged
    return save_contacted_candidates(payload)
