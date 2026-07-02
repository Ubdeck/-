import json
import time
from typing import Dict, List, Sequence, Union

from DrissionPage import ChromiumPage
from src.maimai_auto.browser_connect import connect_chromium_page
from src.maimai_auto.paths import DEFAULT_DEBUG_PORT, DEFAULT_MAIMAI_URL


SEARCH_URL = DEFAULT_MAIMAI_URL

SEARCH_CONFIG = {
    "keyword": "研发",
    "keyword_mode": "所有",
    "filters": {
        "城市地区": ["杭州"],
        "学历要求": ["本科及以上", "不限"],
        "工作年限": ["1-3年"],
        "就职公司": ["百度"],
        "性别": ["男"],
    },
}

def connect_page() -> ChromiumPage:
    return connect_chromium_page(search_url=SEARCH_URL, port=DEFAULT_DEBUG_PORT)


def clear_selection(page: ChromiumPage) -> None:
    page.run_js("window.getSelection().removeAllRanges();")


def safe_click(ele, by_js: bool = True) -> bool:
    if not ele:
        return False
    try:
        ele.click(by_js=by_js)
        return True
    except Exception:
        return False


def dom_click(page: ChromiumPage, js_selector: str, *args) -> bool:
    js = """
    const selector = arguments[0];
    const args = Array.from(arguments).slice(1);
    const target = (new Function('args', selector))(args);
    if (!target) return false;
    target.scrollIntoView && target.scrollIntoView({block: 'center', inline: 'nearest'});
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
      target.dispatchEvent(new MouseEvent(type, {
        bubbles: true,
        cancelable: true,
        composed: true,
        view: window,
      }));
    }
    if (typeof target.click === 'function') target.click();
    return true;
    """
    try:
        return bool(page.run_js(js, js_selector, *args))
    except Exception:
        return False


def wait_text(page: ChromiumPage, text: str, timeout: float = 2):
    return page.ele(f"text:{text}", timeout=timeout)


def wait_attr(page: ChromiumPage, attr: str, value: str, timeout: float = 2):
    return page.ele(f"@{attr}:{value}", timeout=timeout)


def get_filter_titles(page: ChromiumPage) -> List[str]:
    js = """
    return [...document.querySelectorAll('span.search-item-text')]
      .map(el => (el.innerText || '').trim())
      .filter(Boolean);
    """
    return page.run_js(js) or []


def find_filter_anchor(page: ChromiumPage, label: str):
    anchors = page.eles("@class:search-item-text", timeout=2)
    for ele in anchors:
        try:
            text = (ele.text or "").strip()
            if text == label or text.startswith(label):
                rect = ele.rect
                if rect and rect.size[0] > 0 and rect.size[1] > 0:
                    return ele
        except Exception:
            continue
    return None


def click_filter_by_anchor(page: ChromiumPage, label: str) -> bool:
    anchor = find_filter_anchor(page, label)
    if not anchor:
        print(f"[WARN] 未找到筛选锚点：{label}")
        return False
    if safe_click(anchor):
        clear_selection(page)
        return True
    return dom_click(
        page,
        """
        const label = args[0];
        const visible = el => {
          const s = getComputedStyle(el);
          const r = el.getBoundingClientRect();
          return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
        };
        const norm = text => String(text || '').replace(/\\s+/g, ' ').trim();
        return [...document.querySelectorAll('.search-item-text, [class*="search-item-text"], body *')]
          .filter(el => visible(el) && (norm(el.innerText) === label || norm(el.innerText).startsWith(label)))
          .sort((a, b) => {
            const ra = a.getBoundingClientRect();
            const rb = b.getBoundingClientRect();
            return ra.top - rb.top || ra.left - rb.left;
          })[0] || null;
        """,
        label,
    )


def clear_all_filters(page: ChromiumPage) -> None:
    clear_btn = wait_text(page, "娓呯┖", timeout=2)
    if clear_btn:
        safe_click(clear_btn)
        time.sleep(0.8)
        clear_selection(page)


def menu_is_open(page: ChromiumPage, markers: Sequence[str]) -> bool:
    for marker in markers:
        if wait_text(page, marker, timeout=0.5):
            return True
        if wait_attr(page, "placeholder", marker, timeout=0.5):
            return True
    return False


