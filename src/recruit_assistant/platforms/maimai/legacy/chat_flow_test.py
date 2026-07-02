import json
import sys
import time
from pathlib import Path

from legacy.resume_extract import (
    clear_selection,
    connect_page,
    is_candidate_detail_open,
    open_first_candidate_card,
)
from src.maimai_auto.contacted_candidates import upsert_contacted_candidate
from src.maimai_auto.paths import runtime_root

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
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
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


def wait_candidate_switched(page, expected_name: str, previous_name: str, timeout: float = 2.5) -> bool:
    end_at = time.time() + timeout
    while time.time() < end_at:
        current_name = get_detail_name(page)
        if expected_name and current_name == expected_name:
            return True
        if expected_name and expected_name in current_name:
            return True
        if previous_name and current_name and current_name != previous_name:
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
    return panels[0] || null;
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

    try:
        ok = bool(page.run_js(js, "立即沟通"))
        clear_selection(page)
        time.sleep(0.4)
        return ok, info["text"]
    except Exception:
        return False, info["text"]


def wait_chat_modal(page, timeout: float = 2.5) -> bool:
    end_at = time.time() + timeout
    while time.time() < end_at:
        try:
            if page.ele("@class:settingText___13Are", timeout=0.3):
                return True
            if page.ele("text:索要简历", timeout=0.3):
                return True
            if page.ele("@tag:textarea", timeout=0.3):
                return True
            if page.ele("text:招聘立即沟通", timeout=0.3):
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
    ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(type => {
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
      ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(type => {
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
    const box = document.querySelector('textarea') || document.querySelector('[contenteditable="true"]');
    if (!box) return false;
    if (box.tagName === 'TEXTAREA') {
      box.focus();
      box.select && box.select();
      box.value = value;
      box.dispatchEvent(new Event('input', { bubbles: true }));
      box.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    }
    box.focus();
    box.innerHTML = '';
    box.textContent = value;
    box.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
    return true;
    """
    return bool(page.run_js(js, text))


def click_send_and_stay(page) -> bool:
    if click_modal_text(page, ["发送并留在此页", "发送后留在此页"]):
        return True
    return click_text_exact(page, ["发送并留在此页", "发送后留在此页"])


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
        "skipped_contacted": 0,
        "failed": 0,
    }

    for item in matched_candidates:
        target_index = item["page_list_index"]
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

        if not open_request_menu(page):
            print(f"[WARN] 未找到索要设置入口：第 {target_index} 个 - {name}")
            cleanup_chat_modal(page)
            stats["failed"] += 1
            continue

        if not wait_text_visible(page, "索要简历"):
            print(f"[WARN] 未等到索要简历选项：第 {target_index} 个 - {name}")
            cleanup_chat_modal(page)
            stats["failed"] += 1
            continue

        if not choose_request_resume(page):
            print(f"[WARN] 未成功选择索要简历：第 {target_index} 个 - {name}")
            cleanup_chat_modal(page)
            stats["failed"] += 1
            continue

        if not fill_message(page, greeting):
            print(f"[WARN] 未成功填写问候语：第 {target_index} 个 - {name}")

        if actual_send:
            if click_send_and_stay(page):
                print(f"[INFO] 已发送并留在此页：第 {target_index} 个 - {name}")
                upsert_contacted_candidate(
                    {
                        **item,
                        "name": name or item.get("name", ""),
                        "contact_status": "sent",
                        "contacted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )
                stats["processed"] += 1
                stats["sent"] += 1
                cleanup_chat_modal(page)
                time.sleep(0.5)
            else:
                print(f"[WARN] 未找到“发送并留在此页”按钮：第 {target_index} 个 - {name}")
                cleanup_chat_modal(page)
                stats["failed"] += 1
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
