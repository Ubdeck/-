import json
import sys
import time

from legacy.resume_extract import (
    clear_selection,
    connect_page,
    extract_resume_key_info,
    is_candidate_detail_open,
    open_first_candidate_card,
)
from src.maimai_auto.paths import runtime_root

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


OUTPUT_PATH = runtime_root() / "current_page_candidates.json"


def get_detail_header_name(page) -> str:
    js = """
    const root = document.querySelector('.left___1IRRn');
    if (!root || !root.children.length) return '';
    const lines = (root.children[0].innerText || '')
      .split('\\n')
      .map(item => item.trim())
      .filter(Boolean);
    return lines[0] || '';
    """
    return page.run_js(js) or ""


def get_current_page_candidates(page):
    js = """
    const cards = [...document.querySelectorAll('.card___3uNBJ')];
    return cards.map((card, index) => {
      const lines = (card.innerText || '')
        .split('\\n')
        .map(item => item.trim())
        .filter(Boolean);
      const rect = card.getBoundingClientRect();
      const borderColor = getComputedStyle(card).borderColor || '';
      return {
        index,
        name: lines[0] || '',
        lines: lines.slice(0, 8),
        left: Math.round(rect.left),
        top: Math.round(rect.top),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
        selected: borderColor === 'rgb(51, 117, 255)',
      };
    });
    """
    return page.run_js(js) or []


def reset_candidate_list_to_top(page) -> bool:
    js = """
    const cards = [...document.querySelectorAll('.card___3uNBJ')];
    if (!cards.length) return false;
    const seen = new Set();
    let node = cards[0].parentElement;
    while (node && node !== document.body) {
      if (!seen.has(node)) {
        seen.add(node);
        const style = getComputedStyle(node);
        const overflowY = style.overflowY || '';
        if (node.scrollHeight > node.clientHeight + 20 || overflowY === 'auto' || overflowY === 'scroll') {
          node.scrollTop = 0;
        }
      }
      node = node.parentElement;
    }
    cards[0].scrollIntoView({block: 'start', inline: 'nearest'});
    return true;
    """
    return bool(page.run_js(js))


def nudge_candidate_list(page):
    js = """
    const cards = [...document.querySelectorAll('.card___3uNBJ')];
    if (!cards.length) return false;
    const seen = new Set();
    let moved = false;
    let node = cards[0].parentElement;
    while (node && node !== document.body) {
      if (!seen.has(node)) {
        seen.add(node);
        const style = getComputedStyle(node);
        const overflowY = style.overflowY || '';
        if (node.scrollHeight > node.clientHeight + 20 || overflowY === 'auto' || overflowY === 'scroll') {
          node.scrollTop = Math.min(node.scrollHeight, 240);
          node.scrollTop = 0;
          moved = true;
        }
      }
      node = node.parentElement;
    }
    cards[0].scrollIntoView({block: 'start', inline: 'nearest'});
    return moved;
    """
    return bool(page.run_js(js))


def wait_candidate_list_ready(page, minimum: int = 8, timeout: float = 6.0) -> bool:
    end_at = time.time() + timeout
    best_count = 0
    stable_rounds = 0
    last_signature = ""
    while time.time() < end_at:
        reset_candidate_list_to_top(page)
        candidates = get_current_page_candidates(page)
        count = len(candidates)
        names = [item.get("name", "") for item in candidates[:8]]
        signature = "|".join(names) + f"#{count}"
        best_count = max(best_count, count)
        if signature and signature == last_signature:
            stable_rounds += 1
        else:
            stable_rounds = 0
            last_signature = signature
        if count >= minimum and stable_rounds >= 2:
            return True
        if count < minimum:
            nudge_candidate_list(page)
        time.sleep(0.25)
    return best_count > 0


def load_current_page_candidates(page, minimum: int = 8, retries: int = 3):
    best = []
    for attempt in range(1, max(1, retries) + 1):
        wait_candidate_list_ready(page, minimum=minimum, timeout=6.0 + attempt)
        candidates = get_current_page_candidates(page)
        if len(candidates) > len(best):
            best = candidates
        if len(candidates) >= minimum:
            return candidates
        print(f"[WARN] 候选人列表加载不足，第 {attempt}/{retries} 次仅拿到 {len(candidates)} 人，继续重试。")
        reset_candidate_list_to_top(page)
        nudge_candidate_list(page)
        time.sleep(0.5)
    return best