def ensure_menu_open(page: ChromiumPage, label: str, markers: Sequence[str]) -> bool:
    if menu_is_open(page, markers):
        return True
    if not click_filter_by_anchor(page, label):
        return False
    time.sleep(0.8)
    return menu_is_open(page, markers)


def get_visible_menu_items(page: ChromiumPage, texts: Sequence[str]) -> List[Dict[str, Union[str, int]]]:
    js = """
    const texts = JSON.parse(arguments[0]);
    const isVisible = (el) => {
      const s = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
    };
    const out = [];
    for (const el of [...document.querySelectorAll('body *')]) {
      if (!isVisible(el)) continue;
      const t = (el.innerText || '').trim();
      if (!texts.includes(t)) continue;
      const r = el.getBoundingClientRect();
      out.push({
        text: t,
        cls: String(el.className || ''),
        x: Math.round(r.left + r.width / 2),
        y: Math.round(r.top + r.height / 2),
        w: Math.round(r.width),
        h: Math.round(r.height)
      });
    }
    out.sort((a, b) => a.y - b.y || a.x - b.x);
    return out;
    """
    return page.run_js(js, json.dumps(list(texts), ensure_ascii=False)) or []


def click_first_visible_option_by_keyword(page: ChromiumPage, keyword: str) -> str:
    js = """
    const keyword = JSON.parse(arguments[0]);
    const isVisible = (el) => {
      const s = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
    };
    const options = [...document.querySelectorAll('li[class*="option"]')]
      .filter(el => isVisible(el) && ((el.innerText || '').trim().includes(keyword)));
    options.sort((a, b) => {
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      return ra.top - rb.top || ra.left - rb.left;
    });
    const target = options[0];
    if (!target) return '';
    target.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    return (target.innerText || '').trim();
    """
    return page.run_js(js, json.dumps(keyword, ensure_ascii=False)) or ""


def click_visible_list_item(page: ChromiumPage, text: str) -> bool:
    js = """
    const targetText = JSON.parse(arguments[0]);
    const isVisible = (el) => {
      const s = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
    };
    const rows = [...document.querySelectorAll('[class*="list-item"], [class*="sub-select-item"]')]
      .filter(el => isVisible(el) && ((el.innerText || '').trim().includes(targetText)));
    rows.sort((a, b) => {
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      return ra.top - rb.top || ra.left - rb.left;
    });
    const target = rows[0];
    if (!target) return false;
    target.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    return true;
    """
    return bool(page.run_js(js, json.dumps(text, ensure_ascii=False)))


def click_visible_exact_item(page: ChromiumPage, text: str) -> bool:
    js = """
    const targetText = JSON.parse(arguments[0]);
    const isVisible = (el) => {
      const s = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
    };
    const rows = [...document.querySelectorAll('[class*="list-item"], [class*="sub-select-item"]')]
      .filter(el => isVisible(el) && ((el.innerText || '').trim() === targetText));
    rows.sort((a, b) => {
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      return ra.top - rb.top || ra.left - rb.left;
    });
    const target = rows[0];
    if (!target) return false;
    target.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    return true;
    """
    return bool(page.run_js(js, json.dumps(text, ensure_ascii=False)))


def click_visible_exact_item_by_class(
    page: ChromiumPage,
    text: str,
    class_keyword: str,
    prefer_right: bool = False,
) -> bool:
    js = """
    const targetText = JSON.parse(arguments[0]);
    const classKeyword = JSON.parse(arguments[1]);
    const preferRight = JSON.parse(arguments[2]);
    const isVisible = (el) => {
      const s = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
    };
    const rows = [...document.querySelectorAll('body *')]
      .filter(el => {
        if (!isVisible(el)) return false;
        const cls = String(el.className || '');
        if (!cls.includes(classKeyword)) return false;
        const text = (el.innerText || '').trim();
        return text === targetText;
      });
    rows.sort((a, b) => {
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      if (preferRight) {
        return rb.left - ra.left || ra.top - rb.top;
      }
      return ra.top - rb.top || ra.left - rb.left;
    });
    const target = rows[0];
    if (!target) return false;
    target.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    return true;
    """
    return bool(
        page.run_js(
            js,
            json.dumps(text, ensure_ascii=False),
            json.dumps(class_keyword),
            json.dumps(prefer_right),
        )
    )


