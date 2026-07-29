from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path

from .paths import runtime_root


LOG_FILE_NAME = "app2.log"

DAY_OR_TIME_PATTERN = re.compile(
    r"\d{1,2}:\d{2}|\u521a\u521a|\u4eca\u5929|\u6628\u5929|\u524d\u5929|\d{1,2}\u6708\d{1,2}\u65e5"
)
NAME_TITLE_SEPARATOR = "\u00b7"

TEXT_RECRUIT_MESSAGES = "\u62db\u8058\u6d88\u606f"
TEXT_PHONE_REQUEST_SENT = "\u60a8\u5411\u5bf9\u65b9\u53d1\u8d77\u4e86\u4ea4\u6362\u624b\u673a\u53f7\u7684\u7533\u8bf7"
TEXT_EXCHANGE_PHONE = "\u4ea4\u6362\u624b\u673a"
TEXT_REQUESTING = "\u7533\u8bf7\u4e2d"
TEXT_SEND_JOB = "\u53d1\u9001\u804c\u4f4d"
TEXT_EXCHANGE_WECHAT = "\u4ea4\u6362\u5fae\u4fe1"
TEXT_RESERVE_TALENT = "\u50a8\u5907\u4eba\u624d"
TEXT_NOT_SUITABLE = "\u4e0d\u5408\u9002"

KNOWN_DIALOGUE_TOOLS = {
    TEXT_SEND_JOB,
    TEXT_EXCHANGE_WECHAT,
    TEXT_RESERVE_TALENT,
    TEXT_NOT_SUITABLE,
}

PHONE_DONE_STATUSES = {
    "requested",
    "already_requested",
    "already_processed",
}


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()


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


def wait_message_frame(page, timeout: float = 10.0):
    deadline = time.time() + max(0.5, float(timeout))
    last_error = "iframe not ready"
    while time.time() < deadline:
        try:
            frame = page.get_frame("tag:iframe")
            if not frame:
                raise RuntimeError("message iframe missing")
            body = normalize_text(frame.run_js("return document.body ? document.body.innerText : '';"))
            if TEXT_RECRUIT_MESSAGES in body:
                return frame
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
    ['pointerdown', 'mousedown', 'pointerup', 'mouseup'].forEach(type => {
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


def get_message_ui_snapshot(frame) -> dict:
    js = """
    const norm = (s) => (s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
    const body = document.body ? document.body.innerText : '';
    return {
      name: norm(document.querySelector('.dialogue-header-username')?.innerText),
      profile: norm(document.querySelector('.dialogue-header-profile, .dialogue-header-career')?.innerText),
      tools: [...document.querySelectorAll('.tool.normal')].map(el => norm(el.innerText)).filter(Boolean),
      session_count: document.querySelectorAll('.message-item').length,
      body_length: body.length,
    };
    """
    data = frame.run_js(js) or {}
    return {
        "name": normalize_text(data.get("name", "")),
        "profile": normalize_text(data.get("profile", "")),
        "tools": [normalize_text(item) for item in data.get("tools", []) if normalize_text(item)],
        "session_count": int(data.get("session_count", 0) or 0),
        "body_length": int(data.get("body_length", 0) or 0),
    }


def dialogue_tools_ready(tools: list[str]) -> bool:
    values = set(tools)
    if TEXT_EXCHANGE_PHONE in values or TEXT_REQUESTING in values:
        return True
    return len(values.intersection(KNOWN_DIALOGUE_TOOLS)) >= 3


def wait_candidate_dialogue_ready(
    page,
    candidate_name: str,
    timeout: float = 35.0,
    stable_seconds: float = 1.5,
):
    expected_name = normalize_text(candidate_name)
    deadline = time.time() + max(2.0, float(timeout))
    stable_signature = None
    stable_since = None
    last_session_scan = 0.0
    last_log_at = 0.0
    last_snapshot = {
        "name": "",
        "profile": "",
        "tools": [],
        "session_count": 0,
        "body_length": 0,
    }
    last_error = "message UI has not rendered"

    while time.time() < deadline:
        try:
            frame = wait_message_frame(page, timeout=min(2.0, max(0.5, deadline - time.time())))
            snapshot = get_message_ui_snapshot(frame)
            last_snapshot = snapshot

            if snapshot["name"] == expected_name and dialogue_tools_ready(snapshot["tools"]):
                signature = (snapshot["name"], tuple(snapshot["tools"]))
                if signature != stable_signature:
                    stable_signature = signature
                    stable_since = time.time()
                elif stable_since and time.time() - stable_since >= stable_seconds:
                    return frame, snapshot
            else:
                stable_signature = None
                stable_since = None

            now = time.time()
            if (
                snapshot["name"] != expected_name
                and snapshot["session_count"] > 0
                and now - last_session_scan >= 3.0
            ):
                last_session_scan = now
                session = find_and_open_session(frame, expected_name, max_scrolls=8)
                if session:
                    continue

            if now - last_log_at >= 4.0:
                last_log_at = now
                append_log(
                    "waiting message UI: "
                    f"expected={expected_name} opened={snapshot['name'] or '-'} "
                    f"sessions={snapshot['session_count']} tools={snapshot['tools']}"
                )
        except Exception as exc:
            last_error = str(exc)

        time.sleep(0.4)

    raise RuntimeError(
        "candidate message UI not ready: "
        f"expected={expected_name}, opened={last_snapshot['name'] or '-'}, "
        f"sessions={last_snapshot['session_count']}, tools={last_snapshot['tools']}, "
        f"last_error={last_error}"
    )


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
            "status": "already_processed" if dialogue_tools_ready(before_tools) else "tool_missing",
            "before_tools": before_tools,
            "after_tools": before_tools,
        }

    js = """
    const norm = (s) => (s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
    const button = [...document.querySelectorAll('.tool.normal')].find(el => norm(el.innerText) === arguments[0]);
    if (!button) return false;
    ['pointerdown', 'mousedown', 'pointerup', 'mouseup'].forEach(type => {
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
    deadline = time.time() + 6.0
    after_tools = before_tools
    body_text = ""
    while clicked and time.time() < deadline:
        time.sleep(0.4)
        after_tools = get_tool_texts(frame)
        body_text = get_body_text(frame)
        if TEXT_REQUESTING in after_tools or TEXT_PHONE_REQUEST_SENT in body_text[-1200:]:
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


def exchange_phone_for_candidate(page, candidate_name: str, timeout: float = 35.0) -> dict:
    expected_name = normalize_text(candidate_name)
    frame, snapshot = wait_candidate_dialogue_ready(page, expected_name, timeout=timeout)

    result = click_exchange_phone_if_present(frame)
    result["expected_name"] = expected_name
    result["opened_name"] = snapshot["name"]
    return result
