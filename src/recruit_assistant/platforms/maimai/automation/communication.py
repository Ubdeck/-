import json
import sys
import time
from pathlib import Path

from .resume import (
    clear_selection,
    connect_page,
    is_candidate_detail_open,
    open_first_candidate_card,
)
from ..contacts import upsert_contacted_candidate
from ..phone_exchange import PHONE_DONE_STATUSES, exchange_phone_for_candidate
from ..paths import runtime_root

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


MATCHED_PATH = runtime_root() / "matched_candidates.json"


def load_matched_candidates(path: Path, target_page: int | None = None) -> list[dict]:
    if not path.exists():
        return []

    data = json.loads(path.read_text(encoding="utf-8-sig"))
    candidates = []
    for item in data.get("matched_candidates", []):
        page_number = int(item.get("page_number", 0) or 0)
        if target_page is not None and page_number != int(target_page):
            continue

        page_list_index = int(item.get("page_list_index", item.get("list_index", 0)) or 0)
        if page_list_index <= 0:
            continue

        candidates.append(
            {
                "page_number": page_number,
                "page_list_index": page_list_index,
                "list_index": int(item.get("list_index", 0) or 0),
                "name": item.get("name", ""),
            }
        )

    candidates.sort(key=lambda item: item["page_list_index"])
    return candidates


def ensure_candidate_detail(page) -> bool:
    if is_candidate_detail_open(page):
        return True
    if not open_first_candidate_card(page):
        return False
    time.sleep(0.35)
    return is_candidate_detail_open(page)


def get_card_point(page, page_list_index: int):
    js = """
    const index = arguments[0] - 1;
    const cards = [...document.querySelectorAll('.card___3uNBJ')];
    const card = cards[index];
    if (!card) return null;
    card.scrollIntoView({block: 'center', inline: 'nearest'});
    const rect = card.getBoundingClientRect();
    const lines = (card.innerText || '').split('\\n').map(item => item.trim()).filter(Boolean);
    return {
      name: lines[0] || '',
      x: Math.round(rect.left + rect.width * 0.5),
      y: Math.round(rect.top + Math.min(rect.height * 0.4, 64)),
    };
    """
    return page.run_js(js, page_list_index)


def click_candidate(page, page_list_index: int) -> str:
    point = get_card_point(page, page_list_index)
    if not point:
        raise RuntimeError(f"未找到左侧第 {page_list_index} 个候选人")

    js = """
    const index = arguments[0] - 1;
    const cards = [...document.querySelectorAll('.card___3uNBJ')];
    const card = cards[index];
    if (!card) return false;
    card.scrollIntoView({block: 'center', inline: 'nearest'});
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup']) {
      card.dispatchEvent(new MouseEvent(type, {
        bubbles: true,
        cancelable: true,
        composed: true,
        view: window,
      }));
    }
    if (typeof card.click === 'function') card.click();
    return true;
    """
    if not bool(page.run_js(js, page_list_index)):
        raise RuntimeError(f"点击左侧第 {page_list_index} 个候选人失败")
    clear_selection(page)
    time.sleep(0.25)
    return point["name"]


def get_detail_name(page) -> str:
    js = """
    const root = document.querySelector('.left___1IRRn');
    if (!root || !root.children.length) return '';
    const lines = (root.children[0].innerText || '').split('\\n').map(item => item.trim()).filter(Boolean);
    return lines[0] || '';
    """
    return page.run_js(js) or ""


def wait_candidate_switched(page, expected_name: str, previous_name: str, timeout: float = 4.5) -> bool:
    end_at = time.time() + timeout
    while time.time() < end_at:
        current_name = get_detail_name(page)
        if expected_name and current_name == expected_name:
            return True
        if expected_name and expected_name in current_name:
            return True
        time.sleep(0.1)
    return False