def find_visible_text_element(
    page: ChromiumPage,
    text: str,
    min_left: int = 0,
    max_left: int = 1200,
    min_top: int = 0,
    max_top: int = 1200,
):
    try:
        candidates = page.eles(f"text:{text}", timeout=2)
    except Exception:
        return None
    for ele in candidates:
        try:
            rect = ele.rect
            if not rect or rect.size[0] <= 0 or rect.size[1] <= 0:
                continue
            x, y = rect.midpoint
            if min_left <= x <= max_left and min_top <= y <= max_top:
                return ele
        except Exception:
            continue
    return None


def click_ele_center(ele) -> bool:
    if not ele:
        return False
    return safe_click(ele)


def click_list_item_in_region(
    page: ChromiumPage,
    text: str,
    min_left: int,
    max_left: int,
    min_top: int,
    max_top: int,
    class_keyword: str = "list-item",
) -> bool:
    js = """
    const targetText = JSON.parse(arguments[0]);
    const minLeft = JSON.parse(arguments[1]);
    const maxLeft = JSON.parse(arguments[2]);
    const minTop = JSON.parse(arguments[3]);
    const maxTop = JSON.parse(arguments[4]);
    const classKeyword = JSON.parse(arguments[5]);
    const isVisible = (el) => {
      const s = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
    };
    const rows = [...document.querySelectorAll(`body *`)].filter(el => {
      if (!isVisible(el)) return false;
      const text = (el.innerText || '').trim();
      if (text !== targetText) return false;
      const cls = String(el.className || '');
      if (!cls.includes(classKeyword)) return false;
      const r = el.getBoundingClientRect();
      return r.left >= minLeft && r.left <= maxLeft && r.top >= minTop && r.top <= maxTop;
    });
    rows.sort((a, b) => {
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      return ra.top - rb.top || ra.left - rb.left;
    });
    const target = rows[0];
    if (!target) return false;
    target.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    return true;
    """
    return bool(
        page.run_js(
            js,
            json.dumps(text, ensure_ascii=False),
            json.dumps(min_left),
            json.dumps(max_left),
            json.dumps(min_top),
            json.dumps(max_top),
            json.dumps(class_keyword),
        )
    )


def click_text_center_in_region(
    page: ChromiumPage,
    text: str,
    min_left: int,
    max_left: int,
    min_top: int,
    max_top: int,
) -> bool:
    js = """
    const targetText = JSON.parse(arguments[0]);
    const minLeft = JSON.parse(arguments[1]);
    const maxLeft = JSON.parse(arguments[2]);
    const minTop = JSON.parse(arguments[3]);
    const maxTop = JSON.parse(arguments[4]);
    const isVisible = (el) => {
      const s = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
    };
    const rows = [...document.querySelectorAll('body *')].filter(el => {
      if (!isVisible(el)) return null;
      const text = (el.innerText || '').trim();
      if (text !== targetText) return null;
      const r = el.getBoundingClientRect();
      if (r.left < minLeft || r.right > maxLeft || r.top < minTop || r.bottom > maxTop) return null;
      return true;
    });
    rows.sort((a, b) => {
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      return rb.width - ra.width || ra.top - rb.top || ra.left - rb.left;
    });
    const target = rows[0];
    if (!target) return false;
    target.scrollIntoView({block: 'center', inline: 'nearest'});
    target.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    if (typeof target.click === 'function') target.click();
    return true;
    """
    clicked = page.run_js(
        js,
        json.dumps(text, ensure_ascii=False),
        json.dumps(min_left),
        json.dumps(max_left),
        json.dumps(min_top),
        json.dumps(max_top),
    )
    if clicked:
        clear_selection(page)
    return bool(clicked)


