from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

from .browser_connect import connect_chromium_page
from .contacted_candidates import load_contacted_candidates
from .paths import DEFAULT_DEBUG_PORT, runtime_root


MESSAGE_URL = "https://maimai.cn/ent/v41/im"
STATE_FILE_NAME = "app2_state.json"
LOG_FILE_NAME = "app2.log"
DEFAULT_POLL_SECONDS = 5.0
VISIBLE_REPLY_MAX = 8

DAY_OR_TIME_PATTERN = re.compile(
    r"\d{1,2}:\d{2}|\u521a\u521a|\u4eca\u5929|\u6628\u5929|\u524d\u5929|\d{1,2}\u6708\d{1,2}\u65e5"
)
NAME_TITLE_SEPARATOR = "\u00b7"

TEXT_RECRUIT_MESSAGES = "\u62db\u8058\u6d88\u606f"
TEXT_READ_MESSAGE = "\u5df2\u9605\u8bfb\u60a8\u7684\u6d88\u606f"
TEXT_PHONE_REQUEST_SENT = "\u60a8\u5411\u5bf9\u65b9\u53d1\u8d77\u4e86\u4ea4\u6362\u624b\u673a\u53f7\u7684\u7533\u8bf7"
TEXT_SMS_REMINDER = "\u7acb\u5373\u6c9f\u901a\u5df2\u77ed\u4fe1\u63d0\u9192\u5bf9\u65b9"
TEXT_EXCHANGE_PHONE = "\u4ea4\u6362\u624b\u673a"
TEXT_REQUESTING = "\u7533\u8bf7\u4e2d"
TEXT_SEND_JOB = "\u53d1\u9001\u804c\u4f4d"
TEXT_EXCHANGE_WECHAT = "\u4ea4\u6362\u5fae\u4fe1"
TEXT_RESERVE_TALENT = "\u50a8\u5907\u4eba\u624d"
TEXT_NOT_SUITABLE = "\u4e0d\u5408\u9002"
TEXT_BEFORE_REPLY_LIMIT = "\u5bf9\u65b9\u56de\u590d\u4e4b\u524d\uff0c\u6700\u591a\u53d1\u90016\u6761\u6d88\u606f"

PHONE_DONE_STATUSES = {
    "requested",
    "already_requested",
    "already_processed",
}


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()


def target_identity_key(item: dict) -> tuple[str, int, int]:
    return (
        normalize_text(item.get("name", "")),
        int(item.get("page_number", 0) or 0),
        int(item.get("page_list_index", item.get("list_index", 0)) or 0),
    )


def state_path() -> Path:
    return runtime_root() / STATE_FILE_NAME


def log_path() -> Path:
    return runtime_root() / LOG_FILE_NAME


def append_log(message: str) -> None:
    line = f"[{now_text()}] {message}"
    print(line)
    try:
        with log_path().open("a", encoding="utf-8") as file:
            file.write(line + "\n")
    except Exception:
        return


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def _save_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_state() -> dict:
    payload = _load_json(
        state_path(),
        {
            "targets": [],
            "events": [],
            "updated_at": "",
        },
    )
    payload.setdefault("targets", [])
    payload.setdefault("events", [])
    payload.setdefault("updated_at", "")
    return payload


def reset_state() -> Path:
    payload = {
        "targets": [],
        "events": [],
        "updated_at": now_text(),
    }
    _save_json(state_path(), payload)
    return state_path()


def save_state(state: dict) -> None:
    state["updated_at"] = now_text()
    _save_json(state_path(), state)


def load_seed_candidates() -> list[dict]:
    contacted = load_contacted_candidates().get("contacted_candidates", [])
    return contacted if isinstance(contacted, list) else []


def record_event(state: dict, name: str, event_type: str, payload: dict) -> None:
    events = state.setdefault("events", [])
    events.append(
        {
            "time": now_text(),
            "name": name,
            "type": event_type,
            "payload": payload,
        }
    )
    state["events"] = events[-300:]


def clean_name_prefix(value: str) -> str:
    text = normalize_text(value)
    return re.sub(r"^\d+\s+", "", text)


def parse_session_text(raw_text: str) -> dict:
    text = clean_name_prefix(raw_text)
    match = DAY_OR_TIME_PATTERN.search(text)
    if match:
        prefix = text[: match.start()].strip()
        time_text = normalize_text(match.group(0))
        preview = text[match.end() :].strip()
    else:
        prefix = text
        time_text = ""
        preview = ""

    if NAME_TITLE_SEPARATOR in prefix:
        name, title = prefix.split(NAME_TITLE_SEPARATOR, 1)
    else:
        name, title = prefix, ""

    return {
        "raw_text": text,
        "name": normalize_text(name),
        "title": normalize_text(title),
        "time_text": time_text,
        "preview": normalize_text(preview),
    }