def get_chat_button_info(page):
    js = """
    const wantedTexts = ['立即沟通', '沟通'];
    const isVisible = (el) => {
      if (!el) return false;
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
    };
    const exactText = (el) => ((el.innerText || '').replace(/\\s+/g, ' ').trim());

    const panels = [...document.querySelectorAll('.directChatButtonPanel, [class*="directChatButtonPanel"]')]
      .filter(el => isVisible(el))
      .map(el => {
        const rect = el.getBoundingClientRect();
        return {
          text: exactText(el),
          left: Math.round(rect.left),
          top: Math.round(rect.top),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        };
      })
      .filter(item => wantedTexts.includes(item.text))
      .filter(item => item.left > window.innerWidth * 0.62)
      .filter(item => item.top < window.innerHeight * 0.35);

    panels.sort((a, b) => a.top - b.top || a.left - b.left);
    if (panels.length) return panels[0];

    const fallback = [...document.querySelectorAll('.mui-btn, button, a')]
      .filter(el => isVisible(el))
      .filter(el => wantedTexts.includes(exactText(el)))
      .map(el => {
        const rect = el.getBoundingClientRect();
        return {
          text: exactText(el),
          left: Math.round(rect.left),
          top: Math.round(rect.top),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        };
      })
      .filter(item => item.left > window.innerWidth * 0.62)
      .filter(item => item.top < window.innerHeight * 0.55);
    fallback.sort((a, b) => a.top - b.top || a.left - b.left);
    return fallback[0] || null;
    """
    return page.run_js(js)