def click_nth_item_in_region(
    page: ChromiumPage,
    index: int,
    min_left: int,
    max_left: int,
    min_top: int,
    max_top: int,
    class_keyword: str,
) -> bool:
    js = """
    const index = JSON.parse(arguments[0]);
    const minLeft = JSON.parse(arguments[1]);
    const maxLeft = JSON.parse(arguments[2]);
    const minTop = JSON.parse(arguments[3]);
    const maxTop = JSON.parse(arguments[4]);
    const classKeyword = JSON.parse(arguments[5]);
    const isVisible = (el) => {
      const s = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
    };
    const rows = [...document.querySelectorAll('body *')].filter(el => {
      if (!isVisible(el)) return false;
      const cls = String(el.className || '');
      if (!cls.includes(classKeyword)) return false;
      const r = el.getBoundingClientRect();
      return r.left >= minLeft && r.left <= maxLeft && r.top >= minTop && r.top <= maxTop;
    });
    rows.sort((a, b) => {
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      return ra.top - rb.top || ra.left - rb.left;
    });
    const target = rows[index];
    if (!target) return false;
    target.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    return true;
    """
    return bool(
        page.run_js(
            js,
            json.dumps(index),
            json.dumps(min_left),
            json.dumps(max_left),
            json.dumps(min_top),
            json.dumps(max_top),
            json.dumps(class_keyword),
        )
    )


def close_active_filter_popup(page: ChromiumPage) -> None:
    try:
        page.run_js(
            """
            document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true}));
            document.dispatchEvent(new KeyboardEvent('keyup', {key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true}));
            """
        )
    except Exception:
        pass
    time.sleep(0.2)
    clear_selection(page)
    page.run_js(
        """
        const target = document.querySelector('input[placeholder*="搜索提示"]')
          || document.querySelector('input.ant-input')
          || document.body;
        for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
          target.dispatchEvent(new MouseEvent(type, {
            bubbles: true,
            cancelable: true,
            composed: true,
            view: window,
          }));
        }
        """
    )
    time.sleep(0.3)
    clear_selection(page)


def click_gender_row_by_order(page: ChromiumPage, target_value: str) -> bool:
    order_map = {"不限": 0, "男": 1, "女": 2}
    target_index = order_map.get(target_value)
    if target_index is None:
        return False
    js = """
    const targetIndex = JSON.parse(arguments[0]);
    const isVisible = (el) => {
      const s = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
    };
    const rows = [...document.querySelectorAll('[class*="list-item"]')]
      .filter(el => {
        if (!isVisible(el)) return false;
        const r = el.getBoundingClientRect();
        return r.left < 380 && r.top >= 320 && r.top <= 460;
      });
    rows.sort((a, b) => {
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      return ra.top - rb.top || ra.left - rb.left;
    });
    const target = rows[targetIndex];
    if (!target) return false;
    target.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    return true;
    """
    return bool(page.run_js(js, json.dumps(target_index)))


def input_keyword(page: ChromiumPage, keyword: str) -> None:
    if not keyword:
        return

    search_input = find_keyword_input(page, timeout=2)
    if not search_input:
        print("[WARN] 未找到搜索输入框")
        return

    search_input.click(by_js=True)
    search_input.clear(by_js=True)
    clear_selection(page)
    search_input.input(keyword, clear=True)
    time.sleep(0.6)
    try:
        search_input.click(by_js=True)
    except Exception:
        pass
    clear_selection(page)


def find_keyword_input(page: ChromiumPage, timeout: float = 2):
    candidates = [
        '搜索提示：输入带双引号的完整词组，如"算法工程师"，可精准匹配人才',
        "按职位/公司等搜人才",
    ]
    for placeholder in candidates:
        try:
            ele = wait_attr(page, "placeholder", placeholder, timeout=timeout)
        except Exception:
            ele = None
        if ele:
            return ele
    try:
        eles = page.eles("@tag:input", timeout=timeout)
    except Exception:
        return None
    for ele in eles:
        try:
            cls = str(ele.attr("class") or "")
            rect = ele.rect
            if "ant-input" in cls and rect and rect.size[0] > 300 and rect.location[1] < 260:
                return ele
        except Exception:
            continue
    return None