def is_incoming_preview(preview: str) -> bool:
    text = normalize_text(preview)
    if not text:
        return False
    blocked = {
        TEXT_READ_MESSAGE,
        TEXT_PHONE_REQUEST_SENT,
        TEXT_SMS_REMINDER,
    }
    return text not in blocked


def seed_targets_from_matches(state: dict) -> dict:
    existing = {
        target_identity_key(item): dict(item)
        for item in state.get("targets", [])
        if target_identity_key(item)[0]
    }
    seeded = []
    seen = set()

    for item in load_seed_candidates():
        identity = target_identity_key(item)
        name = identity[0]
        if not name or identity in seen:
            continue
        seen.add(identity)

        base = existing.get(identity, {})
        target = {
            "name": name,
            "page_number": int(item.get("page_number", 0) or 0),
            "page_list_index": int(item.get("page_list_index", item.get("list_index", 0)) or 0),
            "list_index": int(item.get("list_index", item.get("page_list_index", 0)) or 0),
            "match_score": int(item.get("match_score", 0) or 0),
            "reason": item.get("reason", ""),
            "companies": list(item.get("companies", []) or []),
            "career_tags": list(item.get("career_tags", []) or []),
            "contact_status": item.get("contact_status", base.get("contact_status", "")),
            "contacted_at": item.get("contacted_at", base.get("contacted_at", "")),
            "thread_found": bool(base.get("thread_found", False)),
            "phone_exchange_status": normalize_text(base.get("phone_exchange_status", "")) or "pending",
            "last_session_time": base.get("last_session_time", ""),
            "last_session_preview": base.get("last_session_preview", ""),
            "last_incoming_preview": base.get("last_incoming_preview", ""),
            "last_messages": list(base.get("last_messages", []) or []),
            "reply_detected_at": base.get("reply_detected_at", ""),
            "reply_opened_at": base.get("reply_opened_at", ""),
            "opened_dialogue_name": base.get("opened_dialogue_name", ""),
            "opened_dialogue_profile": base.get("opened_dialogue_profile", ""),
            "updated_at": now_text(),
        }
        seeded.append(target)

    state["targets"] = seeded
    return state


def connect_message_frame():
    page = connect_chromium_page(search_url=MESSAGE_URL, port=DEFAULT_DEBUG_PORT)
    try:
        if MESSAGE_URL not in (page.url or ""):
            page.get(MESSAGE_URL)
            time.sleep(2)
    except Exception:
        page.get(MESSAGE_URL)
        time.sleep(2)

    deadline = time.time() + 10
    last_error = "iframe not ready"
    while time.time() < deadline:
        try:
            frame = page.get_frame("tag:iframe")
            if not frame:
                raise RuntimeError("message iframe missing")
            body = normalize_text(frame.run_js("return document.body ? document.body.innerText : '';"))
            if TEXT_RECRUIT_MESSAGES in body:
                return page, frame
            last_error = body[:120] or "message body empty"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"message iframe not ready: {last_error}")


def get_rendered_sessions(frame) -> list[dict]:
    js = """
    const norm = (s) => (s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
    return [...document.querySelectorAll('.message-item')].map((el, renderedIndex) => {
      const rect = el.getBoundingClientRect();
      return {
        rendered_index: renderedIndex,
        text: norm(el.innerText),
        class_name: String(el.className || ''),
        selected: String(el.className || '').includes('selected'),
        left: Math.round(rect.left),
        top: Math.round(rect.top),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      };
    }).sort((a, b) => a.top - b.top || a.left - b.left);
    """
    rows = frame.run_js(js) or []
    return [row for row in rows if normalize_text(row.get("text"))]


def reset_session_list_to_top(frame) -> None:
    js = """
    const list = document.querySelector('.virtualized-message-list') ||
      document.querySelector('.ReactVirtualized__Grid.ReactVirtualized__List.virtualized-message-list');
    if (!list) return false;
    list.scrollTop = 0;
    list.dispatchEvent(new Event('scroll', { bubbles: true }));
    return true;
    """
    frame.run_js(js)
    time.sleep(0.4)


def scroll_session_list(frame, delta: int = 420) -> bool:
    js = """
    const delta = arguments[0];
    const list = document.querySelector('.virtualized-message-list') ||
      document.querySelector('.ReactVirtualized__Grid.ReactVirtualized__List.virtualized-message-list');
    if (!list) return false;
    const before = list.scrollTop;
    list.scrollTop = before + delta;
    list.dispatchEvent(new Event('scroll', { bubbles: true }));
    return list.scrollTop !== before;
    """
    changed = bool(frame.run_js(js, int(delta)))
    time.sleep(0.5)
    return changed