def click_chat_button(page) -> tuple[bool, str]:
    info = get_chat_button_info(page)
    if not info:
        return False, ""
    if info["text"] != "立即沟通":
        return False, info["text"]

    js = """
    const targetText = arguments[0];
    const isVisible = (el) => {
      if (!el) return false;
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
    };
    const exactText = (el) => ((el.innerText || '').replace(/\\s+/g, ' ').trim());
    const candidates = [...document.querySelectorAll('.directChatButtonPanel, [class*="directChatButtonPanel"]')]
      .filter(el => isVisible(el))
      .filter(el => exactText(el) === targetText)
      .filter(el => {
        const rect = el.getBoundingClientRect();
        return rect.left > window.innerWidth * 0.62 && rect.top < window.innerHeight * 0.35;
      });

    if (!candidates.length) return false;
    candidates.sort((a, b) => {
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      return ra.top - rb.top || ra.left - rb.left;
    });

    const panel = candidates[0];
    const target = panel.querySelector('.mui-btn, [class*="mui-btn"], button, a') || panel;
    target.scrollIntoView({block: 'center', inline: 'nearest'});

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

    try:
        ok = bool(page.run_js(js, "立即沟通"))
        clear_selection(page)
        time.sleep(0.4)
        return ok, info["text"]
    except Exception:
        return False, info["text"]


def wait_chat_modal(page, timeout: float = 4.5) -> bool:
    js = """
    const isVisible = el => {
      if (!el) return false;
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
    };
    return [...document.querySelectorAll('.mui-modal-wrap, [role="dialog"]')].some(root => {
      if (!isVisible(root)) return false;
      const text = (root.innerText || '').replace(/\s+/g, ' ').trim();
      return text.includes('招聘立即沟通') && !!root.querySelector('textarea, [contenteditable="true"]');
    });
    """
    end_at = time.time() + timeout
    while time.time() < end_at:
        try:
            if page.run_js(js):
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def open_request_menu(page) -> bool:
    try:
        ele = page.ele("@class:settingText___13Are", timeout=0.8)
    except Exception:
        ele = None
    if not ele:
        return False
    try:
        ele.click(by_js=True)
        time.sleep(0.2)
        return True
    except Exception:
        return False


def get_request_setting_text(page) -> str:
    js = """
    const node = document.querySelector('.settingText___13Are');
    return node ? ((node.innerText || '').replace(/\\s+/g, ' ').trim()) : '';
    """
    try:
        return page.run_js(js) or ""
    except Exception:
        return ""


def is_resume_request_active(page) -> bool:
    js = """
    const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
    const footer = document.querySelector('.autoRequestContainer___1q3Us');
    const footerText = footer ? norm(footer.innerText) : '';
    const setting = document.querySelector('.settingText___13Are');
    const settingText = setting ? norm(setting.innerText) : '';
    return footerText.includes('索要简历') || settingText.includes('索要简历');
    """
    try:
        return bool(page.run_js(js))
    except Exception:
        return False


def wait_text_visible(page, text: str, timeout: float = 2.0) -> bool:
    end_at = time.time() + timeout
    while time.time() < end_at:
        try:
            if page.ele(f"text:{text}", timeout=0.2):
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def click_modal_text(page, texts: list[str]) -> bool:
    js = """
    const texts = JSON.parse(arguments[0]);
    const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
    const isVisible = (el) => {
      if (!el) return false;
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
    };

    const roots = [];
    for (const el of document.querySelectorAll('.mui-modal, .mui-modal-wrap, [role="dialog"], .mui-popover, [role="tooltip"], [role="menu"]')) {
      if (!isVisible(el)) continue;
      const text = norm(el.innerText);
      if (!text) continue;
      if (
        text.includes('招聘立即沟通')
        || text.includes('索要简历')
        || text.includes('索要设置')
        || text.includes('发送后继续沟通')
        || text.includes('发送后留在此页')
        || text.includes('发送并留在此页')
      ) {
        roots.push(el);
      }
    }
    if (!roots.length) return false;

    const nodes = roots.flatMap(root => [...root.querySelectorAll('*')]).filter(el => {
      const text = norm(el.innerText);
      return texts.includes(text) && isVisible(el);
    });

    nodes.sort((a, b) => {
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      return ra.top - rb.top || ra.left - rb.left;
    });

    const target = nodes[0];
    if (!target) return false;
    const clickable = target.closest('.mui-btn, [class*="mui-btn"], button, a, li, [role="menuitem"], [role="option"]') || target;
    ['pointerdown', 'mousedown', 'pointerup', 'mouseup'].forEach(type => {
      clickable.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
    });
    if (typeof clickable.click === 'function') clickable.click();
    return true;
    """
    return bool(page.run_js(js, json.dumps(texts, ensure_ascii=False)))


def click_request_option(page, target_text: str) -> bool:
    js = """
    const targetText = arguments[0];
    const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
    const isVisible = (el) => {
      if (!el) return false;
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
    };
    const clickNode = (node) => {
      const target = node.closest('.mui-menu-item, [role="menuitem"], li, button, a, .mui-btn, [class*="mui-btn"]') || node;
      target.scrollIntoView({block: 'center', inline: 'nearest'});
      ['pointerdown', 'mousedown', 'pointerup', 'mouseup'].forEach(type => {
        target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, composed: true, view: window }));
      });
      if (typeof target.click === 'function') target.click();
      return true;
    };

    const nodes = [...document.querySelectorAll('body *')].filter(el => {
      if (!isVisible(el)) return false;
      const text = norm(el.innerText);
      if (text !== targetText) return false;
      const rect = el.getBoundingClientRect();
      return rect.left > window.innerWidth * 0.45 && rect.top > 0 && rect.top < window.innerHeight;
    });

    nodes.sort((a, b) => {
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      return ra.top - rb.top || ra.left - rb.left;
    });

    for (const node of nodes) {
      const root = node.closest('.mui-popover, [class*="popover"], .mui-modal, [role="menu"], [role="dialog"]') || node;
      if (!isVisible(root)) continue;
      if (!norm(root.innerText).includes(targetText)) continue;
      return clickNode(node);
    }

    return false;
    """
    return bool(page.run_js(js, target_text))


def click_text_exact(page, texts: list[str]) -> bool:
    js = """
    const texts = JSON.parse(arguments[0]);
    const isVisible = (el) => {
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
    };
    const nodes = [...document.querySelectorAll('body *')].filter(el => {
      const text = (el.innerText || '').trim();
      return texts.includes(text) && isVisible(el);
    });
    nodes.sort((a, b) => {
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      return ra.top - rb.top || ra.left - rb.left;
    });
    const target = nodes[0];
    if (!target) return false;
    ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(type => {
      target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
    });
    return true;
    """
    return bool(page.run_js(js, json.dumps(texts, ensure_ascii=False)))


def choose_request_resume(page) -> bool:
    if is_resume_request_active(page):
        return True

    if click_request_option(page, "索要简历"):
        time.sleep(0.25)
        if is_resume_request_active(page):
            return True

    if click_modal_text(page, ["索要简历"]):
        time.sleep(0.25)
        if is_resume_request_active(page):
            return True

    try:
        ele = page.ele("text:索要简历", timeout=0.8)
    except Exception:
        ele = None

    if ele:
        try:
            ele.click(by_js=True)
            time.sleep(0.25)
            if is_resume_request_active(page):
                return True
        except Exception:
            pass

    return is_resume_request_active(page)


def fill_message(page, text: str = "111") -> bool:
    js = """
    const value = arguments[0];
    const isVisible = el => {
      if (!el) return false;
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
    };
    const root = [...document.querySelectorAll('.mui-modal-wrap, [role="dialog"]')].find(el => {
      if (!isVisible(el)) return false;
      return (el.innerText || '').includes('招聘立即沟通');
    });
    const box = root && (root.querySelector('textarea') || root.querySelector('[contenteditable="true"]'));
    if (!box) return false;
    if (box.tagName === 'TEXTAREA') {
      box.focus();
      box.select && box.select();
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
      setter.call(box, value);
      box.dispatchEvent(new InputEvent('input', {
        bubbles: true,
        data: value,
        inputType: 'insertText',
      }));
      box.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    }
    box.focus();
    box.innerHTML = '';
    box.textContent = value;
    box.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
    return true;
    """
    deadline = time.time() + 2.0
    while time.time() < deadline:
        try:
            if page.run_js(js, text):
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def click_send_and_continue(page) -> bool:
    if click_modal_text(page, ["发送后继续沟通"]):
        return True
    return click_text_exact(page, ["发送后继续沟通"])


def capture_talent_page_state(page) -> dict:
    return {
        "tab_id": page.tab_id,
        "tab_ids": set(page.tab_ids),
        "url": page.url or "",
    }


def is_message_page(page) -> bool:
    try:
        return "/ent/v41/im" in (page.url or "")
    except Exception:
        return False


def wait_message_page(page, state: dict, timeout: float = 12.0):
    deadline = time.time() + timeout
    original_tab_id = state["tab_id"]
    original_tab_ids = state["tab_ids"]

    while time.time() < deadline:
        try:
            tab_ids = list(page.tab_ids)
        except Exception:
            tab_ids = []

        ordered_ids = [tab_id for tab_id in tab_ids if tab_id not in original_tab_ids]
        if original_tab_id in tab_ids:
            ordered_ids.append(original_tab_id)
        ordered_ids.extend(tab_id for tab_id in tab_ids if tab_id not in ordered_ids)

        for tab_id in ordered_ids:
            try:
                tab = page.get_tab(tab_id)
                if is_message_page(tab):
                    return tab, tab_id != original_tab_id
            except Exception:
                continue
        time.sleep(0.25)

    raise RuntimeError("发送后未进入招聘消息页面")


def wait_talent_page_ready(page, timeout: float = 10.0, stable_seconds: float = 2.5) -> bool:
    deadline = time.time() + timeout
    stable_since = None
    while time.time() < deadline:
        try:
            if "/ent/v41/recruit/talents" in (page.url or "") and is_candidate_detail_open(page):
                stable_since = stable_since or time.time()
                if time.time() - stable_since >= stable_seconds:
                    return True
            else:
                stable_since = None
        except Exception:
            stable_since = None
        time.sleep(0.25)
    return False


def restore_talent_page(browser, message_page, state: dict, opened_new_tab: bool):
    original_tab_id = state["tab_id"]
    if opened_new_tab:
        try:
            message_page.close()
        except Exception:
            browser.close_tabs(message_page.tab_id)
        browser.activate_tab(original_tab_id)
        talent_page = browser
    else:
        message_page.back()
        browser.activate_tab(original_tab_id)
        talent_page = browser

    deadline = time.time() + 18.0
    back_attempts = 0
    detail_open_attempted = False
    while time.time() < deadline:
        if is_message_page(talent_page) and back_attempts < 3:
            talent_page.back()
            back_attempts += 1
            detail_open_attempted = False
            time.sleep(0.75)
            continue

        try:
            on_talent_page = "/ent/v41/recruit/talents" in (talent_page.url or "")
        except Exception:
            on_talent_page = False

        if on_talent_page and not is_candidate_detail_open(talent_page) and not detail_open_attempted:
            detail_open_attempted = True
            ensure_candidate_detail(talent_page)

        if wait_talent_page_ready(talent_page, timeout=3.0, stable_seconds=2.5):
            clear_selection(talent_page)
            return talent_page

    raise RuntimeError("消息会话关闭后未能稳定恢复人才详情页")


def normalize_candidate_name(value: str) -> str:
    return " ".join(str(value or "").split())


def close_chat_modal(page) -> bool:
    try:
        ele = page.ele("@class:mui-modal-close", timeout=0.8)
    except Exception:
        ele = None
    if ele:
        try:
            ele.click(by_js=True)
            time.sleep(0.25)
            return True
        except Exception:
            pass
    return click_text_exact(page, ["关闭"])


def cleanup_chat_modal(page, retries: int = 3) -> bool:
    closed = False
    for _ in range(max(1, retries)):
        if close_chat_modal(page):
            closed = True
            time.sleep(0.25)
        else:
            break
    clear_selection(page)
    return closed


def run_chat_flow_test(
    greeting: str = "111",
    actual_send: bool = False,
    target_page: int | None = None,
    page=None,
):
    page = page or connect_page()
    if not ensure_candidate_detail(page):
        print("[WARN] 未能进入候选人详情页。")
        return {
            "matched_total": 0,
            "processed": 0,
            "sent": 0,
            "phone_exchanged": 0,
            "phone_exchange_failed": 0,
            "skipped_contacted": 0,
            "failed": 0,
        }

    matched_candidates = load_matched_candidates(MATCHED_PATH, target_page=target_page)
    if target_page is not None:
        print(f"[INFO] 本次处理第 {target_page} 页 {len(matched_candidates)} 个通过候选人")
    else:
        print(f"[INFO] 本次处理 {len(matched_candidates)} 个通过候选人")

    stats = {
        "matched_total": len(matched_candidates),
        "processed": 0,
        "sent": 0,
        "phone_exchanged": 0,
        "phone_exchange_failed": 0,
        "skipped_contacted": 0,
        "failed": 0,
    }

    for item in matched_candidates:
        target_index = item["page_list_index"]
        if not ensure_candidate_detail(page):
            print("[ERROR] 人才详情列表未能重新打开，停止处理本页后续候选人。")
            stats["failed"] += 1
            break
        previous_name = get_detail_name(page)

        try:
            name = click_candidate(page, target_index)
        except Exception as exc:
            print(f"[WARN] 点击候选人失败：第 {target_index} 个 - {exc}")
            stats["failed"] += 1
            continue

        if not wait_candidate_switched(page, name, previous_name):
            print(f"[WARN] 切换候选人详情失败：第 {target_index} 个 - {name}")
            stats["failed"] += 1
            continue

        expected_name = normalize_candidate_name(item.get("name", ""))
        clicked_name = normalize_candidate_name(name)
        if expected_name and clicked_name != expected_name:
            print(
                f"[WARN] 候选人姓名不一致，停止联系：第 {target_index} 个 "
                f"- AI结果 {expected_name} / 页面 {clicked_name}"
            )
            stats["failed"] += 1
            continue

        time.sleep(0.2)
        print(f"[INFO] 当前候选人：第 {target_index} 个 - {name}")

        clicked, button_text = click_chat_button(page)
        if button_text == "沟通":
            print(f"[INFO] 已沟通过，跳过：第 {target_index} 个 - {name}")
            upsert_contacted_candidate(
                {
                    **item,
                    "name": name or item.get("name", ""),
                    "contact_status": "already_contacted",
                    "contacted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
            stats["skipped_contacted"] += 1
            continue
        if not clicked:
            print(f"[WARN] 未找到立即沟通按钮：第 {target_index} 个 - {name}")
            stats["failed"] += 1
            continue

        if not wait_chat_modal(page):
            print(f"[WARN] 未打开沟通弹窗：第 {target_index} 个 - {name}")
            stats["failed"] += 1
            continue

        if not fill_message(page, greeting):
            print(f"[WARN] 未成功填写问候语：第 {target_index} 个 - {name}")
            cleanup_chat_modal(page)
            stats["failed"] += 1
            continue

        if actual_send:
            talent_state = capture_talent_page_state(page)
            if not click_send_and_continue(page):
                print(f"[WARN] 未找到“发送后继续沟通”按钮：第 {target_index} 个 - {name}")
                cleanup_chat_modal(page)
                stats["failed"] += 1
                continue

            contacted_at = time.strftime("%Y-%m-%d %H:%M:%S")
            stats["processed"] += 1
            stats["sent"] += 1
            print(f"[INFO] 已发送并进入继续沟通流程：第 {target_index} 个 - {name}")

            phone_result = {"status": "message_page_failed"}
            message_page = None
            opened_new_tab = False
            phone_exchange_done = False
            try:
                message_page, opened_new_tab = wait_message_page(page, talent_state)
                phone_result = exchange_phone_for_candidate(message_page, name)
                phone_status = phone_result.get("status", "unknown")
                if phone_status in PHONE_DONE_STATUSES:
                    phone_exchange_done = True
                    stats["phone_exchanged"] += 1
                    print(f"[INFO] 交换手机完成：第 {target_index} 个 - {name} ({phone_status})")
                else:
                    stats["phone_exchange_failed"] += 1
                    stats["failed"] += 1
                    print(f"[WARN] 交换手机失败：第 {target_index} 个 - {name} ({phone_status})")
            except Exception as exc:
                phone_result = {"status": "error", "error": str(exc)}
                stats["phone_exchange_failed"] += 1
                stats["failed"] += 1
                print(f"[WARN] 进入会话或交换手机失败：第 {target_index} 个 - {name} - {exc}")
            finally:
                upsert_contacted_candidate(
                    {
                        **item,
                        "name": name or item.get("name", ""),
                        "contact_status": "sent",
                        "contacted_at": contacted_at,
                        "phone_exchange_status": phone_result.get("status", "unknown"),
                    }
                )

            if not phone_exchange_done:
                print(
                    f"[ERROR] 交换手机未确认，保留消息页面并停止处理本页："
                    f"第 {target_index} 个 - {name}"
                )
                break

            try:
                page = restore_talent_page(page, message_page, talent_state, opened_new_tab)
                print(f"[INFO] 已关闭消息会话并返回人才页：第 {target_index} 个 - {name}")
            except Exception as exc:
                print(f"[ERROR] 无法返回人才页，停止处理本页后续候选人：{exc}")
                stats["failed"] += 1
                break
        else:
            print(f"[INFO] 已完成测试动作（未发送）：第 {target_index} 个 - {name}")
            cleanup_chat_modal(page)
            stats["processed"] += 1

        time.sleep(0.3)

    cleanup_chat_modal(page)
    print(f"[INFO] 当前页沟通统计：{json.dumps(stats, ensure_ascii=False)}")
    return stats


if __name__ == "__main__":
    run_chat_flow_test()