def get_keyword_mode_trigger_point(page: ChromiumPage):
    js = """
    const isVisible = (el) => {
      if (!el) return false;
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
    };
    const norm = (text) => (text || '').replace(/\\s+/g, ' ').trim();
    const input = [...document.querySelectorAll('input')].find(el => {
      const placeholder = norm(el.getAttribute('placeholder') || '');
      const cls = String(el.className || '');
      const rect = el.getBoundingClientRect();
      return (
        placeholder.includes('搜索提示') ||
        (cls.includes('ant-input') && rect.width > 300 && rect.top < 260)
      );
    }) || null;
    if (!input) return null;
    const inputRect = input.getBoundingClientRect();
    const triggers = [...document.querySelectorAll('.mui-select')]
      .filter(el => isVisible(el))
      .map(el => {
        const rect = el.getBoundingClientRect();
        return {
          el,
          text: norm(el.innerText),
          left: rect.left,
          top: rect.top,
          right: rect.right,
          bottom: rect.bottom,
          width: rect.width,
          height: rect.height,
        };
      })
      .filter(item => item.left >= inputRect.right + 40)
      .filter(item => item.left <= inputRect.right + 170)
      .filter(item => Math.abs(item.top - inputRect.top) <= 24)
      .filter(item => item.width >= 40 && item.width <= 90)
      .sort((a, b) => a.left - b.left || a.top - b.top);
    const target = triggers[0];
    if (!target) return null;
    return {
      x: Math.round(target.left + target.width / 2),
      y: Math.round(target.top + target.height / 2),
      text: target.text,
      left: Math.round(target.left),
      top: Math.round(target.top),
      width: Math.round(target.width),
      height: Math.round(target.height),
    };
    """
    return page.run_js(js)


def get_keyword_mode_option_point(page: ChromiumPage, mode_text: str):
    js = """
    const wanted = JSON.parse(arguments[0]);
    const isVisible = (el) => {
      if (!el) return false;
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
    };
    const norm = (text) => (text || '').replace(/\\s+/g, ' ').trim();
    const options = [...document.querySelectorAll('.mui-select-dropdown .mui-select-item-option, .mui-select-item-option')]
      .filter(el => isVisible(el))
      .map(el => {
        const rect = el.getBoundingClientRect();
        return {
          text: norm(el.innerText),
          left: rect.left,
          top: rect.top,
          width: rect.width,
          height: rect.height,
        };
      })
      .filter(item => item.text === wanted)
      .sort((a, b) => a.top - b.top || a.left - b.left);
    const target = options[0];
    if (!target) return null;
    return {
      x: Math.round(target.left + target.width / 2),
      y: Math.round(target.top + target.height / 2),
      text: target.text,
      left: Math.round(target.left),
      top: Math.round(target.top),
      width: Math.round(target.width),
      height: Math.round(target.height),
    };
    """
    return page.run_js(js, json.dumps(mode_text, ensure_ascii=False))


def set_keyword_mode(page: ChromiumPage, mode_text: str) -> None:
    if not mode_text:
        return
    trigger = get_keyword_mode_trigger_point(page)
    if not trigger:
        print("[WARN] 未找到关键词模式下拉框")
        return
    if not dom_click(
        page,
        """
        const isVisible = el => {
          if (!el) return false;
          const style = getComputedStyle(el);
          const rect = el.getBoundingClientRect();
          return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
        };
        const norm = text => String(text || '').replace(/\\s+/g, ' ').trim();
        const input = [...document.querySelectorAll('input')].find(el => {
          const placeholder = norm(el.getAttribute('placeholder') || '');
          const cls = String(el.className || '');
          const rect = el.getBoundingClientRect();
          return placeholder.includes('搜索提示') || (cls.includes('ant-input') && rect.width > 300 && rect.top < 260);
        });
        if (!input) return null;
        const inputRect = input.getBoundingClientRect();
        return [...document.querySelectorAll('.mui-select')]
          .filter(el => isVisible(el))
          .filter(el => {
            const rect = el.getBoundingClientRect();
            return rect.left >= inputRect.right + 40
              && rect.left <= inputRect.right + 170
              && Math.abs(rect.top - inputRect.top) <= 24
              && rect.width >= 40
              && rect.width <= 90;
          })
          .sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left)[0] || null;
        """,
    ):
        print("[WARN] 点击关键词模式下拉框失败")
        return

    time.sleep(0.5)
    option = get_keyword_mode_option_point(page, mode_text)
    if not option:
        print(f"[WARN] 未找到关键词模式选项：{mode_text}")
        clear_selection(page)
        return

    if not dom_click(
        page,
        """
        const wanted = args[0];
        const isVisible = el => {
          if (!el) return false;
          const style = getComputedStyle(el);
          const rect = el.getBoundingClientRect();
          return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
        };
        const norm = text => String(text || '').replace(/\\s+/g, ' ').trim();
        return [...document.querySelectorAll('.mui-select-dropdown .mui-select-item-option, .mui-select-item-option')]
          .filter(el => isVisible(el) && norm(el.innerText) === wanted)
          .sort((a, b) => {
            const ra = a.getBoundingClientRect();
            const rb = b.getBoundingClientRect();
            return ra.top - rb.top || ra.left - rb.left;
          })[0] || null;
        """,
        mode_text,
    ):
        print("[WARN] 点击关键词模式选项失败")
        return

    time.sleep(0.4)
    clear_selection(page)
    print(f"[INFO] 已设置关键词模式：{mode_text}")