def click_rendered_session(frame, rendered_index: int) -> bool:
    js = """
    const index = arguments[0];
    const rows = [...document.querySelectorAll('.message-item')];
    const row = rows[index];
    if (!row) return false;
    const target = row.querySelector('.message-detail') || row;
    ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(type => {
      target.dispatchEvent(new MouseEvent(type, {
        bubbles: true,
        cancelable: true,
        composed: true,
        view: window,
      }));
    });
    if (typeof target.click === 'function') target.click();
    return true;
    """
    clicked = bool(frame.run_js(js, int(rendered_index)))
    time.sleep(1.0)
    return clicked


def get_open_dialogue_identity(frame) -> dict:
    js = """
    const norm = (s) => (s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
    const name = document.querySelector('.dialogue-header-username');
    const profile = document.querySelector('.dialogue-header-profile, .dialogue-header-career');
    return {
      name: name ? norm(name.innerText) : '',
      profile: profile ? norm(profile.innerText) : '',
    };
    """
    data = frame.run_js(js) or {}
    return {
        "name": normalize_text(data.get("name", "")),
        "profile": normalize_text(data.get("profile", "")),
    }


def wait_dialogue_switched(frame, expected_name: str, timeout: float = 4.0) -> bool:
    expected = normalize_text(expected_name)
    deadline = time.time() + timeout
    while time.time() < deadline:
        identity = get_open_dialogue_identity(frame)
        if identity["name"] == expected:
            return True
        time.sleep(0.2)
    return False


def find_and_open_session(frame, target_name: str, max_scrolls: int = 20) -> dict | None:
    expected = normalize_text(target_name)
    reset_session_list_to_top(frame)
    seen_signatures = set()

    for _ in range(max_scrolls):
        rows = get_rendered_sessions(frame)
        for row in rows:
            parsed = parse_session_text(row["text"])
            if parsed["name"] != expected:
                continue
            if not click_rendered_session(frame, int(row["rendered_index"])):
                continue
            if not wait_dialogue_switched(frame, expected):
                continue
            identity = get_open_dialogue_identity(frame)
            parsed.update(row)
            parsed["dialogue"] = identity
            return parsed

        signature = tuple(row["text"] for row in rows[:5])
        if signature in seen_signatures:
            break
        seen_signatures.add(signature)

        if not scroll_session_list(frame):
            break

    return None


def get_tool_texts(frame) -> list[str]:
    js = """
    const norm = (s) => (s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
    return [...document.querySelectorAll('.tool.normal')].map(el => norm(el.innerText)).filter(Boolean);
    """
    values = frame.run_js(js) or []
    return [normalize_text(item) for item in values if normalize_text(item)]


def get_body_text(frame) -> str:
    return normalize_text(frame.run_js("return document.body ? document.body.innerText : '';"))


def click_exchange_phone_if_present(frame) -> dict:
    before_tools = get_tool_texts(frame)
    if TEXT_EXCHANGE_PHONE not in before_tools:
        if TEXT_REQUESTING in before_tools:
            return {
                "status": "already_requested",
                "before_tools": before_tools,
                "after_tools": before_tools,
            }
        return {
            "status": "already_processed" if before_tools else "tool_missing",
            "before_tools": before_tools,
            "after_tools": before_tools,
        }

    js = """
    const norm = (s) => (s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
    const button = [...document.querySelectorAll('.tool.normal')].find(el => norm(el.innerText) === arguments[0]);
    if (!button) return false;
    ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(type => {
      button.dispatchEvent(new MouseEvent(type, {
        bubbles: true,
        cancelable: true,
        composed: true,
        view: window,
      }));
    });
    if (typeof button.click === 'function') button.click();
    return true;
    """
    clicked = bool(frame.run_js(js, TEXT_EXCHANGE_PHONE))
    time.sleep(1.5)
    after_tools = get_tool_texts(frame)
    body_text = get_body_text(frame)
    if clicked and (TEXT_REQUESTING in after_tools or TEXT_PHONE_REQUEST_SENT in body_text[-1000:]):
        return {
            "status": "requested",
            "before_tools": before_tools,
            "after_tools": after_tools,
            "body_excerpt": body_text[-800:],
        }
    return {
        "status": "click_attempted" if clicked else "click_failed",
        "before_tools": before_tools,
        "after_tools": after_tools,
        "body_excerpt": body_text[-800:],
    }


