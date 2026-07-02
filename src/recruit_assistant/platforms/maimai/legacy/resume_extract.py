import json
import sys
import time

from DrissionPage import ChromiumPage
from src.maimai_auto.browser_connect import connect_chromium_page
from src.maimai_auto.paths import DEFAULT_DEBUG_PORT

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def connect_page() -> ChromiumPage:
    return connect_chromium_page(port=DEFAULT_DEBUG_PORT)


def clear_selection(page: ChromiumPage) -> None:
    try:
        page.run_js("window.getSelection().removeAllRanges();")
    except Exception:
        pass


def is_candidate_detail_open(page: ChromiumPage) -> bool:
    js = """
    const root = document.querySelector('.left___1IRRn');
    if (!root) return false;
    const facets = root.querySelectorAll('.facet___2Ak8o, .facet___3hPuz');
    return facets.length >= 3;
    """
    try:
        return bool(page.run_js(js))
    except Exception:
        return False


def get_first_card_point(page: ChromiumPage):
    js = """
    const isVisible = (el) => {
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 220 && r.height > 80;
    };
    const withMeta = (el, mode) => {
      const r = el.getBoundingClientRect();
      const lines = (el.innerText || '').split('\\n').map(item => item.trim()).filter(Boolean);
      return {
        mode,
        name: lines[0] || '',
        left: Math.round(r.left),
        top: Math.round(r.top),
        width: Math.round(r.width),
        height: Math.round(r.height),
      };
    };

    const detailCards = [...document.querySelectorAll('.card___3uNBJ')]
      .filter(el => {
        if (!isVisible(el)) return false;
        const r = el.getBoundingClientRect();
        return r.left < window.innerWidth * 0.3;
      })
      .map(el => withMeta(el, 'detail_list'));

    if (detailCards.length) {
      detailCards.sort((a, b) => a.top - b.top || a.left - b.left);
      const target = detailCards[0];
      return {
        ...target,
        x: Math.round(target.left + Math.min(120, target.width * 0.28)),
        y: Math.round(target.top + Math.min(60, target.height * 0.32)),
      };
    }

    const resultCards = [...document.querySelectorAll('.mainContent___nwb6Q.talent-common-card, .card___1Ydo0')]
      .filter(el => {
        if (!isVisible(el)) return false;
        const r = el.getBoundingClientRect();
        return r.left > window.innerWidth * 0.18
          && r.left < window.innerWidth * 0.4
          && r.width > window.innerWidth * 0.45
          && r.top > 80;
      })
      .map(el => withMeta(el, 'search_results'));

    if (!resultCards.length) return null;
    resultCards.sort((a, b) => a.top - b.top || a.left - b.left);
    const target = resultCards[0];
    return {
      ...target,
      x: Math.round(target.left + Math.min(180, target.width * 0.2)),
      y: Math.round(target.top + Math.min(72, target.height * 0.28)),
    };
    """
    return page.run_js(js)


def get_search_result_cards(page: ChromiumPage):
    js = """
    const isVisible = (el) => {
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 220 && r.height > 80;
    };
    const seen = new Set();
    const cards = [...document.querySelectorAll('.mainContent___nwb6Q.talent-common-card, .card___1Ydo0')]
      .filter(el => {
        if (!isVisible(el)) return false;
        const r = el.getBoundingClientRect();
        if (!(r.left > window.innerWidth * 0.18 && r.left < window.innerWidth * 0.45 && r.top > 80)) return false;
        const key = `${Math.round(r.top)}:${(el.innerText || '').split('\\n')[0] || ''}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .sort((a, b) => {
        const ra = a.getBoundingClientRect();
        const rb = b.getBoundingClientRect();
        return ra.top - rb.top || ra.left - rb.left;
      });
    return cards.slice(0, 8).map((card, index) => {
      const r = card.getBoundingClientRect();
      const lines = (card.innerText || '').split('\\n').map(item => item.trim()).filter(Boolean);
      return {
        index,
        mode: 'search_results',
        name: lines[0] || '',
        left: Math.round(r.left),
        top: Math.round(r.top),
        width: Math.round(r.width),
        height: Math.round(r.height),
      };
    });
    """
    return page.run_js(js) or []