def apply_city_filter(page: ChromiumPage, values: Sequence[str]) -> None:
    if not values:
        return
    city_name = values[0]
    if not ensure_menu_open(page, "城市地区", ["请输入城市地区"]):
        print("[WARN] 城市地区弹层未打开")
        return
    search_input = wait_attr(page, "placeholder", "请输入城市地区", timeout=2)
    if not search_input:
        print("[WARN] 未找到城市地区输入框")
        return
    search_input.click(by_js=True)
    search_input.clear(by_js=True)
    clear_selection(page)
    search_input.input(city_name, clear=True)
    time.sleep(1)
    chosen = click_first_visible_option_by_keyword(page, city_name)
    if not chosen:
        print(f"[WARN] 未找到城市地区候选项：{city_name}")
        return
    time.sleep(0.8)
    close_active_filter_popup(page)
    clear_selection(page)


def apply_education_filter(page: ChromiumPage, values: Sequence[str]) -> None:
    if not values:
        return
    first_level = values[0]
    second_level = values[1] if len(values) > 1 else None
    menu_labels = ["专科及以上", "本科及以上", "硕士及以上", "博士", "自定义"]
    first_level_index_map = {
        "专科及以上": 0,
        "本科及以上": 1,
        "硕士及以上": 2,
        "博士": 3,
    }
    bachelor_second_index_map = {
        "不限": 0,
        "只看统招本科": 1,
    }
    click_filter_by_anchor(page, "学历要求")
    time.sleep(0.8)
    if not menu_is_open(page, menu_labels):
        print("[WARN] 未读取到学历要求菜单")
        return
    if first_level == "本科及以上":
        if not click_nth_item_in_region(page, 1, 240, 560, 290, 450, "list-item"):
            print("[WARN] 未找到本科及以上一级菜单")
            return
        time.sleep(0.6)
        second_level = second_level or "不限"
        second_index = bachelor_second_index_map.get(second_level)
        if second_index is None:
            print(f"[WARN] 不支持的本科二级菜单：{second_level}")
            return
        if not click_nth_item_in_region(page, second_index, 500, 720, 320, 420, "sub-select-item"):
            print(f"[WARN] 未找到本科二级菜单：{second_level}")
            return
        time.sleep(0.8)
        clear_selection(page)
        close_active_filter_popup(page)
        return
    first_index = first_level_index_map.get(first_level)
    if first_index is None:
        print(f"[WARN] 不支持的学历一级菜单：{first_level}")
        return
    if not click_nth_item_in_region(page, first_index, 240, 560, 290, 450, "list-item"):
        print(f"[WARN] 未找到学历一级菜单：{first_level}")
        return
    time.sleep(0.8)
    clear_selection(page)
    close_active_filter_popup(page)