def get_page_marker(page):
    candidates = get_current_page_candidates(page)
    if not candidates:
        return ""
    first = candidates[0].get("name", "")
    last = candidates[-1].get("name", "")
    return f"{first}|{last}|{len(candidates)}"


def scroll_card_into_view(page, index: int):
    js = """
    const index = arguments[0];
    const cards = [...document.querySelectorAll('.card___3uNBJ')];
    const card = cards[index];
    if (!card) return null;
    card.scrollIntoView({block: 'center', inline: 'nearest'});
    const rect = card.getBoundingClientRect();
    const lines = (card.innerText || '')
      .split('\\n')
      .map(item => item.trim())
      .filter(Boolean);
    return {
      index,
      name: lines[0] || '',
      x: Math.round(rect.left + rect.width * 0.5),
      y: Math.round(rect.top + Math.min(rect.height * 0.4, 64)),
      left: Math.round(rect.left),
      top: Math.round(rect.top),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
    };
    """
    return page.run_js(js, index)


def js_click_card(page, index: int) -> bool:
    js = """
    const index = arguments[0];
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
    return bool(page.run_js(js, index))


def click_candidate_card(page, index: int) -> str:
    point = scroll_card_into_view(page, index)
    if not point:
        raise RuntimeError(f"未找到左侧第 {index + 1} 个候选人卡片")

    print(f"[INFO] 点击左侧候选人 {index + 1}: {point['name']} @ ({point['left']}, {point['top']})")

    if not js_click_card(page, index):
        raise RuntimeError(f"点击左侧第 {index + 1} 个候选人失败")
    clear_selection(page)

    return point["name"]


def wait_resume_switch(page, expected_name: str, previous_name: str, timeout: float = 2.5) -> bool:
    end_at = time.time() + timeout
    while time.time() < end_at:
        current_name = get_detail_header_name(page)
        if expected_name and current_name == expected_name:
            return True
        if expected_name and expected_name in current_name:
            return True
        if previous_name and current_name and current_name != previous_name:
            return True
        time.sleep(0.1)
    return False


def ensure_candidate_switched(page, index: int, expected_name: str, previous_name: str, retries: int = 3) -> bool:
    for attempt in range(1, retries + 1):
        click_candidate_card(page, index)
        if wait_resume_switch(page, expected_name, previous_name, timeout=2.5):
            time.sleep(0.15)
            return True
        print(f"[WARN] 第 {attempt}/{retries} 次切换候选人失败：{expected_name}")
        time.sleep(0.2)
    return False


def get_jump_next_page_point(page):
    js = """
    const texts = ['跳转至下一页', '下一页'];
    const isVisible = (el) => {
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
    };
    const norm = (text) => (text || '').replace(/\\s+/g, '');
    const nodes = [...document.querySelectorAll('body *')].filter(el => {
      const text = norm(el.innerText);
      if (!texts.includes(text)) return false;
      if (!isVisible(el)) return false;
      const rect = el.getBoundingClientRect();
      return rect.left < window.innerWidth * 0.45 && rect.top > window.innerHeight * 0.55;
    });
    nodes.sort((a, b) => {
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      return rb.top - ra.top || ra.left - rb.left;
    });
    const target = nodes[0];
    if (!target) return null;
    const rect = target.getBoundingClientRect();
    return {
      x: Math.round(rect.left + rect.width / 2),
      y: Math.round(rect.top + rect.height / 2),
      text: (target.innerText || '').trim(),
      left: Math.round(rect.left),
      top: Math.round(rect.top),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
    };
    """
    return page.run_js(js)


def click_next_page(page) -> bool:
    point = get_jump_next_page_point(page)
    if not point:
        print("[WARN] 未找到“跳转至下一页”按钮")
        return False
    try:
        print(
            f"[INFO] 点击翻页按钮：{point['text']} @ "
            f"({point['left']}, {point['top']}, {point['width']}, {point['height']})"
        )
        js = """
        const texts = ['跳转至下一页', '下一页'];
        const isVisible = (el) => {
          const style = getComputedStyle(el);
          const rect = el.getBoundingClientRect();
          return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
        };
        const norm = (text) => (text || '').replace(/\\s+/g, '');
        const nodes = [...document.querySelectorAll('body *')].filter(el => {
          const text = norm(el.innerText);
          if (!texts.includes(text)) return false;
          if (!isVisible(el)) return false;
          const rect = el.getBoundingClientRect();
          return rect.left < window.innerWidth * 0.45 && rect.top > window.innerHeight * 0.55;
        });
        nodes.sort((a, b) => {
          const ra = a.getBoundingClientRect();
          const rb = b.getBoundingClientRect();
          return rb.top - ra.top || ra.left - rb.left;
        });
        const target = nodes[0];
        if (!target) return false;
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
        ok = bool(page.run_js(js))
        if not ok:
            return False
        clear_selection(page)
        return True
    except Exception:
        return False