def click_first_detail_list_card_js(page: ChromiumPage) -> bool:
    js = """
    const isVisible = (el) => {
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 220 && r.height > 80;
    };
    const cards = [...document.querySelectorAll('.card___3uNBJ')]
      .filter(el => {
        if (!isVisible(el)) return false;
        const r = el.getBoundingClientRect();
        return r.left < window.innerWidth * 0.3;
      });
    cards.sort((a, b) => {
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      return ra.top - rb.top || ra.left - rb.left;
    });
    const card = cards[0];
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
    try:
        return bool(page.run_js(js))
    except Exception:
        return False


def click_search_result_js(page: ChromiumPage, index: int = 0) -> bool:
    js = """
    const index = Number(arguments[0] || 0);
    const isVisible = (el) => {
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 220 && r.height > 80;
    };
    const domClick = (target) => {
      target.scrollIntoView({block: 'center', inline: 'nearest'});
      const rect = target.getBoundingClientRect();
      const clientX = Math.round(rect.left + rect.width / 2);
      const clientY = Math.round(rect.top + rect.height / 2);
      const base = {
        bubbles: true,
        cancelable: true,
        composed: true,
        view: window,
        clientX,
        clientY,
        screenX: clientX,
        screenY: clientY,
        button: 0,
      };
      for (const type of ['pointerover', 'pointermove', 'pointerdown']) {
        const eventInit = {...base, buttons: type === 'pointerdown' ? 1 : 0};
        target.dispatchEvent(window.PointerEvent ? new PointerEvent(type, eventInit) : new MouseEvent(type, eventInit));
      }
      for (const type of ['mouseover', 'mousemove']) {
        target.dispatchEvent(new MouseEvent(type, {...base, buttons: 0}));
      }
      target.dispatchEvent(new MouseEvent('mousedown', {...base, buttons: 1}));
      target.dispatchEvent(window.PointerEvent ? new PointerEvent('pointerup', {...base, buttons: 0}) : new MouseEvent('pointerup', {...base, buttons: 0}));
      target.dispatchEvent(new MouseEvent('mouseup', {...base, buttons: 0}));
      target.dispatchEvent(new MouseEvent('click', {...base, buttons: 0}));
      if (typeof target.click === 'function') target.click();
    };
    const seen = new Set();
    const cards = [...document.querySelectorAll('.mainContent___nwb6Q.talent-common-card, .card___1Ydo0')]
      .filter(el => {
        if (!isVisible(el)) return false;
        const r = el.getBoundingClientRect();
        if (!(r.left > window.innerWidth * 0.18
          && r.left < window.innerWidth * 0.45
          && r.top > 80)) return false;
        const key = `${Math.round(r.top)}:${(el.innerText || '').split('\\n')[0] || ''}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
    cards.sort((a, b) => {
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      return ra.top - rb.top || ra.left - rb.left;
    });
    const card = cards[index];
    if (!card) return false;
    const target = card.querySelector('.name___2TJeJ')
      || card.querySelector('.info___euZCx')
      || card.querySelector('.top___23_Zy')
      || card.querySelector('.card___1Ydo0')
      || card;
    domClick(target);
    return true;
    """
    try:
        return bool(page.run_js(js, index))
    except Exception:
        return False


def click_first_search_result_js(page: ChromiumPage) -> bool:
    return click_search_result_js(page, 0)


def open_first_candidate_card(page: ChromiumPage) -> bool:
    point = None
    for _ in range(12):
        point = get_first_card_point(page)
        if point:
            break
        time.sleep(0.5)
    if not point:
        print("[WARN] 未找到候选人块状区")
        return False

    print(
        f"[INFO] 点击首个候选人块[{point.get('mode', '')}]：{point.get('name', '')} "
        f"left={point['left']} top={point['top']} "
        f"width={point['width']} height={point['height']}"
    )

    if point.get("mode") == "detail_list":
        for _ in range(3):
            if click_first_detail_list_card_js(page):
                clear_selection(page)
                time.sleep(0.25)
                if is_candidate_detail_open(page):
                    return True
            time.sleep(0.2)
    else:
        search_cards = get_search_result_cards(page)
        for card in search_cards[:5] or [point]:
            index = int(card.get("index", 0) or 0)
            print(
                f"[INFO] 尝试打开搜索结果候选人 {index + 1}: {card.get('name', '')} "
                f"left={card.get('left')} top={card.get('top')}"
            )
            if not click_search_result_js(page, index):
                continue
            clear_selection(page)
            end_at = time.time() + 4
            while time.time() < end_at:
                if is_candidate_detail_open(page):
                    return True
                time.sleep(0.2)

    return False


def extract_resume_key_info(page: ChromiumPage):
    js = """
    const text = (el) => el ? (el.innerText || '').replace(/\\u00a0/g, ' ').trim() : '';
    const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
    const splitLines = (value) => value
      .split('\\n')
      .map(item => norm(item))
      .filter(Boolean);

    const root = document.querySelector('.left___1IRRn');
    if (!root) return null;

    const header = root.children[0];
    const headerLines = header ? splitLines(text(header)) : [];

    const findFacet = (label) => {
      const facets = [...root.querySelectorAll('.facet___2Ak8o, .facet___3hPuz')];
      return facets.find(facet => {
        const title = facet.querySelector('.font_title___1dWcC');
        return title && norm(text(title)) === label;
      }) || null;
    };

    const cleanCardLines = (card) => {
      const lines = splitLines(text(card));
      return lines.filter(line => !['展开', '预览', '下载', '转发简历', '该段经历来自附件简历'].includes(line));
    };

    const expectationFacet = findFacet('期望偏好');
    const workFacet = findFacet('工作经历');
    const eduFacet = findFacet('教育经历');
    const tagsFacet = findFacet('职业标签');

    const expectationLines = expectationFacet ? splitLines(text(expectationFacet)).filter(line => line !== '期望偏好') : [];

    const workCards = workFacet
      ? [...workFacet.querySelectorAll('.card___2U2pq')].map(card => {
          const lines = cleanCardLines(card);
          return {
            company: lines[0] || '',
            role: lines[1] || '',
            dates: lines[2] || '',
            details: lines.slice(3),
          };
        })
      : [];

    const educationCards = eduFacet
      ? [...eduFacet.querySelectorAll('.card___2U2pq')].map(card => {
          const lines = cleanCardLines(card);
          return {
            school: lines[0] || '',
            degree_major: lines[1] || '',
            dates: lines[2] || '',
            details: lines.slice(3),
          };
        })
      : [];

    const tags = tagsFacet
      ? [...tagsFacet.querySelectorAll('.tagWrapper___1XmhF')]
          .map(tag => norm(text(tag)))
          .filter(Boolean)
      : [];

    const recentActivity = headerLines.find(line => line.includes('活跃')) || '';
    const years = headerLines.find(line => /^\\d+年/.test(line) || /\\d+年/.test(line)) || '';
    const degree = headerLines.find(line => /本科|硕士|博士|大专/.test(line)) || '';
    const gender = headerLines.find(line => line === '男' || line === '女') || '';
    const location = headerLines.find(line => /市|区|县/.test(line)) || '';

    return {
      name: headerLines[0] || '',
      recent_activity: recentActivity,
      location,
      work_years: years,
      degree,
      gender,
      expectation: {
        preferred_locations: expectationLines[0] || '',
        target_role: expectationLines[1] || '',
        salary: expectationLines[2] || '',
      },
      work_experience: workCards,
      education_experience: educationCards,
      career_tags: [...new Set(tags)],
    };
    """
    return page.run_js(js)


def run_resume_extract() -> None:
    page = connect_page()
    start = time.time()

    if not is_candidate_detail_open(page):
        if not open_first_candidate_card(page):
            print("[WARN] 未能点击进入候选人二简历详情页")
            return
        time.sleep(0.35)

    if not is_candidate_detail_open(page):
        print("[WARN] 点击后仍未进入候选人二简历详情页")
        return

    data = extract_resume_key_info(page)
    cost = round(time.time() - start, 2)
    print(f"[INFO] 候选人简历提取完成，耗时 {cost}s")
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run_resume_extract()