def apply_work_experience_filter(page: ChromiumPage, values: Sequence[str]) -> None:
    if not values:
        return
    first_level = values[0]
    second_level = values[1] if len(values) > 1 else None
    menu_labels = ["在校/应届", "1年以内", "1-3年", "3-5年", "5-10年", "10年以上", "自定义"]
    first_level_index_map = {
        "在校/应届": 0,
        "1年以内": 1,
        "1-3年": 2,
        "3-5年": 3,
        "5-10年": 4,
        "10年以上": 5,
    }
    click_filter_by_anchor(page, "工作年限")
    time.sleep(0.8)
    if not menu_is_open(page, menu_labels):
        print("[WARN] 未读取到工作年限菜单")
        return
    if first_level == "在校/应届":
        clicked_first = click_visible_exact_item_by_class(page, "在校/应届", "list-item")
        if not clicked_first:
            clicked_first = click_nth_item_in_region(page, 0, 380, 700, 290, 520, "list-item")
        if not clicked_first:
            print("[WARN] 未找到在校/应届一级菜单")
            return
        time.sleep(0.6)
        second_level = second_level or "不限"
        clicked = click_visible_exact_item_by_class(page, second_level, "sub-select-item", prefer_right=True)
        if not clicked:
            clicked = click_text_center_in_region(page, second_level, 620, 1040, 180, 580)
        if not clicked:
            clicked = click_list_item_in_region(page, second_level, 620, 1040, 180, 580, "sub-select-item")
        if not clicked:
            print(f"[WARN] 未找到在校/应届二级菜单：{second_level}")
            return
        time.sleep(0.8)
        clear_selection(page)
        close_active_filter_popup(page)
        return
    first_index = first_level_index_map.get(first_level)
    if first_index is None:
        print(f"[WARN] 不支持的工作年限一级菜单：{first_level}")
        return
    clicked_first = click_visible_exact_item_by_class(page, first_level, "list-item")
    if not clicked_first:
        clicked_first = click_nth_item_in_region(page, first_index, 380, 700, 290, 520, "list-item")
    if not clicked_first:
        print(f"[WARN] 未找到工作年限一级菜单：{first_level}")
        return
    time.sleep(0.8)
    clear_selection(page)
    close_active_filter_popup(page)


def apply_company_filter(page: ChromiumPage, values: Sequence[str]) -> None:
    if not values:
        return
    for company_name in values:
        if not ensure_menu_open(page, "就职公司", ["请输入就职公司"]):
            print("[WARN] 就职公司弹层未打开")
            return
        search_input = wait_attr(page, "placeholder", "请输入就职公司", timeout=2)
        if not search_input:
            print("[WARN] 未找到就职公司输入框")
            return
        search_input.click(by_js=True)
        search_input.clear(by_js=True)
        clear_selection(page)
        search_input.input(company_name, clear=True)
        time.sleep(1)
        items = get_visible_menu_items(page, [company_name])
        dropdown_items = [
            item for item in items
            if item["text"] == company_name and "list-item" in str(item["cls"])
        ]
        if not dropdown_items:
            print(f"[WARN] 未找到就职公司候选项：{company_name}")
            return
        if not click_visible_exact_item_by_class(page, company_name, "list-item"):
            print(f"[WARN] 点击就职公司候选项失败：{company_name}")
            return
        time.sleep(0.8)
        clear_selection(page)
    close_active_filter_popup(page)


def apply_gender_filter(page: ChromiumPage, values: Sequence[str]) -> None:
    if not values:
        return
    target_value = values[0]
    gender_index_map = {
        "不限": 0,
        "男": 1,
        "女": 2,
    }
    close_active_filter_popup(page)
    clear_selection(page)
    time.sleep(0.3)
    click_filter_by_anchor(page, "性别")
    time.sleep(0.8)
    if not menu_is_open(page, ["不限", "男", "女"]):
        print("[WARN] 性别弹层未打开")
        return
    target_index = gender_index_map.get(target_value)
    if target_index is None:
        print(f"[WARN] 不支持的性别菜单项：{target_value}")
        return
    js = """
    const targetIndex = JSON.parse(arguments[0]);
    const isVisible = (el) => {
      const s = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
    };
    const rows = [...document.querySelectorAll('body *')].filter(el => {
      if (!isVisible(el)) return false;
      const cls = String(el.className || '');
      if (!cls.includes('list-item')) return false;
      const r = el.getBoundingClientRect();
      return r.top >= 330 && r.top <= 450 && r.width >= 100 && r.width <= 160 && r.height >= 28 && r.height <= 40;
    });
    rows.sort((a, b) => {
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      return ra.top - rb.top || ra.left - rb.left;
    });
    const target = rows[targetIndex];
    if (!target) return false;
    target.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    return true;
    """
    if not bool(page.run_js(js, json.dumps(target_index))):
        print(f"[WARN] 未找到性别菜单项：{target_value}")
        return
    time.sleep(0.8)
    clear_selection(page)
    close_active_filter_popup(page)