def wait_next_page_loaded(page, previous_marker: str, timeout: float = 8.0) -> bool:
    end_at = time.time() + timeout
    while time.time() < end_at:
        current_marker = get_page_marker(page)
        if current_marker and current_marker != previous_marker:
            return True
        time.sleep(0.2)
    return False


def ensure_detail_page(page) -> bool:
    if is_candidate_detail_open(page):
        return True
    if not open_first_candidate_card(page):
        print("[WARN] 未能先进入候选人详情页")
        return False
    time.sleep(0.35)
    if not is_candidate_detail_open(page):
        print("[WARN] 当前不在候选人详情页，无法处理左侧列表")
        return False
    return True


def extract_current_page(page_number: int = 1, max_candidates: int | None = None, page=None):
    page = page or connect_page()
    if not ensure_detail_page(page):
        OUTPUT_PATH.write_text("[]", encoding="utf-8")
        return []

    reset_candidate_list_to_top(page)
    candidates = load_current_page_candidates(page)
    if not candidates:
        raise RuntimeError("当前详情页左侧未找到候选人列表")

    limit = len(candidates) if max_candidates is None else min(len(candidates), max(0, int(max_candidates)))
    results = []
    for local_order, candidate in enumerate(candidates[:limit], start=1):
        index = candidate["index"]
        expected_name = candidate["name"]
        previous_name = get_detail_header_name(page)

        if not (candidate.get("selected") and previous_name == expected_name):
            if not ensure_candidate_switched(page, index, expected_name, previous_name):
                raise RuntimeError(f"第 {page_number} 页第 {local_order} 个候选人切换失败：{expected_name}")

        current_name = get_detail_header_name(page)
        if expected_name and current_name and expected_name not in current_name and current_name != expected_name:
            raise RuntimeError(
                f"第 {page_number} 页第 {local_order} 个候选人详情未切换成功：期望 {expected_name}，实际 {current_name}"
            )

        data = extract_resume_key_info(page) or {}
        data["list_index"] = local_order
        data["page_number"] = page_number
        data["page_list_index"] = local_order
        data["list_preview_name"] = expected_name
        results.append(data)
        print(f"[INFO] 已提取第 {page_number} 页 {local_order}/{limit}: {data.get('name') or expected_name}")

    OUTPUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[INFO] 当前页候选人提取完成，共 {len(results)} 人")
    print(f"[INFO] 结果已写入：{OUTPUT_PATH}")
    return results


def goto_next_page(page=None) -> bool:
    page = page or connect_page()
    previous_marker = get_page_marker(page)
    for attempt in range(1, 4):
        if not click_next_page(page):
            print(f"[WARN] 第 {attempt}/3 次翻页未找到“跳转至下一页”按钮。")
            time.sleep(0.5)
            continue
        if not wait_next_page_loaded(page, previous_marker):
            print(f"[WARN] 下一页加载确认失败，第 {attempt}/3 次重试。")
            time.sleep(0.5)
            continue
        time.sleep(0.8)
        if not is_candidate_detail_open(page):
            ensure_detail_page(page)
        reset_candidate_list_to_top(page)
        candidates = load_current_page_candidates(page)
        count = len(candidates)
        if count > 0:
            print(f"[INFO] 翻页成功，当前页已识别 {count} 个候选人。")
            return True
        print(f"[WARN] 翻页后候选人列表为空，第 {attempt}/3 次重试。")
        time.sleep(0.5)
    return False


def run_batch_extract(page_limit: int = 1, max_candidates: int | None = None):
    all_results = []
    total = max_candidates
    for page_number in range(1, max(1, int(page_limit)) + 1):
        remaining = None if total is None else max(0, int(total) - len(all_results))
        if remaining == 0:
            break
        page_results = extract_current_page(page_number, remaining)
        all_results.extend(page_results)
        if page_number >= int(page_limit):
            break
        if not goto_next_page():
            print(f"[WARN] 第 {page_number} 页处理完成后未找到“跳转至下一页”，提前结束。")
            break
    return all_results


if __name__ == "__main__":
    run_batch_extract()
