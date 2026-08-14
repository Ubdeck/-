from __future__ import annotations

import time
import uuid

from DrissionPage._functions.keys import Keys
from DrissionPage.errors import ContextLostError, ElementLostError

from .ai_matcher import MaimaiAiMatcherMixin
from .browser import connect_chromium_page
from .constants import DEFAULT_BROWSER_PORT, DEFAULT_MAIMAI_GREETING, MAIMAI_TALENTS_URL


FILTER_LABELS = ("城市地区", "学历要求", "工作年限", "就职公司", "性别", "年龄")
CITY_PLACEHOLDER = "请输入城市地区"
COMPANY_PLACEHOLDER = "请输入就职公司"
KEYWORD_PLACEHOLDER = '搜索提示：输入带双引号的完整词组，如"算法工程师"，可精准匹配人才'


class MaimaiRecruitPage(MaimaiAiMatcherMixin):
    """Facade for the Maimai recruiting workflow."""

    def __init__(self, port: int = DEFAULT_BROWSER_PORT, progress_callback=None, stop_event=None) -> None:
        self.progress_callback = progress_callback
        self.stop_event = stop_event
        self.page = connect_chromium_page(target_url=MAIMAI_TALENTS_URL, port=port)

    def check_stopped(self) -> None:
        if self.stop_event is not None and self.stop_event.is_set():
            raise RuntimeError("任务已停止。")

    def emit(self, event: str, message: str, data: dict | None = None) -> None:
        self.check_stopped()
        if self.progress_callback:
            self.progress_callback({"event": event, "message": message, "data": data or {}})

    def open(self, url: str | None = None) -> dict:
        self.check_stopped()
        target_url = (url or MAIMAI_TALENTS_URL).strip() or MAIMAI_TALENTS_URL
        self.page.get(target_url)
        return self.inspect_page()

    def run(self, config: dict) -> dict:
        self.emit("maimai_start", "开始打开脉脉人才主界面")
        state = self.open(MAIMAI_TALENTS_URL)
        message = "脉脉人才主界面已打开" if state.get("ready") else "已打开脉脉页面，请确认登录状态"
        self.emit("maimai_ready", message, state)
        filters_state = self.apply_filters(config)
        candidate_state = {}
        results = []
        if filters_state.get("applied_filters"):
            candidate_state = self.open_first_candidate()
            page_limit = self.clean_page_limit(config.get("maimai_page_limit"))
            results = self.extract_and_analyze_candidate_pages(config, page_limit)
        matched = sum(1 for item in results if item.get("match"))
        return {
            "status": "batch_completed" if results else "filters_applied",
            "processed": len(results),
            "matched": matched,
            "results": results,
            **state,
            **filters_state,
            **candidate_state,
        }

    def inspect_page(self) -> dict:
        self.check_stopped()
        state = self.page.run_js(
            """
            const bodyText = document.body ? (document.body.innerText || document.body.textContent || '') : '';
            const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
            const url = location.href;
            const title = document.title || '';
            const loginRequired = /登录|扫码|验证码|账号/.test(bodyText)
              && !/人才|招聘|候选|沟通|简历/.test(bodyText.slice(0, 3000));
            const ready = url.includes('maimai.cn')
              && /recruit\\/talents/.test(url)
              && !loginRequired;
            return {
              url,
              title,
              ready,
              login_required: loginRequired,
              text_sample: clean(bodyText).slice(0, 300),
            };
            """
        )
        return state or {"url": self.page.url, "title": "", "ready": False, "login_required": False, "text_sample": ""}

    def apply_filters(self, config: dict) -> dict:
        self.wait_for_filter_bar()
        self.emit("maimai_filters", "正在清理脉脉已有筛选条件")
        for label in FILTER_LABELS:
            self.clear_filter(label)
            self.close_open_popup()

        applied: dict[str, str] = {}
        keywords = self.clean_config_value(config.get("maimai_keywords"))
        keyword_mode = self.clean_config_value(config.get("maimai_keyword_mode")) or "所有"
        city = self.clean_config_value(config.get("maimai_city"))
        education = self.clean_config_value(config.get("maimai_education"))
        experience = self.clean_config_value(config.get("maimai_experience"))
        graduation_year = self.clean_config_value(config.get("maimai_graduation_year"))
        company = self.clean_config_value(config.get("maimai_company"))
        gender = self.clean_config_value(config.get("maimai_gender"))
        age_min = self.clean_age_value(config.get("maimai_age_min"))
        age_max = self.clean_age_value(config.get("maimai_age_max"))

        if city:
            self.select_city(city)
            applied["城市地区"] = city
        if education:
            self.select_education(education)
            applied["学历要求"] = education
        if experience:
            self.select_experience(experience, graduation_year)
            applied["工作年限"] = " ".join([experience, graduation_year]).strip()
        if company:
            companies = self.split_multi_values(company)
            self.select_companies(companies)
            applied["就职公司"] = "、".join(companies)
        if gender:
            self.select_gender(gender)
            applied["性别"] = gender
        if age_min or age_max:
            self.select_age_range(age_min, age_max)
            applied["年龄"] = "-".join([age_min or "不限", age_max or "不限"])
        if keywords:
            self.set_keywords(keywords, keyword_mode)
            applied["搜索关键词"] = f"{keyword_mode}：{keywords}"

        if applied:
            self.click_main_search_button(keywords)

        summary = self.current_filter_summary()
        message = "脉脉筛选条件已填入" if applied else "脉脉筛选条件已清理，未设置新条件"
        self.emit("maimai_filters_done", message, {"applied": applied, "summary": summary})
        return {"applied_filters": applied, "filter_summary": summary}

    def select_city(self, city: str) -> None:
        self.emit("maimai_filter", f"正在选择城市地区：{city}")
        self.open_filter("城市地区")
        self.fill_popup_input(CITY_PLACEHOLDER, city)
        self.click_city_search_result(city)
        self.close_open_popup()
        self.wait_for_popup_closed("城市地区")
        self.wait_for_summary("城市地区", [city])

    def select_education(self, education: str) -> None:
        value = education.replace("（", "(").replace("）", ")").strip()
        base = "本科及以上" if "只看统招本科" in value else education.strip()
        wants_recruitment_only = "只看统招本科" in value
        self.emit("maimai_filter", f"正在选择学历要求：{education}")
        self.open_filter("学历要求")
        self.click_list_option(base, "学历要求")
        if wants_recruitment_only:
            self.click_sub_option("只看统招本科", required=True)
        else:
            self.click_sub_option("不限", required=False)
        tokens = [base]
        if wants_recruitment_only:
            tokens.append("只看统招本科")
        self.wait_for_summary("学历要求", tokens)
        self.close_open_popup()

    def select_experience(self, experience: str, graduation_year: str = "") -> None:
        self.emit("maimai_filter", f"正在选择工作年限：{' '.join([experience, graduation_year]).strip()}")
        last_error = None
        for _ in range(2):
            try:
                self.open_filter("工作年限")
                self.click_list_option(experience, "工作年限")
                if experience == "在校/应届":
                    graduation_option = self.normalize_maimai_graduation_year(graduation_year)
                    self.click_sub_option(graduation_option or "不限", required=True, context="工作年限毕业年份")
                    tokens = [experience]
                    if graduation_option:
                        tokens.append(graduation_option)
                    self.wait_for_summary("工作年限", tokens)
                    self.close_open_popup()
                    return
                self.wait_for_popup_closed("工作年限")
                self.wait_for_summary("工作年限", [experience])
                return
            except RuntimeError as exc:
                last_error = exc
                self.close_open_popup()
                time.sleep(0.5)
        raise RuntimeError(str(last_error or f"脉脉工作年限未选择：{experience}"))

    @staticmethod
    def normalize_maimai_graduation_year(value: str) -> str:
        text = str(value or "").strip()
        if not text or text == "不限":
            return text
        if text.isdigit() and len(text) == 4:
            return f"{text}年毕业"
        if text.endswith("届") and text[:-1].isdigit() and len(text[:-1]) == 4:
            return f"{text[:-1]}年毕业"
        return text

    def select_company(self, company: str) -> None:
        self.select_companies([company])

    def select_companies(self, companies: list[str]) -> None:
        if not companies:
            return
        self.emit("maimai_filter", f"正在选择就职公司：{'、'.join(companies)}")
        self.open_filter("就职公司")
        selected_companies: list[str] = []
        for company in companies:
            self.fill_popup_input(COMPANY_PLACEHOLDER, company)
            self.click_search_result(company, "就职公司")
            selected_companies.append(company)
            self.wait_for_company_tags(selected_companies)
            time.sleep(0.3)
        self.close_open_popup()
        self.wait_for_popup_closed("就职公司")
        self.wait_for_summary("就职公司", companies)

    def set_keywords(self, keywords: str, keyword_mode: str) -> None:
        self.wait_for_filter_bar()
        mode = self.normalize_keyword_mode(keyword_mode)
        self.emit("maimai_filter", f"正在填写搜索关键词：{mode} - {keywords}")
        self.select_keyword_mode(mode)
        deadline = time.time() + 8
        last_error = None
        while time.time() < deadline:
            try:
                result = self.mark_keyword_input()
                if not result.get("ok"):
                    last_error = result
                else:
                    input_ele = self.find_marked_element(result["token"])
                    input_ele.click()
                    input_ele.clear()
                    input_ele.input(keywords)
                    time.sleep(0.35)
                    current = self.read_keyword_input()
                    if current == keywords:
                        return
                    last_error = {"reason": "keyword input value mismatch", "value": current}
            except Exception as exc:
                last_error = str(exc)
            self.check_stopped()
            time.sleep(0.2)
        raise RuntimeError(f"脉脉搜索关键词填入失败：{keywords}，{last_error}")

    def click_main_search_button(self, expected_keywords: str = "") -> None:
        self.emit("maimai_filter", "正在点击脉脉搜索按钮")
        self.close_open_popup()
        if expected_keywords and self.read_keyword_input() != expected_keywords:
            self.set_keywords(expected_keywords, self.read_keyword_mode())
        if expected_keywords and self.read_keyword_input() != expected_keywords:
            raise RuntimeError(f"脉脉搜索前关键词未保留：{expected_keywords}")
        deadline = time.time() + 6
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\\s+/g, ' ');
                const keywordInput = Array.from(document.querySelectorAll('input[placeholder]'))
                  .filter(visible)
                  .find(ele => ele.placeholder.startsWith('搜索提示：'));
                const keywordRect = keywordInput ? keywordInput.getBoundingClientRect() : null;
                const buttons = Array.from(document.querySelectorAll('.mui-btn-primary, button, [role=button], div'))
                  .filter(visible)
                  .map(ele => ({ele, text: clean(ele), cls: String(ele.className || ''), rect: ele.getBoundingClientRect()}))
                  .filter(item => item.text === '搜索' && item.cls.includes('mui-btn-primary'))
                  .filter(item => !keywordRect || (
                    item.rect.top >= keywordRect.top - 24
                    && item.rect.top <= keywordRect.bottom + 24
                  ))
                  .sort((a, b) => (a.rect.top - b.rect.top) || (a.rect.left - b.rect.left));
                const target = buttons[0];
                if (!target) {
                  return {ok: false, reason: 'main search button not found', buttons: buttons.map(item => item.text)};
                }
                const ele = target.ele;
                ele.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true, view: window}));
                ele.click();
                ele.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true, view: window}));
                return {ok: true, text: target.text};
                """
            )
            if last_result and last_result.get("ok"):
                time.sleep(1.0)
                return
            self.check_stopped()
            time.sleep(0.25)
        raise RuntimeError(f"脉脉搜索按钮点击失败：{last_result}")

    def open_first_candidate(self, timeout: float = 12.0) -> dict:
        self.emit("maimai_candidate", "搜索完成，正在打开第一个候选人")
        deadline = time.time() + timeout
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\s+/g, ' ');
                const cards = Array.from(document.querySelectorAll(
                  '.talent-common-card, [class*=talent-common-card], [class*=talent-card]'
                ))
                  .filter(visible)
                  .map(ele => {
                    const rect = ele.getBoundingClientRect();
                    const name = ele.querySelector('[class*=name___]') || ele.querySelector('.like-link-button');
                    return {
                      ele,
                      rect,
                      text: clean(ele),
                      name: name ? clean(name) : ''
                    };
                  })
                  .filter(item => item.text && item.rect.top >= 0 && item.rect.top < window.innerHeight)
                  .sort((a, b) => (a.rect.top - b.rect.top) || (a.rect.left - b.rect.left));
                const target = cards[0];
                if (!target) {
                  return {ok: false, reason: 'candidate card not found'};
                }
                const ele = target.ele;
                ele.scrollIntoView({block: 'center', inline: 'nearest'});
                const clickTarget = ele.querySelector('[class*=name___], .like-link-button') || ele;
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  clickTarget.dispatchEvent(new MouseEvent(name, {
                    bubbles: true,
                    cancelable: true,
                    composed: true,
                    view: window,
                  }));
                }
                if (clickTarget !== ele) ele.click();
                return {
                  ok: true,
                  name: target.name,
                  text: target.text.slice(0, 240)
                };
                """
            )
            if last_result and last_result.get("ok"):
                time.sleep(0.6)
                self.wait_for_candidate_detail()
                return {
                    "candidate_opened": True,
                    "candidate_name": str(last_result.get("name") or ""),
                    "candidate_view": "detail",
                }
            self.check_stopped()
            time.sleep(0.25)
        raise RuntimeError(f"脉脉第一个候选人打开失败：{last_result}")

    def wait_for_candidate_detail(self, timeout: float = 8.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const bodyText = document.body ? (document.body.innerText || '') : '';
                const detail = Array.from(document.querySelectorAll('[class*=left___], .ant-drawer-body'))
                  .filter(visible)
                  .map(ele => {
                    const rect = ele.getBoundingClientRect();
                    const text = (ele.innerText || ele.textContent || '').trim();
                    return {rect, text};
                  })
                  .filter(item => item.rect.left > 350 && item.rect.width > 240 && item.text.length > 20)
                  .sort((a, b) => b.rect.width - a.rect.width)[0];
                const hasDetailMarker = ['人才档案', '期望偏好', '基本信息', '工作经历', '教育经历']
                  .some(text => bodyText.includes(text));
                return !!detail && hasDetailMarker;
                """
            )
            if state:
                return
            self.check_stopped()
            time.sleep(0.2)
        raise RuntimeError("脉脉候选人详情界面未打开。")

    def extract_candidate_resumes_by_pages(self, page_limit: int = 1) -> list[dict]:
        self.wait_for_candidate_detail()
        page_limit = max(int(page_limit or 1), 1)
        resumes: list[dict] = []
        visited_signatures: set[str] = set()
        for page_number in range(1, page_limit + 1):
            candidates = self.drawer_candidate_descriptors()
            if not candidates:
                break
            signature = self.candidate_page_signature(candidates)
            if signature in visited_signatures:
                break
            visited_signatures.add(signature)
            self.emit("maimai_candidate", f"正在提取第 {page_number}/{page_limit} 页候选人简历")
            page_resumes = self.extract_current_page_resumes_once(candidates)
            for resume in page_resumes:
                resume["page_number"] = page_number
                resume["global_candidate_index"] = len(resumes) + 1
                resumes.append(resume)
            if page_number >= page_limit:
                break
            if not self.goto_next_candidate_page(signature):
                break
            self.wait_for_candidate_detail()
        pages_done = len({item.get("page_number") for item in resumes})
        self.emit("maimai_candidates_done", f"候选人简历已提取：{len(resumes)} 人，{pages_done} 页")
        return resumes

    def extract_and_analyze_candidate_pages(self, config: dict, page_limit: int = 1) -> list[dict]:
        self.wait_for_candidate_detail()
        page_limit = max(int(page_limit or 1), 1)
        results: list[dict] = []
        visited_signatures: set[str] = set()
        for page_number in range(1, page_limit + 1):
            candidates = self.drawer_candidate_descriptors()
            if not candidates:
                break
            signature = self.candidate_page_signature(candidates)
            if signature in visited_signatures:
                break
            visited_signatures.add(signature)
            self.emit("maimai_candidate", f"正在提取第 {page_number}/{page_limit} 页候选人简历")
            page_resumes = self.extract_current_page_resumes_once(candidates)
            page_start_index = len(results)
            for offset, resume in enumerate(page_resumes, start=1):
                resume["page_number"] = page_number
                resume["global_candidate_index"] = page_start_index + offset
            self.emit("maimai_candidates_done", f"第 {page_number} 页候选人简历已提取：{len(page_resumes)} 人")
            page_ai = self.analyze_current_page_candidates(page_resumes, config)
            page_results = page_ai.get("results", []) if isinstance(page_ai, dict) else []
            self.communicate_matched_candidates_on_current_page(page_results, candidates, config)
            for item in page_results:
                item["page_number"] = item.get("page_number") or page_number
                item["global_candidate_index"] = len(results) + 1
                results.append(item)
            if page_number >= page_limit:
                break
            if not self.goto_next_candidate_page(signature):
                break
            self.wait_for_candidate_detail()
        pages_done = len({item.get("page_number") for item in results})
        matched = sum(1 for item in results if item.get("match"))
        summary = {
            "status": "batch_completed",
            "processed": len(results),
            "matched": matched,
            "results": results,
        }
        self.save_batch_summary(results, summary)
        self.emit("maimai_ai_done", f"脉脉 AI 筛选完成：处理 {len(results)} 人，匹配 {matched} 人，{pages_done} 页", summary)
        return results

    def communicate_matched_candidates_on_current_page(
        self,
        page_results: list[dict],
        candidates: list[dict],
        config: dict,
    ) -> None:
        if not bool(config.get("maimai_auto_communicate", False)):
            return
        limit = self.clean_optional_positive_int(config.get("maimai_communicate_limit"))
        greeting = self.clean_config_value(config.get("maimai_greeting")) or DEFAULT_MAIMAI_GREETING
        attempted = 0
        for item in page_results:
            if not item.get("match") or item.get("next_action") == "skip":
                continue
            if limit is not None and attempted >= limit:
                item["communicate_status"] = "pending"
                item["communicate_note"] = "已达到本次脉脉沟通测试上限，留待后续处理。"
                continue
            page_index = self.clean_optional_positive_int(item.get("page_candidate_index"))
            if not page_index or page_index > len(candidates):
                item["communicate_status"] = "failed"
                item["communicate_note"] = "找不到对应的页内候选人卡片。"
                continue
            attempted += 1
            index = item.get("global_candidate_index") or item.get("page_candidate_index") or ""
            name = str(item.get("name") or "")
            self.emit("maimai_communicate_start", f"第 {index} 个脉脉候选人匹配，准备沟通：{name}")
            try:
                self.open_drawer_candidate(candidates[page_index - 1])
                communicate_result = self.communicate_current_candidate_and_request_phone(name, greeting=greeting)
                item["communicate_status"] = communicate_result.get("status", "unknown")
                item["communicate_note"] = communicate_result.get("message", "")
                phone = communicate_result.get("phone") or {}
                if phone:
                    item["phone_request_status"] = phone.get("status", "unknown")
                    item["phone_request_note"] = phone.get("message", "")
                self.emit(
                    "maimai_communicate_done",
                    f"第 {index} 个脉脉候选人沟通结果：{item['communicate_status']}，{item.get('communicate_note', '')}",
                    item,
                )
            except Exception as exc:
                item["communicate_status"] = "failed"
                item["communicate_note"] = str(exc)
                self.emit(
                    "maimai_communicate_failed",
                    f"第 {index} 个脉脉候选人沟通失败：{exc}",
                    {"error": str(exc), "candidate": item},
                )

    def communicate_current_candidate_and_request_phone(self, expected_name: str = "", greeting: str = "") -> dict:
        before_tab_id = self.page.tab_id
        before_tabs = set(self.page.tab_ids)
        if expected_name:
            self.wait_for_candidate_detail_name(expected_name, timeout=3.0)
        button = self.click_current_candidate_immediate_communicate()
        if button.get("status") == "already_communicated":
            return {"status": "already_communicated", "message": button.get("message", "页面显示“沟通”，已跳过。")}
        self.emit("maimai_communicate_send", f"正在填写招呼语并点击发送后继续沟通：{expected_name}" if greeting else f"正在点击发送后继续沟通：{expected_name}")
        self.click_send_then_continue(greeting=greeting)
        self.emit("maimai_communicate_send", "脉脉发送弹窗已关闭，正在等待消息页")
        try:
            chat_tab = self.wait_for_new_maimai_chat_tab(before_tabs, fallback_tab_id=before_tab_id)
        except RuntimeError as exc:
            send_state = self.inspect_maimai_communicate_send_state()
            if send_state.get("sent"):
                self.emit("maimai_communicate_send", "沟通已发送但消息页未自动打开，正在从当前候选人的沟通按钮进入消息页")
                retry_before_tabs = set(self.page.tab_ids)
                self.click_current_candidate_existing_communicate()
                chat_tab = self.wait_for_new_maimai_chat_tab(retry_before_tabs, fallback_tab_id=before_tab_id)
            else:
                raise RuntimeError(f"{exc}；发送状态未确认：{send_state.get('message', '')}") from exc
        phone_result = {"status": "unknown", "message": ""}
        try:
            self.emit("maimai_phone_request", "脉脉消息页已打开，正在等待会话加载")
            self.wait_for_maimai_chat_page(chat_tab, expected_name=expected_name)
            self.emit("maimai_phone_request", "正在点击交换手机")
            phone_result = self.request_phone_in_maimai_chat(chat_tab)
            self.emit("maimai_phone_request", f"交换手机结果：{phone_result.get('status')}，{phone_result.get('message', '')}")
            return {
                "status": "done",
                "message": f"已发送沟通并进入消息页处理交换手机：{phone_result.get('message', '')}",
                "phone": phone_result,
            }
        finally:
            self.close_maimai_chat_tab_and_return(chat_tab, before_tab_id)

    def click_current_candidate_immediate_communicate(self, timeout: float = 8.0) -> dict:
        deadline = time.time() + timeout
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && !ele.disabled;
                };
                const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                const textOf = ele => clean(ele.innerText || ele.textContent);
                const detailScopes = Array.from(document.querySelectorAll('*'))
                  .filter(visible)
                  .map(ele => ({ele, text: textOf(ele), rect: ele.getBoundingClientRect()}))
                  .filter(item => item.rect.left > 1000
                    && item.rect.width < 520
                    && item.text.startsWith('觉得人才还不错，你可以')
                    && item.text.includes('电话沟通')
                    && (item.text.includes('立即沟通') || item.text.includes('沟通') || item.text.includes('继续沟通')))
                  .sort((a, b) => {
                    const area = item => item.rect.width * item.rect.height;
                    return area(a) - area(b);
                  })
                  .map(item => item.ele);
                const scopes = detailScopes;
                if (!scopes.length) {
                  return {ok: false, reason: 'candidate action scope not found'};
                }
                const buttons = scopes.flatMap(scope => Array.from(scope.querySelectorAll('.mui-btn, button, [role=button], a, div, span')))
                  .filter(visible)
                  .map(ele => ({target: ele, text: textOf(ele), rect: ele.getBoundingClientRect(), cls: String(ele.className || '')}))
                  .filter(item => ['立即沟通', '沟通', '继续沟通'].includes(item.text))
                  .sort((a, b) => {
                    const rank = text => text === '立即沟通' ? 0 : (text === '沟通' ? 1 : 2);
                    const ranked = rank(a.text) - rank(b.text);
                    if (ranked !== 0) return ranked;
                    const aBtn = a.cls.includes('mui-btn-primary') ? 0 : 1;
                    const bBtn = b.cls.includes('mui-btn-primary') ? 0 : 1;
                    if (aBtn !== bBtn) return aBtn - bBtn;
                    return a.rect.left - b.rect.left;
                  });
                const immediate = buttons.find(item => item.text === '立即沟通');
                if (!immediate) {
                  const already = buttons.find(item => item.text === '沟通' || item.text === '继续沟通');
                  if (already) {
                    return {ok: true, status: 'already_communicated', text: already.text, message: `页面按钮为“${already.text}”，说明此前已沟通过，跳过。`};
                  }
                  return {ok: false, reason: 'immediate communicate button not found', buttons: buttons.map(item => item.text)};
                }
                const button = immediate.target;
                button.scrollIntoView({block: 'center', inline: 'nearest'});
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  button.dispatchEvent(new MouseEvent(name, {bubbles: true, cancelable: true, composed: true, view: window}));
                }
                return {ok: true, status: 'clicked', text: immediate.text, class_name: immediate.cls};
                """
            )
            if last_result and last_result.get("ok"):
                return last_result
            self.check_stopped()
            time.sleep(0.3)
        raise RuntimeError(f"脉脉立即沟通按钮处理失败：{last_result}")

    def click_current_candidate_existing_communicate(self, timeout: float = 8.0) -> dict:
        deadline = time.time() + timeout
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && !ele.disabled;
                };
                const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                const textOf = ele => clean(ele.innerText || ele.textContent);
                const detailScopes = Array.from(document.querySelectorAll('*'))
                  .filter(visible)
                  .map(ele => ({ele, text: textOf(ele), rect: ele.getBoundingClientRect()}))
                  .filter(item => item.rect.left > 1000
                    && item.rect.width < 520
                    && item.text.startsWith('觉得人才还不错，你可以')
                    && item.text.includes('电话沟通')
                    && (item.text.includes('沟通') || item.text.includes('继续沟通')))
                  .sort((a, b) => (a.rect.width * a.rect.height) - (b.rect.width * b.rect.height))
                  .map(item => item.ele);
                const scopes = detailScopes;
                if (!scopes.length) return {ok: false, reason: 'candidate action scope not found'};
                const buttons = scopes.flatMap(scope => Array.from(scope.querySelectorAll('.mui-btn, button, [role=button], a, div, span')))
                  .filter(visible)
                  .map(ele => ({ele, text: textOf(ele), cls: String(ele.className || ''), rect: ele.getBoundingClientRect()}))
                  .filter(item => item.text === '沟通' || item.text === '继续沟通')
                  .sort((a, b) => {
                    const aBtn = a.cls.includes('mui-btn') ? 0 : 1;
                    const bBtn = b.cls.includes('mui-btn') ? 0 : 1;
                    if (aBtn !== bBtn) return aBtn - bBtn;
                    return a.rect.top - b.rect.top || a.rect.left - b.rect.left;
                  });
                const target = buttons[0];
                if (!target) return {ok: false, reason: 'existing communicate button not found'};
                target.ele.scrollIntoView({block: 'center', inline: 'nearest'});
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  target.ele.dispatchEvent(new MouseEvent(name, {bubbles: true, cancelable: true, composed: true, view: window}));
                }
                return {ok: true, status: 'clicked', text: target.text, class_name: target.cls};
                """
            )
            if last_result and last_result.get("ok"):
                return last_result
            self.check_stopped()
            time.sleep(0.3)
        raise RuntimeError(f"脉脉沟通按钮打开消息页失败：{last_result}")

    def click_send_then_continue(self, timeout: float = 10.0, greeting: str = "") -> dict:
        deadline = time.time() + timeout
        last_result = None
        greeting = self.clean_config_value(greeting)
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const greeting = String(arguments[0] || '').trim();
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && !ele.disabled;
                };
                const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                const textOf = ele => clean(ele.innerText || ele.textContent);
                const scopes = Array.from(document.querySelectorAll('.mui-modal-wrap .mui-modal, [role=dialog], [class*=modal], [class*=Modal], [class*=dialog], [class*=Dialog]'))
                  .filter(visible)
                  .filter(ele => {
                    const text = textOf(ele);
                    return text.includes('招聘立即沟通') || text.includes('发送后继续沟通') || text.includes('请选择加入职位');
                  })
                  .sort((a, b) => b.getBoundingClientRect().width * b.getBoundingClientRect().height - a.getBoundingClientRect().width * a.getBoundingClientRect().height);
                const scope = scopes[0];
                if (!scope) return {ok: false, reason: 'send modal not found'};
                let greetingFilled = false;
                if (greeting) {
                  const editables = Array.from(scope.querySelectorAll('textarea, input[type=text], input:not([type]), [contenteditable=true], [contenteditable="true"]'))
                    .filter(visible)
                    .map(ele => ({ele, rect: ele.getBoundingClientRect(), tag: ele.tagName.toLowerCase()}))
                    .sort((a, b) => {
                      const rank = item => item.tag === 'textarea' ? 0 : (item.ele.isContentEditable ? 1 : 2);
                      return rank(a) - rank(b) || b.rect.width * b.rect.height - a.rect.width * a.rect.height;
                    });
                  const editable = editables[0] && editables[0].ele;
                  if (!editable) return {ok: false, reason: 'greeting input not found'};
                  editable.focus();
                  if (editable.isContentEditable) {
                    editable.innerText = greeting;
                    editable.dispatchEvent(new InputEvent('input', {bubbles: true, cancelable: true, inputType: 'insertText', data: greeting}));
                  } else {
                    const proto = editable.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                    if (setter) setter.call(editable, greeting);
                    else editable.value = greeting;
                    editable.dispatchEvent(new Event('input', {bubbles: true}));
                    editable.dispatchEvent(new Event('change', {bubbles: true}));
                  }
                  const currentValue = editable.isContentEditable ? clean(editable.innerText || editable.textContent) : clean(editable.value);
                  if (currentValue !== greeting) return {ok: false, reason: 'greeting input value not confirmed', value: currentValue};
                  greetingFilled = true;
                }
                const target = Array.from(scope.querySelectorAll('.mui-btn, button, [role=button], a, div, span'))
                  .filter(visible)
                  .map(ele => {
                    const clickable = ele.closest('.mui-btn, button, [role=button], a') || ele;
                    return {
                      ele: clickable,
                      text: textOf(ele),
                      cls: String(clickable.className || ''),
                      rect: clickable.getBoundingClientRect(),
                    };
                  })
                  .filter(item => item.text === '发送后继续沟通')
                  .sort((a, b) => {
                    const aButton = a.cls.includes('mui-btn') ? 0 : 1;
                    const bButton = b.cls.includes('mui-btn') ? 0 : 1;
                    return aButton - bButton || a.rect.top - b.rect.top;
                  })[0];
                if (!target) return {ok: false, reason: 'send then continue button not found'};
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  target.ele.dispatchEvent(new MouseEvent(name, {bubbles: true, cancelable: true, composed: true, view: window}));
                }
                return {ok: true, text: target.text, class_name: target.cls, greeting_filled: greetingFilled};
                """,
                greeting,
            )
            if last_result and last_result.get("ok"):
                if self.wait_for_maimai_send_modal_closed(timeout=3.0):
                    return {**last_result, "confirmed": True}
                last_result = {**last_result, "reason": "send modal did not close after click"}
            self.check_stopped()
            time.sleep(0.3)
        raise RuntimeError(f"脉脉发送后继续沟通按钮点击失败：{last_result}")

    def wait_for_maimai_send_modal_closed(self, timeout: float = 3.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            is_open = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
                return Array.from(document.querySelectorAll('.mui-modal-wrap, .mui-modal'))
                  .filter(visible)
                  .some(ele => {
                    const text = clean(ele.innerText || ele.textContent);
                    return text.includes('招聘立即沟通') || text.includes('发送后继续沟通');
                  });
                """
            )
            if not is_open:
                return True
            self.check_stopped()
            time.sleep(0.2)
        return False

    def wait_for_new_maimai_chat_tab(self, before_tabs: set[str], fallback_tab_id: str, timeout: float = 25.0):
        deadline = time.time() + timeout
        last_tabs: list[str] = []
        while time.time() < deadline:
            tab_ids = list(self.page.tab_ids)
            last_tabs = tab_ids
            new_ids = [tab_id for tab_id in tab_ids if tab_id not in before_tabs]
            for tab_id in new_ids:
                tab = self.page.get_tab(tab_id)
                try:
                    url = str(tab.url or "")
                    if "maimai.cn" in url and ("/im" in url or "scene=direct_modal" in url or "scene=profile" in url):
                        return tab
                except Exception:
                    return tab
            try:
                current_url = str(self.page.url or "")
                if "/im" in current_url or "scene=direct_modal" in current_url:
                    return self.page
            except Exception:
                pass
            try:
                targets = self.page.run_cdp("Target.getTargets").get("targetInfos", [])
                for target in targets:
                    tab_id = str(target.get("targetId") or "")
                    url = str(target.get("url") or "")
                    if (
                        tab_id
                        and tab_id not in before_tabs
                        and target.get("type") == "page"
                        and "maimai.cn" in url
                        and ("/im" in url or "scene=direct_modal" in url)
                    ):
                        return self.page.get_tab(tab_id)
            except Exception:
                pass
            self.check_stopped()
            time.sleep(0.35)
        raise RuntimeError(f"脉脉消息页新标签未打开：before={sorted(before_tabs)} after={last_tabs} current={fallback_tab_id}")

    def inspect_maimai_communicate_send_state(self, timeout: float = 5.0) -> dict:
        deadline = time.time() + timeout
        state = None
        while time.time() < deadline:
            state = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
                const textOf = ele => clean(ele.innerText || ele.textContent);
                const hasSendModal = Array.from(document.querySelectorAll('.mui-modal-wrap, .mui-modal'))
                  .filter(visible)
                  .some(ele => {
                    const text = textOf(ele);
                    return text.includes('招聘立即沟通') || text.includes('发送后继续沟通');
                  });
                const drawer = document.querySelector('.ant-drawer-open .ant-drawer-content')
                  || document.querySelector('.ant-drawer-content');
                const actionTexts = drawer
                  ? Array.from(drawer.querySelectorAll('.mui-btn, button, [role=button], a'))
                    .filter(visible)
                    .map(textOf)
                    .filter(text => ['立即沟通', '沟通', '继续沟通'].includes(text))
                  : [];
                const sent = !hasSendModal && (actionTexts.includes('沟通') || actionTexts.includes('继续沟通'));
                return {
                  sent,
                  has_send_modal: hasSendModal,
                  action_texts: actionTexts,
                  message: sent
                    ? '发送弹窗已关闭，候选人按钮已变为“沟通”。'
                    : (hasSendModal ? '发送弹窗仍在页面上。' : `候选人按钮状态：${actionTexts.join('、') || '未找到'}`),
                };
                """
            )
            if state and (state.get("sent") or state.get("has_send_modal")):
                return state
            self.check_stopped()
            time.sleep(0.3)
        return state or {"sent": False, "message": "无法确认发送状态。"}

    def wait_for_maimai_chat_page(self, chat_tab, expected_name: str = "", timeout: float = 20.0) -> None:
        deadline = time.time() + timeout
        state = None
        while time.time() < deadline:
            frame = self.get_maimai_chat_frame(chat_tab, timeout=1.0)
            if frame is None:
                self.check_stopped()
                time.sleep(0.3)
                continue
            state = frame.run_js(
                """
                const expectedName = String(arguments[0] || '');
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                const textOf = ele => clean(ele.innerText || ele.textContent);
                const header = Array.from(document.querySelectorAll('.dialogue-header'))
                  .filter(visible)
                  .map(textOf)
                  .find(Boolean) || '';
                const activeText = Array.from(document.querySelectorAll('*'))
                  .filter(visible)
                  .filter(ele => ele.getBoundingClientRect().left >= 300)
                  .map(textOf)
                  .filter(Boolean)
                  .join(' ');
                return {
                  url: location.href,
                  ready: !!header
                    && (activeText.includes('交换手机')
                      || activeText.includes('申请中')
                      || activeText.includes('已申请')
                      || activeText.includes('发起了交换手机号的申请')
                      || activeText.includes('发送职位')),
                  hasName: !expectedName || header.includes(expectedName),
                  header,
                  sample: activeText.slice(0, 240)
                };
                """,
                expected_name,
            )
            if state and state.get("ready") and state.get("hasName"):
                return
            self.check_stopped()
            time.sleep(0.4)
        raise RuntimeError(f"脉脉消息页未加载完成：{state}")

    def request_phone_in_maimai_chat(self, chat_tab, timeout: float = 12.0) -> dict:
        deadline = time.time() + timeout
        last_result = None
        while time.time() < deadline:
            frame = self.get_maimai_chat_frame(chat_tab, timeout=1.0)
            if frame is None:
                self.check_stopped()
                time.sleep(0.3)
                continue
            active_text = self.read_maimai_active_chat_text(frame)
            existing_state = self.read_maimai_phone_request_state(active_text, already=True)
            if existing_state:
                return existing_state
            click_result = frame.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && !ele.disabled;
                };
                const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                const textOf = ele => clean(ele.innerText || ele.textContent);
                const candidates = Array.from(document.querySelectorAll('.tool.normal, [class*=tool], button, [role=button], a'))
                  .filter(visible)
                  .map(ele => {
                    const rect = ele.getBoundingClientRect();
                    return {ele, text: textOf(ele), cls: String(ele.className || ''), rect};
                  })
                  .filter(item => item.rect.left >= 300 && item.text === '交换手机')
                  .sort((a, b) => {
                    const aTool = a.cls.includes('tool normal') ? 0 : 1;
                    const bTool = b.cls.includes('tool normal') ? 0 : 1;
                    if (aTool !== bTool) return aTool - bTool;
                    return b.rect.top - a.rect.top;
                  });
                const target = candidates[0];
                if (!target) return {ok: false, reason: 'exchange phone tool not found'};
                target.ele.scrollIntoView({block: 'center', inline: 'nearest'});
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  target.ele.dispatchEvent(new MouseEvent(name, {bubbles: true, cancelable: true, composed: true, view: window}));
                }
                return {ok: true, text: target.text, class_name: target.cls};
                """
            )
            if click_result and click_result.get("ok"):
                confirm_result = self.confirm_maimai_phone_request(frame)
                requested_state = self.wait_for_maimai_phone_request_state(frame, timeout=4.0)
                if requested_state:
                    requested_state["status"] = "requested" if requested_state.get("status") == "already_requested" else requested_state.get("status")
                    return requested_state
                active_text = self.read_maimai_active_chat_text(frame)
                return {
                    "status": "clicked",
                    "message": f"已点击交换手机，但未确认到申请状态。确认弹窗：{confirm_result.get('message', '') if isinstance(confirm_result, dict) else ''}；当前会话片段：{active_text[:120]}",
                }
            last_result = {"reason": "exchange phone button not found", "sample": active_text[:240]}
            self.check_stopped()
            time.sleep(0.5)
        return {"status": "failed", "message": str(last_result or "交换手机按钮未找到")}

    def read_maimai_active_chat_text(self, chat_frame) -> str:
        return str(
            chat_frame.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                return Array.from(document.querySelectorAll('*'))
                  .filter(visible)
                  .filter(ele => ele.getBoundingClientRect().left >= 300)
                  .map(ele => clean(ele.innerText || ele.textContent))
                  .filter(Boolean)
                  .join(' ');
                """
            )
            or ""
        )

    @staticmethod
    def read_maimai_phone_request_state(body_text: str, already: bool = False) -> dict | None:
        text = str(body_text or "")
        if any(token in text for token in ("申请中", "已申请", "等待对方同意", "您向对方发起了交换手机号的申请", "发起了交换手机号的申请")):
            return {
                "status": "already_requested" if already else "requested",
                "message": "手机号交换申请已在处理中，已返回候选人列表。" if already else "已申请交换手机。",
            }
        if any(token in text for token in ("已交换手机号", "已交换手机", "手机号已交换")):
            return {"status": "already_available", "message": "手机号已交换或可查看，已返回候选人列表。"}
        return None

    def wait_for_maimai_phone_request_state(self, chat_frame, timeout: float = 4.0) -> dict | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            active_text = self.read_maimai_active_chat_text(chat_frame)
            state = self.read_maimai_phone_request_state(active_text, already=False)
            if state:
                return state
            self.check_stopped()
            time.sleep(0.35)
        return None

    def get_maimai_chat_frame(self, chat_tab, timeout: float = 3.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                frame = chat_tab.get_frame("#imIframe", timeout=0.6)
                if frame:
                    return frame
            except Exception:
                pass
            self.check_stopped()
            time.sleep(0.2)
        return None

    def confirm_maimai_phone_request(self, chat_frame) -> dict:
        try:
            result = chat_frame.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && !ele.disabled;
                };
                const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                const textOf = ele => clean(ele.innerText || ele.textContent);
                const scopes = Array.from(document.querySelectorAll('[role=dialog], [class*=modal], [class*=Modal], [class*=popover], [class*=Popover]'))
                  .filter(visible)
                  .filter(ele => {
                    const text = textOf(ele);
                    return (text.includes('交换手机') || text.includes('手机号') || text.includes('手机')) && /确定|确认|申请/.test(text);
                  });
                for (const scope of scopes) {
                  const button = Array.from(scope.querySelectorAll('button, [role=button], a, div, span'))
                    .filter(visible)
                    .map(ele => ({ele, text: textOf(ele), cls: String(ele.className || '')}))
                    .find(item => /^(确定|确认|申请|确认交换|确定交换|确认申请|确定申请)$/.test(item.text) || (item.cls.includes('primary') && /确定|确认|申请/.test(item.text)));
                  if (button) {
                    for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                      button.ele.dispatchEvent(new MouseEvent(name, {bubbles: true, cancelable: true, composed: true, view: window}));
                    }
                    return {ok: true, text: button.text, message: `已点击确认按钮：${button.text}`};
                  }
                }
                return {ok: false, reason: 'no confirm dialog', message: '未出现确认弹窗'};
                """
            )
            return result if isinstance(result, dict) else {"ok": False, "message": "确认弹窗状态未知"}
        except Exception:
            return {"ok": False, "message": "确认弹窗处理异常"}

    def close_maimai_chat_tab_and_return(self, chat_tab, original_tab_id: str) -> None:
        try:
            chat_tab_id = getattr(chat_tab, "tab_id", None)
            if chat_tab_id and chat_tab_id != original_tab_id:
                self.page.run_cdp("Target.closeTarget", targetId=chat_tab_id)
        finally:
            try:
                self.page.activate_tab(original_tab_id)
            except Exception:
                pass

    def extract_current_page_candidate_resumes(self) -> list[dict]:
        self.wait_for_candidate_detail()
        candidates = self.drawer_candidate_descriptors()
        if not candidates:
            return []
        self.emit("maimai_candidate", "正在提取当前页候选人简历")
        resumes = self.extract_current_page_resumes_once(candidates)
        self.emit("maimai_candidates_done", f"本页候选人简历已提取：{len(resumes)} 人")
        return resumes

    def extract_current_page_resumes_once(self, candidates: list[dict]) -> list[dict]:
        total = len(candidates)
        resumes: list[dict] = []
        for index, candidate in enumerate(candidates, start=1):
            self.emit("maimai_candidate", f"正在提取本页候选人简历：{index}/{total}")
            summary = self.open_drawer_candidate(candidate)
            resume = self.extract_current_candidate_resume(summary.get("name", ""))
            resume["page_candidate_index"] = index
            resume["list_summary"] = summary
            resumes.append(resume)
        return resumes

    def candidate_page_signature(self, candidates: list[dict]) -> str:
        def part(item: dict) -> str:
            name = str(item.get("name") or "")
            summary = str(item.get("summary") or "").replace("\n", " ")[:80]
            return f"{name}:{summary}"

        head = [part(item) for item in candidates[:5]]
        tail = [part(item) for item in candidates[-5:]]
        return "|".join(head + tail)

    def goto_next_candidate_page(self, previous_signature: str = "", timeout: float = 8.0) -> bool:
        deadline = time.time() + timeout
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\\s+/g, ' ');
                const drawer = document.querySelector('.ant-drawer-open .ant-drawer-content')
                  || document.querySelector('.ant-drawer-content');
                if (!drawer) return {ok: false, reason: 'drawer not found'};
                const disabled = ele => {
                  const cls = String(ele.className || '');
                  const aria = String(ele.getAttribute('aria-disabled') || '');
                  return ele.disabled
                    || aria === 'true'
                    || cls.includes('disabled')
                    || cls.includes('disable')
                    || ele.closest('[aria-disabled="true"], [disabled], [class*="disabled"], [class*="disable"]');
                };
                const items = Array.from(drawer.querySelectorAll('*'))
                  .filter(visible)
                  .map(ele => ({ele, text: clean(ele), cls: String(ele.className || ''), rect: ele.getBoundingClientRect(), disabled: !!disabled(ele)}));
                const controls = items
                  .filter(item => item.text === '跳转至下一页'
                    || item.text === '下一页'
                    || item.cls.includes('next-btn')
                    || item.cls.includes('nextBtn')
                    || item.cls.includes('next___'))
                  .filter(item => !item.disabled)
                  .sort((a, b) => (a.rect.top - b.rect.top) || (a.rect.left - b.rect.left));
                const target = controls[0];
                if (!target) {
                  return {
                    ok: false,
                    reason: 'next page control not found',
                    controls: items
                      .filter(item => item.cls.includes('btn') || item.text.includes('页') || item.text.includes('跳转'))
                      .map(item => ({text: item.text.slice(0, 80), cls: item.cls.slice(0, 80)}))
                      .slice(0, 20)
                  };
                }
                const ele = target.ele;
                ele.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true, view: window}));
                ele.click();
                ele.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true, view: window}));
                return {ok: true, text: target.text, cls: target.cls};
                """
            )
            if last_result and last_result.get("ok"):
                if self.wait_for_candidate_page_changed(previous_signature):
                    return True
                return False
            self.check_stopped()
            time.sleep(0.25)
        self.emit("maimai_candidate", f"脉脉未找到下一页，停止翻页：{last_result}")
        return False

    def wait_for_candidate_page_changed(self, previous_signature: str, timeout: float = 8.0) -> bool:
        if not previous_signature:
            time.sleep(1.0)
            return True
        deadline = time.time() + timeout
        while time.time() < deadline:
            candidates = self.drawer_candidate_descriptors()
            if candidates and self.candidate_page_signature(candidates) != previous_signature:
                return True
            self.check_stopped()
            time.sleep(0.3)
        return False

    def drawer_candidate_count(self) -> int:
        value = self.page.run_js(
            """
            const drawer = document.querySelector('.ant-drawer-open .ant-drawer-content')
              || document.querySelector('.ant-drawer-content');
            const list = drawer && drawer.querySelector('[class*=talent-list]');
            if (!list) return 0;
            return Array.from(list.querySelectorAll('[class*=card___3uNBJ]')).length;
            """
        )
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def drawer_candidate_descriptors(self) -> list[dict]:
        value = self.page.run_js(
            """
            const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\s+/g, ' ');
            const drawer = document.querySelector('.ant-drawer-open .ant-drawer-content')
              || document.querySelector('.ant-drawer-content');
            const list = drawer && drawer.querySelector('[class*=talent-list]');
            if (!list) return [];
            const seen = new Set();
            return Array.from(list.querySelectorAll('[class*=card___3uNBJ]'))
              .map(card => {
                const nameEle = card.querySelector('[class*=name___3HTf0]');
                return {
                  name: nameEle ? clean(nameEle) : '',
                  summary: clean(card)
                };
              })
              .filter(item => {
                const key = `${item.name}|${item.summary}`;
                if (!item.name || seen.has(key)) return false;
                seen.add(key);
                return true;
              });
            """
        )
        return value if isinstance(value, list) else []

    def open_drawer_candidate(self, candidate: dict | int, timeout: float = 12.0) -> dict:
        if isinstance(candidate, dict):
            candidate_name = str(candidate.get("name") or "")
            candidate_summary = str(candidate.get("summary") or "")
            candidate_index = -1
        else:
            candidate_name = ""
            candidate_summary = ""
            candidate_index = int(candidate)
        last_result = None
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            self.check_stopped()
            try:
                last_result = self.page.run_js(
                """
                const index = Number(arguments[0]);
                const expectedName = String(arguments[1] || '');
                const expectedSummary = String(arguments[2] || '');
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\s+/g, ' ');
                const drawer = document.querySelector('.ant-drawer-open .ant-drawer-content')
                  || document.querySelector('.ant-drawer-content');
                const list = drawer && drawer.querySelector('[class*=talent-list]');
                if (!list) return {ok: false, reason: 'drawer candidate list not found'};
                const cards = Array.from(list.querySelectorAll('[class*=card___3uNBJ]'));
                const cleanName = card => {
                  const name = card.querySelector('[class*=name___3HTf0]');
                  return name ? clean(name) : '';
                };
                const card = expectedName
                  ? (cards.find(item => cleanName(item) === expectedName
                      && expectedSummary
                      && clean(item) === expectedSummary)
                    || cards.find(item => cleanName(item) === expectedName))
                  : cards[index];
                if (!card) return {ok: false, reason: 'drawer candidate card not found', index, count: cards.length};
                const name = cleanName(card);
                const summary = clean(card);
                card.scrollIntoView({block: 'center', inline: 'nearest'});
                card.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true, view: window}));
                card.click();
                card.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true, view: window}));
                return {ok: true, index, name, summary};
                """,
                candidate_index,
                candidate_name,
                candidate_summary,
                )
            except (ContextLostError, ElementLostError):
                time.sleep(0.25)
                continue
            if last_result and last_result.get("ok"):
                expected_name = str(last_result.get("name") or "")
                time.sleep(0.45)
                try:
                    self.ensure_talent_profile_tab(timeout=5.0)
                    self.wait_for_candidate_detail_name(expected_name, timeout=timeout)
                except RuntimeError as exc:
                    last_result = {
                        "ok": False,
                        "reason": str(exc),
                        "attempt": attempt,
                        "name": expected_name,
                    }
                    time.sleep(0.6)
                    continue
                return {
                    "name": expected_name,
                    "summary": str(last_result.get("summary") or ""),
                }
            self.check_stopped()
            time.sleep(0.2)
        raise RuntimeError(f"脉脉候选人列表点击失败：{last_result}")

    def ensure_talent_profile_tab(self, timeout: float = 3.0) -> None:
        deadline = time.time() + timeout
        last_state = None
        while time.time() < deadline:
            last_state = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\\s+/g, ' ');
                const drawer = document.querySelector('.ant-drawer-open .ant-drawer-content')
                  || document.querySelector('.ant-drawer-content');
                if (!drawer) return {ok: false, reason: 'drawer not found'};
                const left = Array.from(drawer.querySelectorAll('[class*=left___]'))
                  .filter(visible)
                  .sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width)[0];
                const detailText = left ? clean(left) : '';
                if (detailText.includes('期望偏好') || detailText.includes('工作经历') || detailText.includes('教育经历') || detailText.includes('附件简历')) {
                  return {ok: true, already: true};
                }
                const candidates = Array.from(drawer.querySelectorAll('*'))
                  .filter(visible)
                  .map(ele => ({ele, text: clean(ele), rect: ele.getBoundingClientRect()}))
                  .filter(item => item.text === '人才档案' || item.text.startsWith('人才档案 '))
                  .sort((a, b) => (a.rect.top - b.rect.top) || (a.rect.left - b.rect.left));
                const target = candidates[0];
                if (!target) return {ok: false, reason: 'talent profile tab not found', sample: detailText.slice(0, 160)};
                target.ele.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true, view: window}));
                target.ele.click();
                target.ele.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true, view: window}));
                return {ok: true, clicked: true, text: target.text};
                """
            )
            if last_state and last_state.get("ok"):
                time.sleep(0.35)
                return
            self.check_stopped()
            time.sleep(0.2)
        raise RuntimeError(f"脉脉人才档案页签未切回：{last_state}")

    def wait_for_candidate_detail_name(self, expected_name: str, timeout: float = 12.0) -> None:
        deadline = time.time() + timeout
        state = None
        while time.time() < deadline:
            try:
                state = self.page.run_js(
                    """
                    const expectedName = arguments[0];
                    const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\s+/g, ' ');
                    const drawer = document.querySelector('.ant-drawer-open .ant-drawer-content')
                      || document.querySelector('.ant-drawer-content');
                    const drawerText = drawer ? clean(drawer) : '';
                    const selectedCard = drawer && Array.from(drawer.querySelectorAll('[class*=talent-list] [class*=card___3uNBJ]'))
                      .find(ele => {
                        const style = String(ele.getAttribute('style') || '');
                        return style.includes('241, 246, 255') || style.includes('51, 117, 255');
                      });
                    const selectedName = selectedCard
                      ? clean(selectedCard.querySelector('[class*=name___3HTf0]') || selectedCard)
                      : '';
                    const detailPanel = Array.from(drawer ? drawer.querySelectorAll('[class*=left___]') : [])
                      .filter(ele => {
                        const rect = ele.getBoundingClientRect();
                        const style = getComputedStyle(ele);
                        return rect.width > 240 && rect.height > 160
                          && rect.left > 350
                          && style.display !== 'none'
                          && style.visibility !== 'hidden'
                          && clean(ele).length > 20;
                      })
                      .sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width)[0];
                    const detailText = detailPanel ? clean(detailPanel) : '';
                    const lines = detailText.split(/\\s+/).filter(Boolean);
                    const detailNameEle = detailPanel && (
                      detailPanel.querySelector('[class*=showNameStyle]')
                      || detailPanel.querySelector('[class*=font_title]')
                    );
                    const detailName = detailNameEle ? clean(detailNameEle) : (lines[0] || '');
                    const hasResumeMarker = ['期望偏好', '工作经历', '教育经历', '附件简历']
                      .some(text => detailText.includes(text));
                    const isDynamicTab = detailText.startsWith('人才档案 职位记录')
                      && detailText.includes('实名动态')
                      && !hasResumeMarker;
                    const nameMatches = !expectedName || detailName === expectedName || detailText.startsWith(expectedName + ' ');
                    const hasDetail = !!detailPanel && nameMatches && hasResumeMarker && !isDynamicTab;
                    return {
                      ready: hasDetail,
                      currentName: selectedName,
                      detailName,
                      name_matches: nameMatches,
                      has_detail: hasDetail,
                      detail_panel: !!detailPanel,
                      has_resume_marker: hasResumeMarker,
                      sample: detailText.slice(0, 180)
                    };
                    """,
                    expected_name,
                )
            except (ContextLostError, ElementLostError):
                time.sleep(0.25)
                continue
            except Exception as exc:
                if "ContextLostError" in type(exc).__name__ or "页面被刷新" in str(exc):
                    time.sleep(0.25)
                    continue
                raise
            if state and state.get("ready"):
                return
            self.check_stopped()
            time.sleep(0.2)
        raise RuntimeError(f"脉脉候选人详情未切换到：{expected_name}，{state}")
    def extract_current_candidate_resume(self, expected_name: str = "") -> dict:
        last_error = None
        for _ in range(35):
            try:
                data = self._extract_current_candidate_resume_once(expected_name)
                if data and data.get("ok"):
                    data.pop("ok", None)
                    return data
                last_error = data
            except (ContextLostError, ElementLostError) as exc:
                last_error = exc
            except Exception as exc:
                if "ContextLostError" in type(exc).__name__ or "页面被刷新" in str(exc):
                    last_error = exc
                else:
                    raise
            self.check_stopped()
            time.sleep(0.35)
        raise RuntimeError(f"脉脉候选人简历提取失败：{last_error}")

    def _extract_current_candidate_resume_once(self, expected_name: str = "") -> dict:
        data = self.page.run_js(
            """
            const expectedName = String(arguments[0] || '');
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0 && rect.height > 0
                && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const cleanText = value => String(value || '')
              .replace(/\u00a0/g, ' ')
              .replace(/[ \t]+/g, ' ')
              .replace(/\\n[ \t]+/g, '\\n')
              .replace(/[ \t]+\\n/g, '\\n')
              .replace(/\\n{3,}/g, '\\n\\n')
              .trim();
            const linesOf = text => cleanText(text).split('\\n').map(item => item.trim()).filter(Boolean);
            const section = (lines, heading, stops) => {
              const start = lines.findIndex(line => line === heading);
              if (start < 0) return '';
              let end = lines.length;
              for (let i = start + 1; i < lines.length; i += 1) {
                if (stops.includes(lines[i])) {
                  end = i;
                  break;
                }
              }
              return lines.slice(start + 1, end).join('\\n').trim();
            };
            const drawer = document.querySelector('.ant-drawer-open .ant-drawer-content')
              || document.querySelector('.ant-drawer-content');
            const detail = drawer && Array.from(drawer.querySelectorAll('[class*=left___]'))
              .filter(visible)
                .find(ele => {
                  const rect = ele.getBoundingClientRect();
                  const text = ele.innerText || ele.textContent || '';
                  return rect.left > 350 && rect.width > 240 && text.trim().length > 20;
                });
            if (!detail) return {ok: false, reason: 'candidate detail panel not found'};
            const rawText = cleanText(detail.innerText || detail.textContent || '');
            const lines = linesOf(rawText);
            const headings = ['期望偏好', '工作经历', '教育经历', '项目经历', '职业标签', '更多资料'];
            const selectedCard = drawer.querySelector('[class*=talent-list] [class*=card___3uNBJ][style*="rgb(241, 246, 255)"]')
              || drawer.querySelector('[class*=talent-list] [class*=card___3uNBJ][style*="51, 117, 255"]');
            const selectedNameEle = selectedCard && selectedCard.querySelector('[class*=name___3HTf0]');
            const nameEle = detail.querySelector('[class*=showNameStyle]')
              || detail.querySelector('[class*=font_title]')
              || selectedNameEle;
            const name = nameEle ? cleanText(nameEle.innerText || nameEle.textContent || '') : (lines[0] || '');
            const hasResumeMarker = ['期望偏好', '工作经历', '教育经历', '附件简历']
              .some(text => rawText.includes(text));
            const isDynamicTab = rawText.startsWith('人才档案\\n职位记录')
              && rawText.includes('实名动态')
              && !hasResumeMarker;
            const nameMatches = !expectedName || name === expectedName || rawText.startsWith(expectedName + '\\n');
            if (!nameMatches || !hasResumeMarker || isDynamicTab) {
              return {
                ok: false,
                reason: 'candidate resume not ready',
                expectedName,
                name,
                nameMatches,
                hasResumeMarker,
                isDynamicTab,
                sample: rawText.slice(0, 180)
              };
            }
            const headerText = cleanText((drawer.querySelector('.ant-drawer-header') || drawer).innerText || '');
            const match = headerText.match(/\((\d+)\s*\/\s*(\d+)\)/);
            return {
              ok: true,
              name,
              drawer_index: match ? Number(match[1]) : null,
              drawer_total: match ? Number(match[2]) : null,
              basic_info: lines.slice(0, Math.max(1, lines.indexOf('人才档案'))).join('\\n'),
              expectation: section(lines, '期望偏好', headings),
              work_experience: section(lines, '工作经历', headings),
              education_experience: section(lines, '教育经历', headings),
              project_experience: section(lines, '项目经历', headings),
              career_tags: section(lines, '职业标签', headings),
              more_info: section(lines, '更多资料', headings),
              resume_text: rawText,
              selected_card_text: selectedCard ? cleanText(selectedCard.innerText || selectedCard.textContent || '') : ''
            };
            """,
            expected_name,
        )
        return data

    def read_keyword_input(self) -> str:
        value = self.page.run_js(
            """
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0 && rect.height > 0
                && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const input = Array.from(document.querySelectorAll('input[placeholder]'))
              .filter(visible)
              .find(ele => ele.placeholder.startsWith('搜索提示：'));
            return input ? input.value : '';
            """
        )
        return str(value or "")

    def read_keyword_mode(self) -> str:
        value = self.page.run_js(
            """
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0 && rect.height > 0
                && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\s+/g, ' ');
            const select = Array.from(document.querySelectorAll('.mui-select'))
              .filter(visible)
              .find(ele => clean(ele) === '所有' || clean(ele) === '任一');
            return select ? clean(select) : '所有';
            """
        )
        return self.normalize_keyword_mode(str(value or "所有"))

    def mark_keyword_input(self) -> dict:
        return self.mark_first_element(
            """
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0 && rect.height > 0
                && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const input = Array.from(document.querySelectorAll('input[placeholder]'))
              .filter(visible)
              .find(ele => ele.placeholder.startsWith('搜索提示：'));
            if (!input) return {ok: false, reason: 'keyword input not found'};
            return {ok: true, text: input.placeholder, element: input};
            """
        )

    def select_keyword_mode(self, mode: str) -> None:
        current = self.page.run_js(
            """
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0 && rect.height > 0
                && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\\s+/g, ' ');
            const select = Array.from(document.querySelectorAll('.mui-select'))
              .filter(visible)
              .find(ele => clean(ele) === '所有' || clean(ele) === '任一');
            return select ? clean(select) : '';
            """
        )
        if current == mode:
            return
        result = self.mark_first_element(
            """
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0 && rect.height > 0
                && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\\s+/g, ' ');
            const select = Array.from(document.querySelectorAll('.mui-select'))
              .filter(visible)
              .find(ele => clean(ele) === '所有' || clean(ele) === '任一');
            if (!select) return {ok: false, reason: 'keyword mode select not found'};
            return {ok: true, text: clean(select), element: select};
            """
        )
        self.click_marked_element(result, "打开脉脉关键词模式")
        self.click_visible_dropdown_option(mode)

    def select_gender(self, gender: str) -> None:
        self.emit("maimai_filter", f"正在选择性别：{gender}")
        self.open_filter("性别")
        self.click_list_option(gender, "性别")
        if self.normalize_text(gender) != "不限":
            self.wait_for_summary("性别", [gender])
        self.close_open_popup()

    def select_age_range(self, age_min: str, age_max: str) -> None:
        self.emit("maimai_filter", f"正在选择年龄：{age_min or '不限'} - {age_max or '不限'}")
        self.open_filter("年龄")
        if age_min:
            self.select_age_dropdown("最低年龄", age_min)
        if age_max:
            self.select_age_dropdown("最高年龄", age_max, start_age=age_min)
        self.click_age_confirm()
        tokens = []
        if age_min:
            tokens.append(age_min)
        if age_max:
            tokens.append(age_max)
        self.wait_for_summary("年龄", tokens or ["年龄"])

    def wait_for_filter_bar(self, timeout: float = 12.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            found = self.page.run_js(
                """
                const bar = document.querySelector('.select-container');
                const text = bar ? (bar.innerText || '') : '';
                return !!bar && text.includes('城市地区') && text.includes('学历要求')
                  && text.includes('工作年限') && text.includes('就职公司');
                """
            )
            if found:
                return
            self.check_stopped()
            time.sleep(0.3)
        raise RuntimeError("脉脉筛选栏未加载完成。")

    def open_filter(self, label: str) -> None:
        last_error = None
        for _ in range(2):
            try:
                self.close_open_popup()
                result = self.find_filter_click_point(label)
                self.click_point(result, f"打开脉脉筛选：{label}")
                self.wait_for_popup(label)
                return
            except RuntimeError as exc:
                last_error = exc
                self.close_open_popup()
                time.sleep(0.5)
        raise RuntimeError(str(last_error or f"脉脉筛选弹层未打开：{label}"))

    def find_filter_click_point(self, label: str) -> dict:
        return self.mark_first_element(
            """
            const label = arguments[1];
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0 && rect.height > 0
                && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\\s+/g, ' ');
            const titleMatches = text => text === label || text.startsWith(label + '：') || text.startsWith(label + ':');
            const bars = Array.from(document.querySelectorAll('.select-container'));
            const candidates = bars.flatMap(bar => Array.from(bar.children))
              .filter(ele => visible(ele))
              .map(ele => {
                const title = ele.querySelector('.search-item-text') || ele;
                const rect = ele.getBoundingClientRect();
                return {ele, title, text: clean(title), rect};
              })
              .filter(item => titleMatches(item.text))
              .sort((a, b) => (a.rect.top - b.rect.top) || (a.rect.left - b.rect.left));
            const target = candidates[0];
            if (!target) return {ok: false, reason: 'filter not found', label};
            return {ok: true, text: target.text, element: target.ele};
            """,
            label,
        )

    def wait_for_popup(self, label: str, timeout: float = 5.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            visible = self.page.run_js(
                """
                const label = arguments[0];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const bodyText = document.body ? (document.body.innerText || '') : '';
                const inputPlaceholders = Array.from(document.querySelectorAll('input[placeholder]'))
                  .filter(visible)
                  .map(input => input.placeholder);
                if (label === '城市地区') return inputPlaceholders.includes('请输入城市地区');
                if (label === '就职公司') return inputPlaceholders.includes('请输入就职公司');
                if (label === '学历要求') return bodyText.includes('专科及以上') && bodyText.includes('博士');
                if (label === '工作年限') return bodyText.includes('1-3年') && bodyText.includes('10年以上');
                if (label === '性别') return bodyText.includes('男') && bodyText.includes('女');
                if (label === '年龄') return bodyText.includes('最低年龄') && bodyText.includes('最高年龄') && bodyText.includes('确定');
                return false;
                """,
                label,
            )
            if visible:
                return
            self.check_stopped()
            time.sleep(0.2)
        raise RuntimeError(f"脉脉筛选弹层未打开：{label}")

    def clear_filter(self, label: str) -> None:
        for _ in range(3):
            result = self.mark_first_element(
                """
                const label = arguments[1];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\\s+/g, ' ');
                const titleMatches = text => text.startsWith(label + '：') || text.startsWith(label + ':');
                const bars = Array.from(document.querySelectorAll('.select-container'));
                const containers = bars.flatMap(bar => Array.from(bar.children))
                  .filter(ele => visible(ele))
                  .map(ele => ({ele, title: ele.querySelector('.search-item-text') || ele}))
                  .filter(item => titleMatches(clean(item.title)));
                const container = containers[0] && containers[0].ele;
                if (!container) return {ok: false, reason: 'not selected'};
                const deleteIcon = Array.from(container.querySelectorAll('[class*=delete], [role=img], span'))
                  .filter(visible)
                  .find(ele => String(ele.className || '').includes('delete'));
                if (!deleteIcon) return {ok: false, reason: 'delete icon not found'};
                return {ok: true, element: deleteIcon};
                """,
                label,
            )
            if not result or not result.get("ok"):
                return
            self.click_marked_element(result, f"清理脉脉筛选：{label}")
            time.sleep(0.4)

    def fill_popup_input(self, placeholder: str, value: str) -> None:
        result = self.mark_first_element(
            """
            const placeholder = arguments[1];
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0 && rect.height > 0
                && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const input = Array.from(document.querySelectorAll('.wrapper___3L7wg input[placeholder], [class*=wrapper] input[placeholder]'))
              .filter(visible)
              .find(ele => ele.placeholder === placeholder);
            if (!input) return {ok: false, reason: 'popup input not found', placeholder};
            return {ok: true, text: placeholder, element: input};
            """,
            placeholder,
        )
        if not result.get("ok"):
            raise RuntimeError(f"脉脉输入框未找到：{placeholder}，{result}")
        input_ele = self.find_marked_element(result["token"])
        input_ele.click()
        input_ele.clear()
        input_ele.input(value)
        deadline = time.time() + 6
        while time.time() < deadline:
            current = self.page.run_js(
                """
                const placeholder = arguments[0];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const input = Array.from(document.querySelectorAll('.wrapper___3L7wg input[placeholder], [class*=wrapper] input[placeholder]'))
                  .filter(visible)
                  .find(ele => ele.placeholder === placeholder);
                return input ? input.value : null;
                """,
                placeholder,
            )
            if current == value:
                time.sleep(0.8)
                return
            self.check_stopped()
            time.sleep(0.2)
        raise RuntimeError(f"脉脉输入框填入失败：{placeholder} -> {value}")

    def click_search_result(self, value: str, label: str) -> None:
        deadline = time.time() + 8
        last_result = None
        while time.time() < deadline:
            last_result = self.mark_first_element(
                """
                const value = arguments[1];
                const label = arguments[2];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\\s+/g, ' ');
                const titleMatches = text => text === label || text.startsWith(label + '：') || text.startsWith(label + ':');
                const containers = Array.from(document.querySelectorAll('.select-container > [class*=container]'))
                  .filter(visible)
                  .filter(ele => {
                    const title = ele.querySelector('.search-item-text') || ele.querySelector('[class*=title]') || ele;
                    return titleMatches(clean(title));
                  });
                const container = containers.find(ele => Array.from(ele.querySelectorAll('.wrapper___3L7wg, [class*=wrapper]')).some(visible));
                if (!container) return {ok: false, reason: 'open filter container not found', label};
                const popup = Array.from(container.querySelectorAll('.wrapper___3L7wg, [class*=wrapper]')).find(visible);
                if (!popup) return {ok: false, reason: 'open filter popup not found', label};
                const score = item => {
                  const text = item.text;
                  if (text === value) return 0;
                  if (label === '城市地区' && text.endsWith('/ ' + value)) return 1;
                  if (label === '城市地区' && text.endsWith(' / ' + value)) return 1;
                  if (text.includes(value)) return 2;
                  return 99;
                };
                const items = Array.from(popup.querySelectorAll('[class*=list-item], li'))
                  .filter(visible)
                  .map(ele => {
                    const content = ele.querySelector('[class*=content]') || ele;
                    return {ele, text: clean(content), cls: String(ele.className || ''), rect: ele.getBoundingClientRect()};
                  })
                  .filter(item => item.text && item.text.includes(value) && item.rect.top > 0 && item.rect.top < window.innerHeight)
                  .filter(item => !item.cls.includes('tag') && !item.cls.includes('tags-container'))
                  .filter(item => !item.text.includes('搜索提示') && !item.text.includes('全选'))
                  .sort((a, b) => score(a) - score(b) || (a.text.length - b.text.length) || (a.rect.top - b.rect.top));
                const target = items[0];
                if (!target || score(target) >= 99) {
                  return {ok: false, reason: 'search result not found', options: items.slice(0, 8).map(item => item.text)};
                }
                return {ok: true, text: target.text, element: target.ele};
                """,
                value,
                label,
            )
            if last_result and last_result.get("ok"):
                self.click_marked_element(last_result, f"选择脉脉{label}：{value}")
                return
            self.check_stopped()
            time.sleep(0.3)
        raise RuntimeError(f"脉脉{label}搜索结果未找到：{value}，{last_result}")

    def click_city_search_result(self, city: str) -> None:
        deadline = time.time() + 8
        last_result = None
        while time.time() < deadline:
            last_result = self.mark_first_element(
                """
                const city = arguments[1];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\\s+/g, ' ');
                const wrapper = Array.from(document.querySelectorAll('.wrapper___3L7wg'))
                  .filter(visible)
                  .find(ele => Array.from(ele.querySelectorAll('input[placeholder]'))
                    .some(input => input.placeholder === '请输入城市地区'));
                if (!wrapper) return {ok: false, reason: 'city popup not found'};
                const items = Array.from(wrapper.querySelectorAll('.cascade___29JZU li.option___nxmry'))
                  .filter(visible)
                  .map(ele => ({ele, text: clean(ele), rect: ele.getBoundingClientRect()}))
                  .filter(item => item.text === city || item.text.endsWith('/ ' + city))
                  .sort((a, b) => {
                    const score = item => item.text === city ? 0 : (item.text.startsWith('历史/热门 / ') ? 1 : 2);
                    return score(a) - score(b) || a.rect.top - b.rect.top;
                  });
                const target = items[0];
                if (!target) {
                  return {ok: false, reason: 'city option not found', options: Array.from(wrapper.querySelectorAll('.cascade___29JZU li.option___nxmry')).filter(visible).map(clean)};
                }
                return {ok: true, text: target.text, element: target.ele};
                """,
                city,
            )
            if last_result and last_result.get("ok"):
                self.click_marked_element(last_result, f"选择脉脉城市地区：{city}")
                return
            self.check_stopped()
            time.sleep(0.25)
        raise RuntimeError(f"脉脉城市地区搜索结果未找到：{city}，{last_result}")

    def click_list_option(self, option_text: str, label: str) -> None:
        deadline = time.time() + 8
        last_result = None
        while time.time() < deadline:
            last_result = self.mark_first_element(
                """
                const optionText = arguments[1];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\\s+/g, ' ');
                const items = Array.from(document.querySelectorAll('[class*=list-item], [class*=content], li, div, span'))
                  .filter(visible)
                  .map(ele => ({ele, text: clean(ele), cls: String(ele.className || ''), rect: ele.getBoundingClientRect()}))
                  .filter(item => item.text === optionText && item.rect.top > 280 && item.rect.top < window.innerHeight)
                  .filter(item => !item.cls.includes('select-container') && !item.text.includes('搜索提示'))
                  .sort((a, b) => {
                    const itemScore = item => item.cls.includes('list-item') ? 0 : (item.cls.includes('content') ? 1 : 2);
                    return itemScore(a) - itemScore(b) || (a.rect.top - b.rect.top) || (a.rect.left - b.rect.left);
                  });
                const target = items[0];
                if (!target) return {ok: false, reason: 'option not found', optionText};
                return {ok: true, text: target.text, element: target.ele};
                """,
                option_text,
            )
            if last_result and last_result.get("ok"):
                self.click_marked_element(last_result, f"选择脉脉{label}：{option_text}")
                return
            self.check_stopped()
            time.sleep(0.2)
        raise RuntimeError(f"脉脉{label}选项未找到：{option_text}，{last_result}")

    def click_sub_option(self, option_text: str, required: bool, context: str = "学历子选项") -> None:
        deadline = time.time() + 3
        last_result = None
        while time.time() < deadline:
            last_result = self.mark_first_element(
                """
                const optionText = arguments[1];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\\s+/g, ' ');
                const items = Array.from(document.querySelectorAll('[class*=sub-select], .mui-popover div, .mui-popover span'))
                  .filter(visible)
                  .map(ele => ({ele, text: clean(ele), cls: String(ele.className || ''), rect: ele.getBoundingClientRect()}))
                  .filter(item => item.text === optionText && item.rect.top > 280)
                  .sort((a, b) => {
                    const itemScore = item => item.cls.includes('sub-select-item') ? 0 : 1;
                    return itemScore(a) - itemScore(b) || (a.rect.top - b.rect.top);
                  });
                const target = items[0];
                if (!target) return {ok: false, reason: 'sub option not found', optionText};
                return {ok: true, text: target.text, element: target.ele};
                """,
                option_text,
            )
            if last_result and last_result.get("ok"):
                self.click_marked_element(last_result, f"选择脉脉{context}：{option_text}")
                return
            self.check_stopped()
            time.sleep(0.2)
        if required:
            raise RuntimeError(f"脉脉{context}未找到：{option_text}，{last_result}")

    def select_age_dropdown(self, label: str, age: str, start_age: str = "") -> None:
        self.validate_age_value(age, start_age=start_age)
        self.mark_age_select(label)
        ele = self.find_marked_element(self.last_mark_token)
        ele.click()
        self.wait_for_age_dropdown()
        try:
            self.click_age_dropdown_option(age, start_age=start_age)
        except RuntimeError:
            selected_age = self.current_age_dropdown_selected_age()
            self.select_age_by_keyboard(age, start_age=start_age, selected_age=selected_age)
            if not self.wait_for_age_popup_select_value(label, age, timeout=1.5):
                raise RuntimeError(f"脉脉年龄选项未选中：{label} -> {age}")

    def wait_for_age_dropdown(self, timeout: float = 1.2) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            visible = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden';
                };
                return Array.from(document.querySelectorAll('.mui-select-dropdown'))
                  .some(visible);
                """
            )
            if visible:
                return
            self.check_stopped()
            time.sleep(0.05)
        raise RuntimeError("脉脉年龄下拉未打开。")

    def click_age_confirm(self) -> None:
        result = self.mark_first_element(
            """
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0 && rect.height > 0
                && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\\s+/g, ' ');
            const button = Array.from(document.querySelectorAll('.select-container [class*=mui-btn], .select-container div'))
              .filter(visible)
              .find(ele => clean(ele) === '确定' && String(ele.className || '').includes('mui-btn'));
            if (!button) return {ok: false, reason: 'age confirm not found'};
            return {ok: true, text: clean(button), element: button};
            """
        )
        self.click_marked_element(result, "确认脉脉年龄")

    def mark_age_select(self, label: str) -> None:
        result = self.mark_first_element(
            """
            const label = arguments[1];
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0 && rect.height > 0
                && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\\s+/g, ' ');
            const tip = Array.from(document.querySelectorAll('.select-container *'))
              .filter(visible)
              .find(ele => clean(ele) === label);
            const wrapper = tip && tip.closest('[class*=select-wrapper]');
            const select = wrapper && wrapper.querySelector('.mui-select');
            if (!select) return {ok: false, reason: 'age select not found', label};
            return {ok: true, text: clean(select), element: select};
            """,
            label,
        )
        if not result.get("ok"):
            raise RuntimeError(f"脉脉年龄下拉未找到：{label}，{result}")

    def click_visible_dropdown_option(self, option_text: str) -> None:
        deadline = time.time() + 8
        last_result = None
        while time.time() < deadline:
            last_result = self.mark_first_element(
                """
                const optionText = arguments[1];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\\s+/g, ' ');
                const dropdown = Array.from(document.querySelectorAll('.mui-select-dropdown'))
                  .filter(visible)
                  .sort((a, b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top)[0];
                if (!dropdown) return {ok: false, reason: 'dropdown not found'};
                const option = Array.from(dropdown.querySelectorAll('.mui-select-item-option-content'))
                  .filter(visible)
                  .find(ele => clean(ele) === optionText);
                if (option) return {ok: true, text: clean(option), element: option};
                const scroller = Array.from(dropdown.querySelectorAll('*'))
                  .find(ele => ele.scrollHeight > ele.clientHeight + 5) || dropdown;
                const before = scroller.scrollTop;
                scroller.scrollTop = before + Math.max(96, scroller.clientHeight || 120);
                scroller.dispatchEvent(new Event('scroll', {bubbles: true}));
                return {ok: false, reason: 'option not visible', optionText, before, after: scroller.scrollTop};
                """,
                option_text,
            )
            if last_result and last_result.get("ok"):
                self.click_marked_element(last_result, f"选择脉脉年龄：{option_text}")
                return
            self.check_stopped()
            time.sleep(0.25)
        raise RuntimeError(f"脉脉年龄选项未找到：{option_text}，{last_result}")

    def click_age_dropdown_option(
        self,
        age: str,
        start_age: str = "",
        timeout: float = 2.0,
    ) -> None:
        deadline = time.time() + timeout
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const age = String(arguments[0] || '');
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\\s+/g, ' ');
                const digits = text => String(text || '').replace(/\\D/g, '');
                const dropdown = Array.from(document.querySelectorAll('.mui-select-dropdown'))
                  .filter(visible)
                  .sort((a, b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top)[0];
                if (!dropdown) return {ok: false, reason: 'age dropdown not found'};
                const scroller = dropdown.querySelector('.rc-virtual-list-holder')
                  || Array.from(dropdown.querySelectorAll('[class*=virtual-list-holder], *'))
                    .find(ele => ele.scrollHeight > ele.clientHeight + 5)
                  || dropdown;
                const before = scroller.scrollTop;
                const startAge = Number(arguments[1]);
                const targetTop = startAge
                  ? Math.max(0, (Number(age) - startAge + 1) * 32)
                  : Math.max(0, (Number(age) - 15) * 32);
                const maxTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
                const nextTop = Math.min(targetTop, maxTop);
                if (Math.abs(before - nextTop) > 1) {
                  scroller.scrollTop = nextTop;
                  scroller.dispatchEvent(new Event('scroll', {bubbles: true}));
                }
                const options = Array.from(dropdown.querySelectorAll('.mui-select-item-option-content'))
                  .filter(visible)
                  .map(ele => ({ele, text: clean(ele), value: digits(clean(ele))}));
                const option = options.find(item => item.value === age);
                if (option) {
                  const target = option.ele.closest('.mui-select-item-option') || option.ele;
                  target.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true, view: window}));
                  target.click();
                  target.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true, view: window}));
                  return {ok: true, text: option.text};
                }
                return {
                  ok: false,
                  reason: 'age option not visible',
                  age,
                  visibleOptions: options.map(item => item.text),
                  before,
                  after: scroller.scrollTop,
                  targetTop: nextTop
                };
                """,
                age,
                start_age,
            )
            if last_result and last_result.get("ok"):
                time.sleep(0.12)
                return
            self.check_stopped()
            time.sleep(0.08)
        raise RuntimeError(f"脉脉年龄选项未找到：{age}，{last_result}")

    def validate_age_value(self, age: str, start_age: str = "") -> None:
        try:
            age_number = int(age)
        except ValueError as exc:
            raise RuntimeError(f"脉脉年龄不是数字：{age}") from exc
        if age_number < 16:
            raise RuntimeError(f"脉脉年龄不能小于 16：{age}")
        try:
            start_number = int(start_age) if start_age else 15
        except ValueError:
            start_number = 15
        if age_number < start_number:
            raise RuntimeError(f"脉脉最高年龄不能小于最低年龄：{start_age} -> {age}")

    def current_age_dropdown_selected_age(self) -> str:
        value = self.page.run_js(
            """
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0 && rect.height > 0
                && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\\s+/g, ' ');
            const digits = text => String(text || '').replace(/\\D/g, '');
            const dropdown = Array.from(document.querySelectorAll('.mui-select-dropdown'))
              .filter(visible)
              .sort((a, b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top)[0];
            if (!dropdown) return '';
            const selected = Array.from(dropdown.querySelectorAll('.mui-select-item-option-selected .mui-select-item-option-content, [aria-selected="true"] .mui-select-item-option-content'))
              .filter(visible)
              .map(clean)
              .find(Boolean);
            return digits(selected || '');
            """
        )
        return str(value or "").strip()

    def wait_for_age_popup_select_value(self, label: str, age: str, timeout: float = 1.5) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = self.page.run_js(
                """
                const label = arguments[0];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\\s+/g, ' ');
                const tip = Array.from(document.querySelectorAll('.select-container *'))
                  .filter(visible)
                  .find(ele => clean(ele) === label);
                const wrapper = tip && tip.closest('[class*=select-wrapper]');
                const select = wrapper && wrapper.querySelector('.mui-select');
                return select ? clean(select) : '';
                """,
                label,
            )
            text = str(value or "")
            digits = "".join(ch for ch in text if ch.isdigit())
            if text == str(age) or f"{age}岁" in text or digits == str(age) or digits.startswith(str(age)):
                return True
            self.check_stopped()
            time.sleep(0.15)
        return False

    def select_age_by_keyboard(self, age: str, start_age: str = "", selected_age: str = "") -> None:
        age_number = int(age)
        try:
            start_number = int(start_age) if start_age else 15
        except ValueError:
            start_number = 15
        selected_number = int(selected_age) if str(selected_age or "").isdigit() else None
        if selected_number is not None:
            delta = age_number - selected_number
        else:
            delta = age_number - start_number + 1 if start_number >= 16 else age_number - 15
        key = Keys.DOWN if delta >= 0 else Keys.UP
        for _ in range(abs(delta)):
            self.page.actions.key_down(key).key_up(key)
            time.sleep(0.02)
        self.page.actions.key_down(Keys.ENTER).key_up(Keys.ENTER)
        time.sleep(0.35)

    def wait_for_summary(self, label: str, tokens: list[str], timeout: float = 6.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            summary = self.current_filter_summary().get(label, "")
            normalized_summary = self.normalize_text(summary)
            if all(self.normalize_text(token) in normalized_summary for token in tokens):
                return
            self.check_stopped()
            time.sleep(0.3)
        raise RuntimeError(f"脉脉筛选未回显：{label} -> {' / '.join(tokens)}")

    def wait_for_company_tags(self, companies: list[str], timeout: float = 6.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            tags_text = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\\s+/g, ' ');
                const wrapper = Array.from(document.querySelectorAll('.wrapper___3L7wg, [class*=wrapper]'))
                  .filter(visible)
                  .find(ele => Array.from(ele.querySelectorAll('input[placeholder]')).some(input => input.placeholder === '请输入就职公司'));
                const tags = wrapper && wrapper.querySelector('[class*=tags-container]');
                return tags ? clean(tags) : '';
                """
            ) or ""
            normalized_tags = self.normalize_text(tags_text)
            if all(self.normalize_text(company) in normalized_tags for company in companies):
                return
            self.check_stopped()
            time.sleep(0.3)
        raise RuntimeError(f"脉脉就职公司未选中：{' / '.join(companies)}")

    def wait_for_popup_closed(self, label: str, timeout: float = 3.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            open_state = self.page.run_js(
                """
                const label = arguments[0];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const inputPlaceholders = Array.from(document.querySelectorAll('input[placeholder]'))
                  .filter(visible)
                  .map(input => input.placeholder);
                if (label === '城市地区') return inputPlaceholders.includes('请输入城市地区');
                if (label === '就职公司') return inputPlaceholders.includes('请输入就职公司');
                return Array.from(document.querySelectorAll('[class*=list-item], .mui-popover, [class*=customize-item]'))
                  .some(ele => visible(ele) && (
                    (label === '学历要求' && (ele.innerText || '').includes('专科及以上'))
                    || (label === '工作年限' && (ele.innerText || '').includes('1-3年'))
                  ));
                """,
                label,
            )
            if not open_state:
                return
            self.check_stopped()
            time.sleep(0.2)
        raise RuntimeError(f"脉脉筛选弹层未关闭：{label}")

    def current_filter_summary(self) -> dict[str, str]:
        rows = self.page.run_js(
            """
            const labels = String(arguments[0] || '').split('|').filter(Boolean);
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0 && rect.height > 0
                && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\\s+/g, ' ');
            const titleMatches = (text, label) => text === label || text.startsWith(label + '：') || text.startsWith(label + ':');
            const result = {};
            const bars = Array.from(document.querySelectorAll('.select-container'));
            const containers = bars.flatMap(bar => Array.from(bar.children)).filter(visible);
            for (const label of labels) {
              const item = containers
                .map(ele => ele.querySelector('.search-item-text') || ele)
                .find(title => titleMatches(clean(title), label));
              result[label] = item ? clean(item) : '';
            }
            return result;
            """,
            "|".join(FILTER_LABELS),
        )
        return rows or {}

    def close_open_popup(self) -> None:
        deadline = time.time() + 3.0
        while time.time() < deadline:
            result = self.mark_first_element(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const containers = Array.from(document.querySelectorAll('.select-container > [class*=container]'))
                  .filter(visible)
                  .map(ele => {
                    const wrapper = Array.from(ele.querySelectorAll('.wrapper___3L7wg, .mui-popover, [class*=popup], [role=dialog]')).find(visible);
                    const input = Array.from(ele.querySelectorAll('input[placeholder]')).find(input => {
                      if (!visible(input)) return false;
                      const placeholder = String(input.placeholder || '');
                      return placeholder.startsWith('请输入城市地区') || placeholder.startsWith('请输入就职公司');
                    });
                    return {ele, wrapper, input, rect: ele.getBoundingClientRect()};
                  })
                  .filter(item => item.wrapper || item.input)
                  .sort((a, b) => (a.rect.top - b.rect.top) || (a.rect.left - b.rect.left));
                const target = containers[0];
                if (!target) return {ok: false, reason: 'no open popup'};
                if (target.input) target.input.blur();
                const header = target.ele.querySelector('[class*=header]') || target.ele;
                return {ok: true, text: (header.innerText || header.textContent || '').trim(), element: header};
                """
            )
            if not result.get("ok"):
                return
            self.click_marked_element(result, "关闭脉脉筛选弹层")
            closed = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const containers = Array.from(document.querySelectorAll('.select-container > [class*=container]'))
                  .filter(visible)
                  .some(ele => {
                    const hasPopup = Array.from(ele.querySelectorAll('.wrapper___3L7wg, .mui-popover, [class*=popup], [role=dialog]')).some(visible);
                    const hasSearchInput = Array.from(ele.querySelectorAll('input[placeholder]')).some(input => {
                      if (!visible(input)) return false;
                      const placeholder = String(input.placeholder || '');
                      return placeholder.startsWith('请输入城市地区') || placeholder.startsWith('请输入就职公司');
                    });
                    return hasPopup || hasSearchInput;
                  });
                return !containers;
                """
            )
            if closed:
                return
            try:
                self.page.actions.key_down(Keys.ESCAPE).key_up(Keys.ESCAPE)
            except Exception:
                pass
            time.sleep(0.2)

    def click_point(self, result: dict | None, context: str) -> None:
        self.click_marked_element(result, context)

    def click_marked_element(self, result: dict | None, context: str) -> None:
        if not result or not result.get("ok"):
            raise RuntimeError(f"{context}失败：{result}")
        token = result.get("token")
        if not token:
            raise RuntimeError(f"{context}失败：未标记真实元素，{result}")
        ele = self.find_marked_element(token)
        ele.click()
        time.sleep(0.35)

    def find_marked_element(self, token: str):
        return self.page.ele(f'xpath://*[@data-codex-click-token="{token}"]', timeout=3)

    def mark_first_element(self, script: str, *args) -> dict:
        self.last_mark_token = f"maimai_{uuid.uuid4().hex}"
        result = self.page.run_js(
            f"""
            const token = arguments[0];
            const inner = () => {{
            {script}
            }};
            const result = inner();
            if (result && result.ok && result.element) {{
              result.element.setAttribute('data-codex-click-token', token);
              delete result.element;
              result.token = token;
            }}
            return result;
            """,
            self.last_mark_token,
            *args,
        )
        return result or {"ok": False, "reason": "mark script returned empty"}

    @staticmethod
    def clean_config_value(value) -> str:
        return str(value or "").strip()

    @staticmethod
    def clean_age_value(value) -> str:
        text = str(value or "").replace("岁", "").strip()
        return text if text.isdigit() else ""

    @staticmethod
    def clean_page_limit(value) -> int:
        try:
            return max(int(value or 1), 1)
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def clean_optional_positive_int(value) -> int | None:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    @staticmethod
    def split_multi_values(value: str) -> list[str]:
        raw = str(value or "").replace("，", ",").replace("、", ",").replace("；", ",").replace(";", ",")
        result: list[str] = []
        for item in raw.split(","):
            text = item.strip()
            if text and text not in result:
                result.append(text)
        return result

    @staticmethod
    def normalize_keyword_mode(value: str) -> str:
        text = str(value or "").strip()
        if text in {"任一", "满足任一关键词", "任一关键词", "OR", "or"}:
            return "任一"
        return "所有"

    @staticmethod
    def normalize_text(value: str) -> str:
        return str(value or "").replace("（", "(").replace("）", ")").replace(" ", "").strip()