def apply_filters(page: ChromiumPage, filters: Dict[str, Union[str, Sequence[str], None]]) -> None:
    for label, raw_value in filters.items():
        if not raw_value:
            continue
        values = [raw_value] if isinstance(raw_value, str) else [item for item in raw_value if item]
        if not values:
            continue
        print(f"[INFO] 设置筛选：{label} -> {values}")
        if label == "城市地区":
            apply_city_filter(page, values)
        elif label == "学历要求":
            apply_education_filter(page, values)
        elif label == "工作年限":
            apply_work_experience_filter(page, values)
        elif label == "就职公司":
            apply_company_filter(page, values)
        elif label == "性别":
            apply_gender_filter(page, values)
        else:
            print(f"[WARN] 暂未实现该筛选：{label}")
    close_active_filter_popup(page)


def click_search(page: ChromiumPage) -> None:
    close_active_filter_popup(page)
    clear_selection(page)
    time.sleep(0.3)
    search_btn = page.ele('@class:mui-btn mui-btn-primary mui-btn-middle w-88 h-40 text-[16px] leading-[24px] flex justify-center items-center px-0', timeout=2)
    if not search_btn:
        search_btn = wait_text(page, "搜索", timeout=2)
    if not search_btn:
        print("[WARN] 未找到搜索按钮")
        return
    clicked = safe_click(search_btn)
    if not clicked:
        try:
            search_btn.click(by_js=True)
            clicked = True
        except Exception:
            clicked = False
    if not clicked:
        search_input = wait_attr(
            page,
            "placeholder",
            '搜索提示：输入带双引号的完整词组，如"算法工程师"，可精准匹配人才',
            timeout=1,
        )
        if search_input:
            try:
                search_input.input("\n", clear=False)
                clicked = True
            except Exception:
                pass
    time.sleep(1.2)
    clear_selection(page)
    if clicked:
        print("[INFO] 已点击搜索")
    else:
        print("[WARN] 搜索按钮未成功触发")


def run_candidate_search(config: Dict) -> ChromiumPage:
    print("[INFO] 脉脉搜索：连接浏览器", flush=True)
    page = connect_page()
    print(f"[INFO] 脉脉搜索：已连接 {page.url}", flush=True)
    try:
        print("[INFO] 脉脉搜索：打开人才搜索页", flush=True)
        page.get(SEARCH_URL)
        page.wait.load_start()
    except Exception:
        try:
            print("[WARN] 脉脉搜索：打开页面异常，尝试刷新", flush=True)
            page.refresh()
        except Exception:
            pass
    time.sleep(2)
    print("[INFO] 脉脉搜索：清空筛选", flush=True)
    clear_all_filters(page)
    print("[INFO] 脉脉搜索：应用筛选条件", flush=True)
    apply_filters(page, config.get("filters", {}))
    print("[INFO] 脉脉搜索：输入关键词", flush=True)
    input_keyword(page, config.get("keyword", ""))
    print("[INFO] 脉脉搜索：设置关键词模式", flush=True)
    set_keyword_mode(page, config.get("keyword_mode", "所有"))
    print("[INFO] 脉脉搜索：点击搜索", flush=True)
    click_search(page)
    return page


if __name__ == "__main__":
    page = run_candidate_search(SEARCH_CONFIG)
    print(f"[INFO] 褰撳墠绛涢€夋爣棰橈細{get_filter_titles(page)}")