def extract_dialogue_messages(frame, limit: int = 12) -> list[str]:
    js = """
    const norm = (s) => (s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
    const isVisible = (el) => {
      if (!el) return false;
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
    };
    const rows = [...document.querySelectorAll('div, span')]
      .filter(isVisible)
      .map(el => {
        const rect = el.getBoundingClientRect();
        return {
          text: norm(el.innerText),
          left: Math.round(rect.left),
          top: Math.round(rect.top),
          width: Math.round(rect.width),
        };
      })
      .filter(item => item.text)
      .filter(item => item.left > 320 && item.left < 1240)
      .filter(item => item.top > 100 && item.top < 420)
      .filter(item => item.width < 820)
      .filter(item => item.text.length < 180)
      .map(item => item.text);
    return [...new Set(rows)];
    """
    blocked = {
        TEXT_BEFORE_REPLY_LIMIT,
        TEXT_SMS_REMINDER,
        TEXT_SEND_JOB,
        TEXT_REQUESTING,
        TEXT_EXCHANGE_WECHAT,
        TEXT_RESERVE_TALENT,
        TEXT_NOT_SUITABLE,
        TEXT_EXCHANGE_PHONE,
    }
    messages = []
    for item in frame.run_js(js) or []:
        text = normalize_text(item)
        if not text or text in blocked:
            continue
        messages.append(text)
    return messages[-limit:]


def get_visible_reply_candidates(frame, target_names: set[str]) -> list[dict]:
    reset_session_list_to_top(frame)
    rows = get_rendered_sessions(frame)
    matches = []
    for row in rows:
        parsed = parse_session_text(row["text"])
        if parsed["name"] not in target_names:
            continue
        if not is_incoming_preview(parsed["preview"]):
            continue
        parsed.update(row)
        matches.append(parsed)
        if len(matches) >= VISIBLE_REPLY_MAX:
            break
    return matches


def open_reply_and_capture(frame, name: str) -> dict | None:
    session = find_and_open_session(frame, name, max_scrolls=12)
    if not session:
        return None
    return {
        "session": session,
        "messages": extract_dialogue_messages(frame),
        "body_excerpt": get_body_text(frame)[-1200:],
    }


def get_visible_target_names(frame, state: dict) -> list[str]:
    target_names = {
        normalize_text(item.get("name", ""))
        for item in state.get("targets", [])
        if normalize_text(item.get("name", ""))
    }
    ordered_names = []
    for session in snapshot_visible_sessions(frame, limit=50):
        name = normalize_text(session.get("name", ""))
        if not name or name not in target_names or name in ordered_names:
            continue
        ordered_names.append(name)
    return ordered_names


def process_phone_exchange_for_targets(frame, state: dict, limit: int | None = None) -> dict:
    ordered_targets = []
    for item in state.get("targets", []):
        if not target_identity_key(item)[0]:
            continue
        ordered_targets.append(item)

    append_log(f"exchange candidates queued: {len(ordered_targets)}")
    processed = 0
    for target in ordered_targets:
        if limit is not None and processed >= limit:
            break

        name = normalize_text(target.get("name", ""))
        status = normalize_text(target.get("phone_exchange_status", "")) or "pending"
        if status in PHONE_DONE_STATUSES:
            continue

        processed += 1
        append_log(f"start exchange phone: {name}")
        session = find_and_open_session(frame, name)
        if not session:
            target["thread_found"] = False
            target["phone_exchange_status"] = "thread_not_found"
            target["updated_at"] = now_text()
            record_event(state, name, "thread_not_found", {})
            append_log(f"session not found: {name}")
            save_state(state)
            continue

        target["thread_found"] = True
        target["last_session_time"] = session.get("time_text", "")
        target["last_session_preview"] = session.get("preview", "")
        target["opened_dialogue_name"] = session.get("dialogue", {}).get("name", "")
        target["opened_dialogue_profile"] = session.get("dialogue", {}).get("profile", "")

        result = click_exchange_phone_if_present(frame)
        target["phone_exchange_status"] = result["status"]
        target["updated_at"] = now_text()
        record_event(state, name, "phone_exchange", result)
        append_log(f"exchange phone result: {name} -> {result['status']}")
        save_state(state)

    if processed == 0:
        append_log("exchange queue empty or all targets already processed")

    return state


