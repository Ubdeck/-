from __future__ import annotations

import json
import time

from .constants import (
    AGE_REQUIREMENT_TITLE,
    AI_FILL_TEXTS,
    CITY_CONFIRM_TEXT,
    CITY_SEARCH_PLACEHOLDER,
    CONFIRM_TEXT,
    EDUCATION_TITLE,
    FUNCTION_MODAL_TITLE,
    FUNCTION_SEARCH_PLACEHOLDER,
    INDUSTRY_MODAL_TITLE,
    INDUSTRY_SEARCH_PLACEHOLDER,
    MORE_CONDITIONS_TEXT,
    OTHER_TEXT,
    RECRUITMENT_TYPE_TITLE,
    SCHOOL_TYPE_TITLE,
)


class FilterMixin:
    def insert_ai_words_for_input(self, placeholder: str) -> None:
        deadline = time.time() + 8
        result = None
        while time.time() < deadline:
            result = self.page.run_js(
                """
                const placeholder = arguments[0];
                const input = Array.from(document.querySelectorAll('input[placeholder]'))
                  .find(ele => ele.placeholder === placeholder);
                if (!input) return {ok: false, reason: 'input not found'};

                const scopes = [];
                let node = input;
                for (let depth = 0; node && depth < 8; depth += 1, node = node.parentElement) {
                  scopes.push(node);
                }

                for (const scope of scopes) {
                  const btn = scope.querySelector('[class*=aiBtn]');
                  if (btn) {
                    for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                      btn.dispatchEvent(new MouseEvent(name, {
                        bubbles: true,
                        cancelable: true,
                        composed: true,
                        view: window,
                      }));
                    }
                    return {ok: true};
                  }
                }
                return {ok: false, reason: 'ai button not found near input'};
                """,
                placeholder,
            )
            if result and result.get("ok"):
                break
            time.sleep(0.3)
        if not result or not result.get("ok"):
            raise RuntimeError(f"AI fill button unavailable: {placeholder}, {result}")

        self.click_ai_fill_button()

    def click_ai_fill_button(self) -> None:
        deadline = time.time() + 15
        last_result = None
        while time.time() < deadline:
            result = self.page.run_js(
                """
                const fillTexts = arguments[0].split('|');
                const popovers = Array.from(document.querySelectorAll('.ant-lpt-popover'))
                  .filter(ele => {
                    const rect = ele.getBoundingClientRect();
                    return rect.width > 0
                      && rect.height > 0
                      && (ele.innerText || '').includes('AI');
                  });
                const popover = popovers[popovers.length - 1];
                if (!popover) return {ok: false, reason: 'popover not visible'};
                const popoverText = (popover.innerText || popover.textContent || '').trim().replace(/\\s+/g, ' ');
                if (popoverText.includes('正在扩展中')) {
                  return {ok: false, waiting: true, reason: 'AI keywords still generating'};
                }
                const buttons = Array.from(popover.querySelectorAll('button'))
                  .map(ele => ({
                    ele,
                    text: (ele.innerText || '').trim().replace(/\\s+/g, ' '),
                    disabled: !!ele.disabled || ele.getAttribute('aria-disabled') === 'true' || ele.className.includes('disabled'),
                  }));
                const button = (buttons.find(item => item.text === '填入复合关键词')
                  || buttons.find(item => fillTexts.includes(item.text))
                  || {});
                if (!button.ele) return {ok: false, reason: 'fill button not visible'};
                if (button.disabled) return {ok: false, waiting: true, reason: 'fill button disabled'};
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  button.ele.dispatchEvent(new MouseEvent(name, {
                    bubbles: true,
                    cancelable: true,
                    composed: true,
                    view: window,
                  }));
                }
                return {ok: true, text: button.text};
                """,
                "|".join(AI_FILL_TEXTS),
            )
            last_result = result
            if result and result.get("ok"):
                time.sleep(1.0)
                return
            time.sleep(0.5)
        raise RuntimeError(f"AI fill popover did not close after clicking fill: {last_result}")

    def select_city(self, row_title: str, city: str) -> None:
        self.progress.emit("city_select", f"正在选择{row_title}：{city}")
        self.open_city_modal(row_title)

        input_ele = self.wait_for_input(CITY_SEARCH_PLACEHOLDER)
        input_ele.click()
        input_ele.clear()
        input_ele.input(city)

        self.click_first_city_result(city)
        self.wait_until_city_selected(city)
        self.click_city_modal_confirm()
        if self.is_city_visible_in_row(row_title, city):
            self.progress.emit("city_select", f"{row_title}已显示：{city}")
        else:
            self.progress.emit(
                "city_select",
                f"{row_title}已确认：{city}（页面未在筛选行回显，已按弹窗确认成功继续）",
            )

    def open_city_modal(self, row_title: str) -> None:
        xpath = (
            f'xpath://span[normalize-space()="{row_title}"]'
            f'/ancestor::div[.//label[normalize-space()="{OTHER_TEXT}"]][1]'
            f'//label[normalize-space()="{OTHER_TEXT}"]'
        )
        other = self.page.ele(xpath, timeout=8)
        if not other:
            raise RuntimeError(f"Could not find city Other option: {row_title}")
        other.click()
        self.wait_for_input(CITY_SEARCH_PLACEHOLDER)

    def click_first_city_result(self, city: str) -> None:
        suggest = self.page.ele(
            f'xpath://div[contains(@class,"city-modal")]'
            f'//div[contains(@class,"suggest-list")]//li[contains(.,"{city}")]',
            timeout=5,
        )
        if suggest:
            suggest.click()
            time.sleep(0.8)
            return

        deadline = time.time() + 10
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const city = arguments[0];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  return rect.width > 0 && rect.height > 0;
                };
                const clean = ele => (ele.innerText || ele.value || '').trim().replace(/\\s+/g, ' ');
                const modal = document.querySelector('.city-modal');
                if (!modal || !visible(modal)) return {ok: false, reason: 'modal not visible'};

                const suggestItems = Array.from(modal.querySelectorAll('.suggest-list li'))
                  .filter(ele => visible(ele) && clean(ele).includes(city));
                const buckets = suggestItems.length ? [suggestItems[0]] : [modal];

                for (const bucket of buckets) {
                  if (bucket.matches && bucket.matches('.suggest-list li')) {
                    for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                      bucket.dispatchEvent(new MouseEvent(name, {
                        bubbles: true,
                        cancelable: true,
                        composed: true,
                        view: window,
                      }));
                    }
                    return {ok: true, text: clean(bucket)};
                  }
                  const candidates = Array.from(bucket.querySelectorAll('li,button,label,span,div'))
                    .filter(ele => {
                      const text = clean(ele);
                      if (!visible(ele) || !text.includes(city)) return false;
                      if (text.length > 40) return false;
                      if (ele.tagName === 'INPUT') return false;
                      if (ele.closest('.antd-lp-city-header')) return false;
                      return true;
                    })
                    .sort((a, b) => {
                      const aText = clean(a);
                      const bText = clean(b);
                      const aExact = aText === city ? 0 : 1;
                      const bExact = bText === city ? 0 : 1;
                      return aExact - bExact || aText.length - bText.length;
                    });
                  const target = candidates[0];
                  if (target) {
                    for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                      target.dispatchEvent(new MouseEvent(name, {
                        bubbles: true,
                        cancelable: true,
                        composed: true,
                        view: window,
                      }));
                    }
                    return {ok: true, text: clean(target)};
                  }
                }
                return {ok: false, reason: 'city result not found'};
                """,
                city,
            )
            if last_result and last_result.get("ok"):
                time.sleep(0.6)
                return
            time.sleep(0.3)
        raise RuntimeError(f"City search result not found: {city}, {last_result}")

    def wait_until_city_selected(self, city: str, timeout: int = 5) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            selected = self.page.run_js(
                """
                const city = arguments[0];
                const modal = document.querySelector('.city-modal');
                if (!modal) return false;
                const text = (modal.innerText || '').trim().replace(/\\s+/g, ' ');
                return text.includes(city) && /已选\\s*[（(][1-5]\\//.test(text);
                """,
                city,
            )
            if selected:
                return
            time.sleep(0.2)
        raise RuntimeError(f"City was not added to selected list: {city}")

    def click_city_modal_confirm(self) -> None:
        deadline = time.time() + 10
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const confirmText = arguments[0];
                const modal = document.querySelector('.city-modal');
                if (!modal) return {ok: false, reason: 'modal not found'};
                const button = Array.from(modal.querySelectorAll('button'))
                  .find(ele => {
                    const rect = ele.getBoundingClientRect();
                    return rect.width > 0
                      && rect.height > 0
                      && (ele.innerText || '').trim().replace(/\\s+/g, ' ') === confirmText
                      && !ele.disabled
                      && !String(ele.className).includes('disabled');
                  });
                if (!button) return {ok: false, reason: 'confirm button not ready'};
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  button.dispatchEvent(new MouseEvent(name, {
                    bubbles: true,
                    cancelable: true,
                    composed: true,
                    view: window,
                  }));
                }
                return {ok: true};
                """,
                CITY_CONFIRM_TEXT,
            )
            if last_result and last_result.get("ok"):
                self.wait_until_city_modal_closed()
                return
            time.sleep(0.3)
        raise RuntimeError(f"Could not confirm city modal: {last_result}")

    def is_city_visible_in_row(self, row_title: str, city: str, timeout: int = 2) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            ok = self.page.run_js(
                """
                const rowTitle = arguments[0];
                const city = arguments[1];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  return rect.width > 0 && rect.height > 0;
                };
                const clean = ele => (ele.innerText || '').trim().replace(/\\s+/g, ' ');
                const compact = value => String(value || '').replace(/\\s+/g, '');
                const title = Array.from(document.querySelectorAll('span, div'))
                  .find(ele => visible(ele) && compact(clean(ele)) === compact(rowTitle));
                let row = title;
                while (row && row !== document.body) {
                  const text = clean(row);
                  if (text.includes(city)) return true;
                  const parent = row.parentElement;
                  if (!parent) break;
                  const parentText = clean(parent);
                  const siblingTitles = Array.from(parent.querySelectorAll('span, div'))
                    .filter(ele => visible(ele) && /城市$/.test(compact(clean(ele))));
                  if (siblingTitles.length > 1 || parentText.length > 300) break;
                  row = parent;
                }
                return !!row && (row.innerText || '').includes(city);
                """,
                row_title,
                city,
            )
            if ok:
                return True
            time.sleep(0.2)
        return False

    def wait_until_city_modal_closed(self, timeout: int = 5) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            visible = self.page.run_js(
                """
                return Array.from(document.querySelectorAll('.city-modal'))
                  .some(ele => {
                    const rect = ele.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                  });
                """
            )
            if not visible:
                return
            time.sleep(0.2)
        raise RuntimeError("City modal did not close.")

    def select_industry_modal(self, row_title: str, industries: str) -> None:
        values = self.split_multi_values(industries)[:5]
        if not values:
            return
        self.progress.emit("industry_select", f"正在选择{row_title}：{', '.join(values)}")
        self.close_open_dropdown()
        self.open_industry_modal(row_title)
        for value in values:
            self.search_and_click_industry(value)
        self.click_industry_modal_confirm()
        self.progress.emit("industry_select", f"{row_title}已确认：{', '.join(values)}")

    def select_function_modal(self, row_title: str, functions: str) -> None:
        values = self.split_multi_values(functions)[:5]
        if not values:
            return
        self.progress.emit("function_select", f"正在选择{row_title}：{', '.join(values)}")
        self.close_open_dropdown()
        self.open_function_modal(row_title)
        for value in values:
            self.search_and_click_function(value)
        self.click_function_modal_confirm()
        self.progress.emit("function_select", f"{row_title}已确认：{', '.join(values)}")

    def open_function_modal(self, row_title: str) -> None:
        deadline = time.time() + 8
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const rowTitle = arguments[0];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\\s+/g, ' ');
                const compact = value => String(value || '').replace(/\\s+/g, '');
                const fireClick = ele => {
                  ele.scrollIntoView({block: 'center', inline: 'nearest'});
                  for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                    ele.dispatchEvent(new MouseEvent(name, {
                      bubbles: true,
                      cancelable: true,
                      composed: true,
                      view: window,
                    }));
                  }
                };
                const exactSelects = Array.from(document.querySelectorAll('.ant-lpt-select, [class*=select]'))
                  .filter(ele => visible(ele) && compact(clean(ele)) === compact(rowTitle));
                let target = exactSelects[0];
                if (!target) {
                  const title = Array.from(document.querySelectorAll('span, div, label'))
                    .find(ele => visible(ele) && compact(clean(ele)) === compact(rowTitle));
                  if (title) {
                    let row = title.closest('[class*=wrap], [class*=filter], [class*=item]') || title.parentElement;
                    for (let depth = 0; row && depth < 5; depth += 1, row = row.parentElement) {
                      const text = clean(row);
                      if (!text.includes(rowTitle)) continue;
                      const candidates = Array.from(row.querySelectorAll('.ant-lpt-select, [class*=select], button, label, span, div'))
                        .filter(ele => visible(ele) && ele !== title)
                        .filter(ele => {
                          const text = clean(ele);
                          const cls = String(ele.className || '').toLowerCase();
                          const cursor = getComputedStyle(ele).cursor;
                          return cls.includes('select')
                            || cursor === 'pointer'
                            || compact(text) === compact(rowTitle);
                        })
                        .sort((a, b) => {
                          const score = ele => {
                            const cls = String(ele.className || '').toLowerCase();
                            const text = compact(clean(ele));
                            if (cls.includes('select')) return 0;
                            if (text === compact(rowTitle)) return 1;
                            if (getComputedStyle(ele).cursor === 'pointer') return 2;
                            return 9;
                          };
                          const ar = a.getBoundingClientRect();
                          const br = b.getBoundingClientRect();
                          return score(a) - score(b) || (ar.width * ar.height) - (br.width * br.height);
                        });
                      if (candidates[0]) {
                        target = candidates[0];
                        break;
                      }
                      if (text.length > 500) break;
                    }
                  }
                }
                if (!target) return {ok: false, reason: 'function trigger not found'};
                fireClick(target);
                return {ok: true, text: clean(target)};
                """,
                row_title,
            )
            if last_result and last_result.get("ok"):
                try:
                    self.wait_for_function_modal(timeout=3)
                    return
                except RuntimeError:
                    pass
            time.sleep(0.3)
        raise RuntimeError(f"Could not open function modal: {row_title}, {last_result}")

    def wait_for_function_modal(self, timeout: int = 8) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            found = self.page.run_js(
                """
                const modalTitle = arguments[0];
                const placeholder = arguments[1];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const modals = Array.from(document.querySelectorAll(
                  '.ant-lpt-modal, .ant-modal, [role=dialog], [class*=modal], [class*=Modal]'
                )).filter(visible);
                return modals.some(ele => (ele.innerText || '').includes(modalTitle))
                  || Array.from(document.querySelectorAll('input[placeholder]'))
                    .some(ele => visible(ele) && ele.placeholder === placeholder);
                """,
                FUNCTION_MODAL_TITLE,
                FUNCTION_SEARCH_PLACEHOLDER,
            )
            if found:
                return
            time.sleep(0.2)
        raise RuntimeError("Function modal did not open.")

    def search_and_click_function(self, function_name: str) -> None:
        deadline = time.time() + 10
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const functionName = arguments[0];
                const modalTitle = arguments[1];
                const placeholder = arguments[2];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || ele.value || '')
                  .trim()
                  .replace(/\\s+/g, ' ');
                const compact = value => String(value || '').replace(/\\s+/g, '');
                const fireClick = ele => {
                  ele.scrollIntoView({block: 'center', inline: 'nearest'});
                  for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                    ele.dispatchEvent(new MouseEvent(name, {
                      bubbles: true,
                      cancelable: true,
                      composed: true,
                      view: window,
                    }));
                  }
                };
                const findModal = () => {
                  const modals = Array.from(document.querySelectorAll(
                    '.ant-lpt-modal, .ant-modal, [role=dialog], [class*=modal], [class*=Modal]'
                  )).filter(visible);
                  return modals.find(ele => (ele.innerText || '').includes(modalTitle))
                    || modals.find(ele => ele.querySelector(`input[placeholder="${placeholder}"]`))
                    || null;
                };
                const modal = findModal();
                if (!modal) return {ok: false, reason: 'function modal not found'};
                const checked = Array.from(modal.querySelectorAll('span, button, label, li, div'))
                  .filter(ele => visible(ele))
                  .find(ele => compact(clean(ele)) === compact(functionName)
                    && /checked|selected|active/.test(String(ele.className || '').toLowerCase()));
                if (checked) return {ok: true, text: clean(checked), mode: 'already_selected'};
                const findOption = () => Array.from(modal.querySelectorAll('button, label, li, span, div'))
                  .filter(ele => visible(ele))
                  .map(ele => ({ele, text: clean(ele), rect: ele.getBoundingClientRect()}))
                  .filter(item => {
                    if (!item.text || item.ele.tagName === 'INPUT') return false;
                    if (item.text.includes('已选') || item.text.includes('确认') || item.text.includes('取消')) return false;
                    if (item.text.length > Math.max(36, functionName.length + 18)) return false;
                    return compact(item.text) === compact(functionName) || item.text.includes(functionName);
                  })
                  .sort((a, b) => {
                    const aExact = compact(a.text) === compact(functionName) ? 0 : 1;
                    const bExact = compact(b.text) === compact(functionName) ? 0 : 1;
                    const aArea = a.rect.width * a.rect.height;
                    const bArea = b.rect.width * b.rect.height;
                    return aExact - bExact || a.text.length - b.text.length || aArea - bArea;
                  });
                let candidates = findOption();
                let target = candidates[0] && candidates[0].ele;
                if (target) {
                  fireClick(target);
                  return {ok: true, text: clean(target), mode: 'visible_option'};
                }
                const input = Array.from(modal.querySelectorAll('input[placeholder]'))
                  .find(ele => visible(ele) && ele.placeholder === placeholder);
                if (input) {
                  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                  setter.call(input, functionName);
                  input.dispatchEvent(new Event('input', {bubbles: true}));
                  input.dispatchEvent(new Event('change', {bubbles: true}));
                }
                candidates = findOption();
                target = candidates[0] && candidates[0].ele;
                if (!target) return {ok: false, reason: 'function option not found'};
                fireClick(target);
                return {ok: true, text: clean(target), mode: 'search_fallback'};
                """,
                function_name,
                FUNCTION_MODAL_TITLE,
                FUNCTION_SEARCH_PLACEHOLDER,
            )
            if last_result and last_result.get("ok"):
                time.sleep(0.4)
                return
            time.sleep(0.35)
        raise RuntimeError(f"Function option not found: {function_name}, {last_result}")

    def click_function_modal_confirm(self) -> None:
        self.click_modal_confirm_by_title(FUNCTION_MODAL_TITLE, "Function")

    def open_industry_modal(self, row_title: str) -> None:
        deadline = time.time() + 8
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const rowTitle = arguments[0];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || '')
                  .trim()
                  .replace(/\\s+/g, ' ');
                const compact = value => String(value || '').replace(/\\s+/g, '');
                const fireClick = ele => {
                  ele.scrollIntoView({block: 'center', inline: 'nearest'});
                  for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                    ele.dispatchEvent(new MouseEvent(name, {
                      bubbles: true,
                      cancelable: true,
                      composed: true,
                      view: window,
                    }));
                  }
                };

                const exactSelects = Array.from(document.querySelectorAll('.ant-lpt-select, [class*=select]'))
                  .filter(ele => visible(ele) && compact(clean(ele)) === compact(rowTitle));
                let target = exactSelects[0];

                if (!target) {
                  const title = Array.from(document.querySelectorAll('span, div, label'))
                    .find(ele => visible(ele) && compact(clean(ele)) === compact(rowTitle));
                  if (title) {
                    let row = title.closest('[class*=wrap], [class*=filter], [class*=item]') || title.parentElement;
                    for (let depth = 0; row && depth < 5; depth += 1, row = row.parentElement) {
                      const text = clean(row);
                      if (!text.includes(rowTitle)) continue;
                      const candidates = Array.from(row.querySelectorAll('.ant-lpt-select, [class*=select], button, label, span, div'))
                        .filter(ele => visible(ele) && ele !== title)
                        .filter(ele => {
                          const text = clean(ele);
                          const cls = String(ele.className || '').toLowerCase();
                          const cursor = getComputedStyle(ele).cursor;
                          return cls.includes('select')
                            || cursor === 'pointer'
                            || compact(text) === compact(rowTitle);
                        })
                        .sort((a, b) => {
                          const score = ele => {
                            const cls = String(ele.className || '').toLowerCase();
                            const text = compact(clean(ele));
                            if (cls.includes('select')) return 0;
                            if (text === compact(rowTitle)) return 1;
                            if (getComputedStyle(ele).cursor === 'pointer') return 2;
                            return 9;
                          };
                          const ar = a.getBoundingClientRect();
                          const br = b.getBoundingClientRect();
                          return score(a) - score(b) || (ar.width * ar.height) - (br.width * br.height);
                        });
                      if (candidates[0]) {
                        target = candidates[0];
                        break;
                      }
                      if (text.length > 500) break;
                    }
                  }
                }

                if (!target) return {ok: false, reason: 'industry trigger not found'};
                fireClick(target);
                return {ok: true, text: clean(target)};
                """,
                row_title,
            )
            if last_result and last_result.get("ok"):
                try:
                    self.wait_for_industry_modal(timeout=3)
                    return
                except RuntimeError:
                    pass
            time.sleep(0.3)
        raise RuntimeError(f"Could not open industry modal: {row_title}, {last_result}")

    def wait_for_industry_modal(self, timeout: int = 8) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            found = self.page.run_js(
                """
                const modalTitle = arguments[0];
                const placeholder = arguments[1];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const modals = Array.from(document.querySelectorAll(
                  '.ant-lpt-modal, .ant-modal, [role=dialog], [class*=modal], [class*=Modal]'
                )).filter(visible);
                return modals.some(ele => (ele.innerText || '').includes(modalTitle))
                  || Array.from(document.querySelectorAll('input[placeholder]'))
                    .some(ele => visible(ele) && ele.placeholder === placeholder);
                """,
                INDUSTRY_MODAL_TITLE,
                INDUSTRY_SEARCH_PLACEHOLDER,
            )
            if found:
                return
            time.sleep(0.2)
        raise RuntimeError("Industry modal did not open.")

    def search_and_click_industry(self, industry: str) -> None:
        deadline = time.time() + 10
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const industry = arguments[0];
                const modalTitle = arguments[1];
                const placeholder = arguments[2];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || ele.value || '')
                  .trim()
                  .replace(/\\s+/g, ' ');
                const compact = value => String(value || '').replace(/\\s+/g, '');
                const fireClick = ele => {
                  ele.scrollIntoView({block: 'center', inline: 'nearest'});
                  for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                    ele.dispatchEvent(new MouseEvent(name, {
                      bubbles: true,
                      cancelable: true,
                      composed: true,
                      view: window,
                    }));
                  }
                };
                const findModal = () => {
                  const modals = Array.from(document.querySelectorAll(
                    '.ant-lpt-modal, .ant-modal, [role=dialog], [class*=modal], [class*=Modal]'
                  )).filter(visible);
                  return modals.find(ele => (ele.innerText || '').includes(modalTitle))
                    || modals.find(ele => ele.querySelector(`input[placeholder="${placeholder}"]`))
                    || null;
                };
                const modal = findModal();
                if (!modal) return {ok: false, reason: 'industry modal not found'};

                const checked = Array.from(modal.querySelectorAll('span, button, label, li, div'))
                  .filter(ele => visible(ele))
                  .find(ele => compact(clean(ele)) === compact(industry)
                    && /checked|selected|active/.test(String(ele.className || '').toLowerCase()));
                if (checked) return {ok: true, text: clean(checked), mode: 'already_selected'};

                const findOption = () => Array.from(modal.querySelectorAll('button, label, li, span, div'))
                  .filter(ele => visible(ele))
                  .map(ele => ({ele, text: clean(ele), rect: ele.getBoundingClientRect()}))
                  .filter(item => {
                    if (!item.text || item.ele.tagName === 'INPUT') return false;
                    if (item.text.includes('已选') || item.text.includes('确认') || item.text.includes('取消')) return false;
                    if (item.text.length > Math.max(36, industry.length + 18)) return false;
                    return compact(item.text) === compact(industry) || item.text.includes(industry);
                  })
                  .sort((a, b) => {
                    const aExact = compact(a.text) === compact(industry) ? 0 : 1;
                    const bExact = compact(b.text) === compact(industry) ? 0 : 1;
                    const aArea = a.rect.width * a.rect.height;
                    const bArea = b.rect.width * b.rect.height;
                    return aExact - bExact || a.text.length - b.text.length || aArea - bArea;
                  });
                let candidates = findOption();
                let target = candidates[0] && candidates[0].ele;
                if (target) {
                  fireClick(target);
                  return {ok: true, text: clean(target), mode: 'visible_option'};
                }

                const input = Array.from(modal.querySelectorAll('input[placeholder]'))
                  .find(ele => visible(ele) && ele.placeholder === placeholder);
                if (input) {
                  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                  setter.call(input, industry);
                  input.dispatchEvent(new Event('input', {bubbles: true}));
                  input.dispatchEvent(new Event('change', {bubbles: true}));
                }
                candidates = findOption();
                target = candidates[0] && candidates[0].ele;
                if (!target) return {ok: false, reason: 'industry option not found'};
                fireClick(target);
                return {ok: true, text: clean(target), mode: 'search_fallback'};
                """,
                industry,
                INDUSTRY_MODAL_TITLE,
                INDUSTRY_SEARCH_PLACEHOLDER,
            )
            if last_result and last_result.get("ok"):
                time.sleep(0.4)
                return
            time.sleep(0.35)
        raise RuntimeError(f"Industry option not found: {industry}, {last_result}")

    def click_industry_modal_confirm(self) -> None:
        self.click_modal_confirm_by_title(INDUSTRY_MODAL_TITLE, "Industry")

    def click_modal_confirm_by_title(self, modal_title: str, label: str) -> None:
        deadline = time.time() + 10
        last_result = None
        confirm_texts_json = json.dumps([CONFIRM_TEXT, CITY_CONFIRM_TEXT], ensure_ascii=False)
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const modalTitle = arguments[0];
                const confirmTexts = JSON.parse(arguments[1]);
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || '')
                  .trim()
                  .replace(/\\s+/g, ' ');
                const compact = value => String(value || '').replace(/\\s+/g, '');
                const modals = Array.from(document.querySelectorAll(
                  '.ant-lpt-modal, .ant-modal, [role=dialog], [class*=modal], [class*=Modal]'
                )).filter(visible);
                const modal = modals.find(ele => (ele.innerText || '').includes(modalTitle)) || modals[modals.length - 1];
                if (!modal) return {ok: false, reason: 'industry modal not found'};
                const exact = ele => confirmTexts.some(text => compact(clean(ele)) === compact(text));
                const button = Array.from(modal.querySelectorAll('button'))
                  .filter(ele => visible(ele) && !ele.disabled)
                  .find(exact)
                  || Array.from(modal.querySelectorAll('span, div'))
                    .filter(ele => visible(ele))
                    .map(ele => exact(ele) ? (ele.closest('button') || ele) : null)
                    .find(Boolean);
                if (!button) return {ok: false, reason: 'confirm button not found'};
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  button.dispatchEvent(new MouseEvent(name, {
                    bubbles: true,
                    cancelable: true,
                    composed: true,
                    view: window,
                  }));
                }
                if (typeof button.click === 'function') button.click();
                return {ok: true};
                """,
                modal_title,
                confirm_texts_json,
            )
            if last_result and last_result.get("ok"):
                self.wait_until_modal_closed(modal_title, label)
                return
            time.sleep(0.3)
        raise RuntimeError(f"Could not confirm {label.lower()} modal: {last_result}")

    def wait_until_industry_modal_closed(self, timeout: int = 5) -> None:
        self.wait_until_modal_closed(INDUSTRY_MODAL_TITLE, "Industry", timeout=timeout)

    def wait_until_modal_closed(self, modal_title: str, label: str, timeout: int = 5) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            visible = self.page.run_js(
                """
                const modalTitle = arguments[0];
                const modals = Array.from(document.querySelectorAll(
                  '.ant-lpt-modal, .ant-modal, [role=dialog], [class*=modal], [class*=Modal]'
                ));
                return modals.some(ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && (ele.innerText || '').includes(modalTitle);
                });
                """,
                modal_title,
            )
            if not visible:
                return
            time.sleep(0.2)
        raise RuntimeError(f"{label} modal did not close.")

    def click_row_option(self, row_title: str, option_text: str) -> None:
        result = self.page.run_js(
            """
            const rowTitle = arguments[0];
            const optionText = arguments[1];
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              return rect.width > 0 && rect.height > 0;
            };
            const clean = ele => (ele.innerText || '').trim().replace(/\\s+/g, ' ');
            const compact = value => String(value || '').replace(/\\s+/g, '');
            const title = Array.from(document.querySelectorAll('span'))
              .find(ele => compact(clean(ele)) === compact(rowTitle) && visible(ele));
            if (!title) return {ok: false, reason: 'row title not found'};
            const row = title.closest('[class*=wrap]');
            if (!row) return {ok: false, reason: 'row wrapper not found'};
            const target = Array.from(row.querySelectorAll('label, span, div'))
              .find(ele => clean(ele) === optionText && visible(ele));
            if (!target) return {ok: false, reason: 'option not found'};
            for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
              target.dispatchEvent(new MouseEvent(name, {
                bubbles: true,
                cancelable: true,
                composed: true,
                view: window,
              }));
            }
            return {ok: true};
            """,
            row_title,
            option_text,
        )
        if not result or not result.get("ok"):
            raise RuntimeError(f"Could not click row option: {row_title} -> {option_text}, {result}")

    def ensure_more_conditions_expanded(self) -> None:
        if self.is_more_conditions_expanded():
            return

        result = self.page.run_js(
            """
            const moreText = arguments[0];
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0
                && rect.height > 0
                && style.display !== 'none'
                && style.visibility !== 'hidden';
            };
            const clean = ele => (ele.innerText || ele.textContent || '')
              .trim()
              .replace(/\\s+/g, ' ');
            const buttons = Array.from(document.querySelectorAll('span, button, div'))
              .filter(ele => visible(ele)
                && clean(ele) === moreText
                && getComputedStyle(ele).cursor === 'pointer');
            const button = buttons[buttons.length - 1];
            if (!button) return {ok: false, reason: 'more button not found'};
            button.scrollIntoView({block: 'center', inline: 'nearest'});
            for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
              button.dispatchEvent(new MouseEvent(name, {
                bubbles: true,
                cancelable: true,
                composed: true,
                view: window,
              }));
            }
            return {ok: true};
            """,
            MORE_CONDITIONS_TEXT,
        )
        if not result or not result.get("ok"):
            raise RuntimeError(f"Could not click More Conditions button: {result}")

        deadline = time.time() + 5
        while time.time() < deadline:
            if self.is_more_conditions_expanded():
                return
            time.sleep(0.2)
        raise RuntimeError("More conditions did not expand.")

    def is_more_conditions_expanded(self) -> bool:
        return bool(
            self.page.run_js(
                """
                const clean = ele => (ele.innerText || '').trim().replace(/\\s+/g, ' ');
                const compact = value => String(value || '').replace(/\\s+/g, '');
                const otherTitle = Array.from(document.querySelectorAll('span'))
                  .find(ele => compact(clean(ele)) === '其他筛选');
                const row = otherTitle && otherTitle.closest('[class*=wrap]');
                if (!row) return false;
                return row.getBoundingClientRect().height > 60;
                """
            )
        )

    def select_dropdown_option(self, dropdown_title: str, option_text: str, keep_open: bool = False) -> None:
        if keep_open:
            if not self.has_visible_dropdown():
                self.open_dropdown(dropdown_title)
        else:
            self.close_open_dropdown()
            self.open_dropdown(dropdown_title)
        self.click_dropdown_option(option_text)
        if not keep_open:
            time.sleep(0.3)

    def open_dropdown(self, dropdown_title: str) -> None:
        result = self.page.run_js(
            """
            const title = arguments[0];
            const schoolTitle = arguments[1];
            const recruitmentTitle = arguments[2];
            const educationTitle = arguments[3];
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              return rect.width > 0 && rect.height > 0;
            };
            const clean = ele => (ele.innerText || '').trim().replace(/\\s+/g, ' ');
            const compact = value => String(value || '').replace(/\\s+/g, '');
            let candidates = Array.from(document.querySelectorAll('.ant-lpt-select, [class*=select]'))
              .filter(ele => visible(ele) && clean(ele) === title);

            if (!candidates.length && (title === schoolTitle || title === recruitmentTitle)) {
              const educationLabel = Array.from(document.querySelectorAll('span'))
                .find(ele => compact(clean(ele)) === compact(educationTitle) && visible(ele));
              const row = educationLabel && educationLabel.closest('[class*=wrap]');
              if (row) {
                const selects = Array.from(row.querySelectorAll('.ant-lpt-select')).filter(visible);
                candidates = title === schoolTitle
                  ? selects.filter(ele => String(ele.className).includes('multiple'))
                  : selects.filter(ele => !String(ele.className).includes('multiple'));
              }
            }

            candidates = candidates.sort((a, b) => {
              const ar = a.getBoundingClientRect();
              const br = b.getBoundingClientRect();
              return (ar.width * ar.height) - (br.width * br.height);
            });
            const target = candidates[0];
            if (!target) return {ok: false, reason: 'dropdown not found'};
            for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
              target.dispatchEvent(new MouseEvent(name, {
                bubbles: true,
                cancelable: true,
                composed: true,
                view: window,
              }));
            }
            return {ok: true};
            """,
            dropdown_title,
            SCHOOL_TYPE_TITLE,
            RECRUITMENT_TYPE_TITLE,
            EDUCATION_TITLE,
        )
        if not result or not result.get("ok"):
            raise RuntimeError(f"Could not open dropdown: {dropdown_title}, {result}")

    def has_visible_dropdown(self) -> bool:
        return bool(
            self.page.run_js(
                """
                return Array.from(document.querySelectorAll('.ant-lpt-select-dropdown:not(.ant-lpt-select-dropdown-hidden)'))
                  .some(ele => {
                    const rect = ele.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                  });
                """
            )
        )

    def click_dropdown_option(self, option_text: str) -> None:
        deadline = time.time() + 8
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const optionText = arguments[0];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  return rect.width > 0 && rect.height > 0;
                };
                const clean = ele => (ele.innerText || '').trim().replace(/\\s+/g, ' ');
                const dropdowns = Array.from(document.querySelectorAll(
                  '.ant-lpt-select-dropdown:not(.ant-lpt-select-dropdown-hidden)'
                )).filter(visible);
                const dropdown = dropdowns[dropdowns.length - 1];
                if (!dropdown) return {ok: false, reason: 'dropdown panel not visible'};
                const options = Array.from(dropdown.querySelectorAll('[class*=option], li, div, span'))
                  .filter(ele => visible(ele) && clean(ele) === optionText)
                  .sort((a, b) => {
                    const ar = a.getBoundingClientRect();
                    const br = b.getBoundingClientRect();
                    return (ar.width * ar.height) - (br.width * br.height);
                  });
                const target = options[0];
                if (!target) return {ok: false, reason: 'option not found'};
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  target.dispatchEvent(new MouseEvent(name, {
                    bubbles: true,
                    cancelable: true,
                    composed: true,
                    view: window,
                  }));
                }
                return {ok: true};
                """,
                option_text,
            )
            if last_result and last_result.get("ok"):
                time.sleep(0.4)
                return
            time.sleep(0.2)
        raise RuntimeError(f"Could not click dropdown option: {option_text}, {last_result}")

    def select_custom_age_range(self, age_min: str, age_max: str) -> None:
        min_value = str(age_min or "").strip()
        max_value = str(age_max or "").strip()
        if not min_value and not max_value:
            return
        self.select_dropdown_option(AGE_REQUIREMENT_TITLE, "自定义")
        deadline = time.time() + 8
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const minValue = arguments[0];
                const maxValue = arguments[1];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const compact = value => String(value || '').replace(/\\s+/g, '');
                const setValue = (input, value) => {
                  if (!input || value === '') return;
                  input.focus();
                  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                  setter.call(input, value);
                  input.dispatchEvent(new Event('input', {bubbles: true}));
                  input.dispatchEvent(new Event('change', {bubbles: true}));
                  input.blur();
                };
                const containers = Array.from(document.querySelectorAll('div, span'))
                  .filter(ele => visible(ele) && compact(ele.innerText || ele.textContent).includes('自定义'))
                  .map(ele => ele.closest('[class*=wrap], [class*=Box], [class*=group], [class*=content]') || ele)
                  .filter(Boolean);
                const unique = [];
                for (const item of containers) {
                  if (!unique.includes(item)) unique.push(item);
                }
                const candidates = unique.map(container => {
                  const rect = container.getBoundingClientRect();
                  const inputs = Array.from(container.querySelectorAll('input'))
                    .filter(input => visible(input) && !input.disabled && input.type !== 'hidden')
                    .sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
                  return {container, inputs, area: rect.width * rect.height};
                })
                  .filter(item => item.inputs.length >= 2)
                  .sort((a, b) => a.area - b.area);
                const target = candidates[0];
                if (!target) return {ok: false, reason: 'age custom inputs not found'};
                setValue(target.inputs[0], minValue);
                setValue(target.inputs[1], maxValue);
                let confirmScope = target.container;
                let confirm = null;
                for (let depth = 0; confirmScope && depth < 6 && !confirm; depth += 1, confirmScope = confirmScope.parentElement) {
                  confirm = Array.from(confirmScope.querySelectorAll('button, span, div, a'))
                    .filter(ele => visible(ele) && compact(ele.innerText || ele.textContent) === '确认')
                    .sort((a, b) => {
                      const ar = a.getBoundingClientRect();
                      const br = b.getBoundingClientRect();
                      return (ar.width * ar.height) - (br.width * br.height);
                    })[0] || null;
                }
                if (confirm) {
                  confirm.scrollIntoView({block: 'center', inline: 'nearest'});
                  for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                    confirm.dispatchEvent(new MouseEvent(name, {
                      bubbles: true,
                      cancelable: true,
                      composed: true,
                      view: window,
                    }));
                  }
                }
                return {ok: true, values: target.inputs.slice(0, 2).map(input => input.value), confirmed: !!confirm};
                """,
                min_value,
                max_value,
            )
            if last_result and last_result.get("ok"):
                time.sleep(0.3)
                return
            time.sleep(0.2)
        raise RuntimeError(f"Could not fill custom age range: {last_result}")

    def close_open_dropdown(self) -> None:
        self.page.run_js(
            """
            const target = document.querySelector('.searchBarBox--IpmLs') || document.body;
            for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
              target.dispatchEvent(new MouseEvent(name, {
                bubbles: true,
                cancelable: true,
                composed: true,
                view: window,
              }));
            }
            """
        )
        time.sleep(0.3)

    @staticmethod
    def split_multi_values(value: str) -> list[str]:
        for sep in ["，", "、", ";", "；", "|", "\n"]:
            value = value.replace(sep, ",")
        return [item.strip() for item in value.split(",") if item.strip()]

    def has_visible_ai_popover(self) -> bool:
        return bool(
            self.page.run_js(
                """
                return Array.from(document.querySelectorAll('.ant-lpt-popover'))
                  .some(ele => {
                    const rect = ele.getBoundingClientRect();
                    return rect.width > 0
                      && rect.height > 0
                      && (ele.innerText || '').includes('AI');
                  });
                """
            )
        )

    def wait_for_input(self, placeholder: str, timeout: int = 10):
        return self.page.ele(f'xpath://input[@placeholder="{placeholder}"]', timeout=timeout)

    def wait_until_input_value(self, placeholder: str, expected: str, timeout: int = 5) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = self.page.run_js(
                """
                const placeholder = arguments[0];
                const input = Array.from(document.querySelectorAll('input[placeholder]'))
                  .find(ele => ele.placeholder === placeholder);
                return input ? input.value : null;
                """,
                placeholder,
            )
            if value == expected:
                return
            time.sleep(0.2)
        raise RuntimeError(f"Input did not take effect: {placeholder} -> {expected}")