def monitor_replies(frame, state: dict, poll_seconds: float = DEFAULT_POLL_SECONDS) -> None:
    append_log("start monitoring replies")
    while True:
        target_lookup = {
            normalize_text(item.get("name", "")): item
            for item in state.get("targets", [])
            if normalize_text(item.get("name", ""))
        }
        visible = get_visible_reply_candidates(frame, set(target_lookup))
        for session in visible:
            target = target_lookup.get(session["name"])
            if not target:
                continue

            preview = session.get("preview", "")
            time_text = session.get("time_text", "")
            if (
                preview == target.get("last_incoming_preview", "")
                and time_text == target.get("last_session_time", "")
            ):
                continue

            capture = open_reply_and_capture(frame, session["name"])
            if not capture:
                continue

            target["thread_found"] = True
            target["last_session_time"] = capture["session"].get("time_text", time_text)
            target["last_session_preview"] = capture["session"].get("preview", preview)
            target["last_incoming_preview"] = capture["session"].get("preview", preview)
            target["last_messages"] = capture["messages"]
            target["opened_dialogue_name"] = capture["session"].get("dialogue", {}).get("name", "")
            target["opened_dialogue_profile"] = capture["session"].get("dialogue", {}).get("profile", "")
            target["reply_detected_at"] = now_text()
            target["reply_opened_at"] = now_text()
            target["updated_at"] = now_text()
            record_event(
                state,
                session["name"],
                "reply_detected",
                {
                    "session": capture["session"],
                    "messages": capture["messages"],
                    "body_excerpt": capture["body_excerpt"],
                },
            )
            append_log(
                f"reply opened: {session['name']} -> {capture['session'].get('preview', '')}"
            )
            save_state(state)

        time.sleep(max(1.0, float(poll_seconds)))


def monitor_replies_once(frame, state: dict) -> dict:
    target_lookup = {
        normalize_text(item.get("name", "")): item
        for item in state.get("targets", [])
        if normalize_text(item.get("name", ""))
    }
    visible = get_visible_reply_candidates(frame, set(target_lookup))
    for session in visible:
        target = target_lookup.get(session["name"])
        if not target:
            continue

        preview = session.get("preview", "")
        time_text = session.get("time_text", "")
        if (
            preview == target.get("last_incoming_preview", "")
            and time_text == target.get("last_session_time", "")
        ):
            continue

        capture = open_reply_and_capture(frame, session["name"])
        if not capture:
            continue

        target["thread_found"] = True
        target["last_session_time"] = capture["session"].get("time_text", time_text)
        target["last_session_preview"] = capture["session"].get("preview", preview)
        target["last_incoming_preview"] = capture["session"].get("preview", preview)
        target["last_messages"] = capture["messages"]
        target["opened_dialogue_name"] = capture["session"].get("dialogue", {}).get("name", "")
        target["opened_dialogue_profile"] = capture["session"].get("dialogue", {}).get("profile", "")
        target["reply_detected_at"] = now_text()
        target["reply_opened_at"] = now_text()
        target["updated_at"] = now_text()
        record_event(
            state,
            session["name"],
            "reply_detected",
            {
                "session": capture["session"],
                "messages": capture["messages"],
                "body_excerpt": capture["body_excerpt"],
            },
        )
        append_log(f"reply opened: {session['name']} -> {capture['session'].get('preview', '')}")
        save_state(state)

    return state


def snapshot_visible_sessions(frame, limit: int = 10) -> list[dict]:
    reset_session_list_to_top(frame)
    rows = get_rendered_sessions(frame)
    sessions = []
    for row in rows[:limit]:
        parsed = parse_session_text(row["text"])
        sessions.append(
            {
                "name": parsed["name"],
                "title": parsed["title"],
                "time_text": parsed["time_text"],
                "preview": parsed["preview"],
            }
        )
    return sessions


def run_message_followup(
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    exchange_only: bool = False,
    max_targets: int | None = None,
    monitor_once: bool = False,
) -> dict:
    state = seed_targets_from_matches(load_state())
    save_state(state)
    append_log(f"loaded targets: {len(state.get('targets', []))}")
    if not state.get("targets"):
        append_log("no contacted candidates available for follow-up")
        return state

    page, frame = connect_message_frame()
    append_log(f"connected message page: {page.url}")

    process_phone_exchange_for_targets(frame, state, limit=max_targets)
    if exchange_only:
        append_log("exchange_only mode finished")
        return state

    if monitor_once:
        return monitor_replies_once(frame, state)

    monitor_replies(frame, state, poll_seconds=poll_seconds)
    return state


def run_app2(
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    exchange_only: bool = False,
    max_targets: int | None = None,
) -> None:
    run_message_followup(
        poll_seconds=poll_seconds,
        exchange_only=exchange_only,
        max_targets=max_targets,
        monitor_once=False,
    )
