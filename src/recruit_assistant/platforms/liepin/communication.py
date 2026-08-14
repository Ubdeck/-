from __future__ import annotations

import json
import time
from datetime import datetime

from .constants import CHAT_URL


class CommunicationMixin:
    def request_contacts_after_batch(
        self,
        targets: list[dict],
        results: list[dict],
        started_at: datetime,
        request_resume: bool = True,
        request_phone: bool = False,
    ) -> None:
        actions = []
        if request_phone:
            actions.append("phone")
        if request_resume:
            actions.append("resume")
        if not actions:
            return

        start_minute = started_at.hour * 60 + started_at.minute
        self.progress.emit(
            "resume_request_start",
            f"开始进入消息页，处理 {started_at.strftime('%H:%M')} 之后的新会话：{len(targets)} 个",
        )
        self.page.get(CHAT_URL)
        self.wait_for_chat_page()
        self.reset_chat_list_scroll()

        cards = self.collect_recent_chat_cards(start_minute, max_count=len(targets))
        processed_count = 0
        for opened in cards[: len(targets)]:
            clicked = self.open_chat_card_by_signature(opened.get("signature", ""))
            if not clicked.get("ok"):
                self.progress.emit(
                    "resume_request_done",
                    f"消息卡片打开失败，跳过：{opened.get('time', '')} {opened.get('title', '')}",
                    {"card": opened, "error": clicked},
                )
                continue

            target = targets[processed_count]
            index = target.get("index")
            self.progress.emit(
                "resume_request",
                f"正在处理第 {index} 个已沟通会话：{opened.get('time', '')} {opened.get('title', '')}",
                {"index": index, "card": opened},
            )
            time.sleep(0.8)

            action_results: dict[str, dict] = {}
            for action in actions:
                try:
                    action_results[action] = self.request_chat_action_in_current_chat(action)
                except Exception as exc:
                    action_results[action] = {"status": "failed", "message": str(exc)}

            for item in results:
                if item.get("index") == index:
                    if request_resume:
                        resume = action_results.get("resume") or {}
                        item["resume_request_status"] = resume.get("status", "unknown")
                        item["resume_request_note"] = resume.get("message", "")
                    if request_phone:
                        phone = action_results.get("phone") or {}
                        item["phone_request_status"] = phone.get("status", "unknown")
                        item["phone_request_note"] = phone.get("message", "")
                    break

            message_parts = []
            if request_phone:
                phone = action_results.get("phone") or {}
                message_parts.append(f"电话：{self.contact_status_text(phone.get('status', 'unknown'))}")
            if request_resume:
                resume = action_results.get("resume") or {}
                message_parts.append(f"简历：{self.contact_status_text(resume.get('status', 'unknown'))}")
            self.progress.emit(
                "resume_request_done",
                f"第 {index} 个会话处理结果：{'；'.join(message_parts)}",
                {"index": index, "actions": action_results},
            )
            self.save_batch_summary(results)
            processed_count += 1

        if processed_count < len(targets):
            self.progress.emit(
                "resume_request_done",
                f"消息页只处理到 {processed_count}/{len(targets)} 个本轮开始后的会话，其余未处理",
                {"processed": processed_count, "expected": len(targets), "cards": cards},
            )

    def request_contacts_from_continue_chat(
        self,
        index: int,
        request_resume: bool = True,
        request_phone: bool = False,
    ) -> dict[str, dict]:
        actions = []
        if request_phone:
            actions.append("phone")
        if request_resume:
            actions.append("resume")
        if not actions:
            return {}

        self.progress.emit("resume_request_start", f"第 {index} 个沟通成功，打开继续沟通小窗索要联系方式")
        action_results: dict[str, dict] = {}
        try:
            opened = self.open_continue_chat_panel()
            if not opened.get("ok"):
                raise RuntimeError(str(opened))
            time.sleep(0.8)
            for action in actions:
                try:
                    action_results[action] = self.request_chat_action_in_current_chat(action)
                except Exception as exc:
                    action_results[action] = {"status": "failed", "message": str(exc)}
                if (action_results.get(action) or {}).get("status") == "failed":
                    self.dismiss_chat_confirm_overlay()
            return action_results
        except Exception as exc:
            failed = {"status": "failed", "message": str(exc)}
            for action in actions:
                action_results.setdefault(action, failed)
            return action_results
        finally:
            closed = self.close_continue_chat_panel()
            message_parts = []
            if request_phone:
                phone = action_results.get("phone") or {}
                message_parts.append(f"电话：{self.contact_status_text(phone.get('status', 'unknown'))}")
            if request_resume:
                resume = action_results.get("resume") or {}
                message_parts.append(f"简历：{self.contact_status_text(resume.get('status', 'unknown'))}")
            close_note = "" if closed.get("ok") else f"；关闭小窗失败：{closed.get('reason', closed)}"
            self.progress.emit(
                "resume_request_done",
                f"第 {index} 个小窗处理结果：{'；'.join(message_parts)}{close_note}",
                {"index": index, "actions": action_results, "close": closed},
            )

    def dismiss_chat_confirm_overlay(self) -> None:
        try:
            self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                const textOf = ele => clean(ele.innerText || ele.textContent);
                const compact = value => clean(value).replace(/\\s+/g, '');
                const scopes = Array.from(document.querySelectorAll('.ant-im-modal, .ant-lpt-modal, [role=dialog], [class*=modal], .ant-im-popover, .ant-popover, [class*=popover], [class*=Popconfirm]'))
                  .filter(visible)
                  .filter(ele => /确定|确认|索要|获取/.test(textOf(ele)));
                for (const scope of scopes) {
                  const cancel = Array.from(scope.querySelectorAll('button, [role=button], a, span, div'))
                    .filter(visible)
                    .find(ele => /^(取消|关闭|×|x|X)$/.test(compact(textOf(ele))));
                  if (cancel) {
                    for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                      cancel.dispatchEvent(new MouseEvent(name, {bubbles: true, cancelable: true, composed: true, view: window}));
                    }
                    return {ok: true, clicked: textOf(cancel)};
                  }
                }
                document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', code: 'Escape', bubbles: true, cancelable: true}));
                document.dispatchEvent(new KeyboardEvent('keyup', {key: 'Escape', code: 'Escape', bubbles: true, cancelable: true}));
                return {ok: true, escaped: true};
                """
            )
            time.sleep(0.4)
        except Exception:
            pass

    def open_continue_chat_panel(self, timeout: int = 12) -> dict:
        deadline = time.time() + timeout
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden' && !ele.disabled;
                };
                const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                const textOf = ele => clean(ele.innerText || ele.textContent);
                const drawerLeft = () => {
                  const drawer = Array.from(document.querySelectorAll('.ant-im-drawer-content-wrapper, .ant-im-drawer'))
                    .filter(visible)
                    .map(ele => ele.getBoundingClientRect())
                    .filter(rect => rect.width > 0 && rect.left > window.innerWidth * 0.55)
                    .sort((a, b) => a.left - b.left)[0];
                  return drawer ? drawer.left : window.innerWidth + 1;
                };
                const currentResumeActionPanel = () => {
                  const rightLimit = drawerLeft();
                  const candidates = Array.from(document.querySelectorAll('.xpath-wrap-resume-download, [class*=operation]'))
                    .filter(visible)
                    .filter(ele => {
                      const rect = ele.getBoundingClientRect();
                      const text = textOf(ele);
                      return text.includes('觉得TA还不错')
                        && rect.left > window.innerWidth * 0.45
                        && rect.right < rightLimit - 8
                        && rect.width >= 180
                        && rect.width <= 420
                        && rect.height >= 80;
                    })
                    .sort((a, b) => {
                      const ac = String(a.className || '');
                      const bc = String(b.className || '');
                      const aDirect = ac.includes('xpath-wrap-resume-download') ? 0 : 1;
                      const bDirect = bc.includes('xpath-wrap-resume-download') ? 0 : 1;
                      if (aDirect !== bDirect) return aDirect - bDirect;
                      return (a.getBoundingClientRect().height * a.getBoundingClientRect().width)
                        - (b.getBoundingClientRect().height * b.getBoundingClientRect().width);
                    });
                  return candidates[0] || null;
                };
                const chatPanel = Array.from(document.querySelectorAll('.chatwin-action'))
                  .filter(visible)
                  .map(ele => {
                    const direct = ele.closest('.im-ui-basic-chat-modal, .ant-im-modal');
                    if (direct && visible(direct)) return direct;
                    let panel = ele;
                    for (let depth = 0; panel && depth < 8; depth += 1, panel = panel.parentElement) {
                      const text = textOf(panel);
                      const rect = panel.getBoundingClientRect();
                      if (text.includes('沟通职位') && rect.width >= 420 && rect.height >= 360) {
                        return panel;
                      }
                    }
                    return null;
                  })
                  .filter(Boolean)[0];
                if (chatPanel) return {ok: true, already_open: true};

                const clickableOf = ele => {
                  let node = ele;
                  for (let depth = 0; node && depth < 8; depth += 1, node = node.parentElement) {
                    const tag = node.tagName;
                    const role = node.getAttribute('role') || '';
                    const cls = String(node.className || '');
                    const style = getComputedStyle(node);
                    if (tag === 'BUTTON' || tag === 'A' || role === 'button' || cls.includes('btn') || cls.includes('Btn') || cls.includes('button') || cls.includes('Button') || style.cursor === 'pointer') {
                      return node;
                    }
                  }
                  return ele;
                };
                const panel = currentResumeActionPanel();
                if (!panel) return {ok: false, reason: '当前简历右侧操作卡片未找到'};
                const candidates = Array.from(panel.querySelectorAll('*'))
                  .filter(visible)
                  .map(ele => ({source: ele, target: clickableOf(ele), text: textOf(ele)}))
                  .filter(item => item.text === '继续沟通' || item.text.includes('继续沟通'))
                  .filter(item => visible(item.target) && panel.contains(item.target));
                candidates.sort((a, b) => {
                  const aExact = a.text === '继续沟通' ? 0 : 1;
                  const bExact = b.text === '继续沟通' ? 0 : 1;
                  if (aExact !== bExact) return aExact - bExact;
                  const aOpenIm = String(a.target.className || '').includes('xpath-open-im-btn') ? 0 : 1;
                  const bOpenIm = String(b.target.className || '').includes('xpath-open-im-btn') ? 0 : 1;
                  if (aOpenIm !== bOpenIm) return aOpenIm - bOpenIm;
                  const aButton = a.target.tagName === 'BUTTON' ? 0 : 1;
                  const bButton = b.target.tagName === 'BUTTON' ? 0 : 1;
                  if (aButton !== bButton) return aButton - bButton;
                  const ar = a.target.getBoundingClientRect();
                  const br = b.target.getBoundingClientRect();
                  return bRectLeft(br) - bRectLeft(ar);
                });
                function bRectLeft(rect) { return rect.left; }
                const button = candidates[0] && candidates[0].target;
                if (!button) return {ok: false, reason: '继续沟通按钮未找到'};
                button.scrollIntoView({block: 'center', inline: 'nearest'});
                for (const name of ['mouseover', 'pointerover', 'pointerenter', 'mouseenter', 'pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  button.dispatchEvent(new MouseEvent(name, {bubbles: true, cancelable: true, composed: true, view: window}));
                }
                return {ok: false, clicked: true, reason: '已点击继续沟通，等待小窗打开'};
                """
            )
            if last_result and last_result.get("ok"):
                return last_result
            time.sleep(0.4)
        return {"ok": False, "reason": str(last_result or "继续沟通小窗未打开")}

    def close_continue_chat_panel(self, timeout: int = 5) -> dict:
        deadline = time.time() + timeout
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                const textOf = ele => clean(ele.innerText || ele.textContent);
                const panels = Array.from(document.querySelectorAll('.chatwin-action'))
                  .filter(visible)
                  .map(ele => {
                    const direct = ele.closest('.im-ui-basic-chat-modal, .ant-im-modal');
                    if (direct && visible(direct)) return direct;
                    let panel = ele;
                    for (let depth = 0; panel && depth < 8; depth += 1, panel = panel.parentElement) {
                      const text = textOf(panel);
                      const rect = panel.getBoundingClientRect();
                      if (text.includes('沟通职位') && rect.width >= 420 && rect.height >= 360) {
                        return panel;
                      }
                    }
                    return null;
                  })
                  .filter(Boolean);
                const panel = panels[0];
                if (!panel) return {ok: true, already_closed: true};
                const panelRect = panel.getBoundingClientRect();
                const closeCandidates = Array.from(panel.querySelectorAll('button, [role=button], i, svg, span, div'))
                  .filter(visible)
                  .map(ele => ({ele, text: textOf(ele), rect: ele.getBoundingClientRect(), cls: String(ele.className || '')}))
                  .filter(item => {
                    const aria = item.ele.getAttribute('aria-label') || item.ele.getAttribute('title') || '';
                    const nearTopRight = item.rect.top <= panelRect.top + 90 && item.rect.left >= panelRect.right - 90;
                    return nearTopRight && (
                      item.text === '×'
                      || item.text === 'x'
                      || item.text === 'X'
                      || aria.includes('关闭')
                      || aria.toLowerCase().includes('close')
                      || item.cls.includes('close')
                      || item.cls.includes('Close')
                    );
                  })
                  .sort((a, b) => {
                    const ar = a.rect;
                    const br = b.rect;
                    const aScore = Math.abs(ar.right - panelRect.right) + Math.abs(ar.top - panelRect.top);
                    const bScore = Math.abs(br.right - panelRect.right) + Math.abs(br.top - panelRect.top);
                    return aScore - bScore;
                  });
                let close = closeCandidates[0] && closeCandidates[0].ele;
                if (!close) {
                  close = document.elementFromPoint(panelRect.right - 36, panelRect.top + 36);
                }
                if (!close) return {ok: false, reason: '关闭按钮未找到'};
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  close.dispatchEvent(new MouseEvent(name, {bubbles: true, cancelable: true, composed: true, view: window}));
                }
                return {ok: false, clicked: true, reason: '已点击关闭小窗'};
                """
            )
            if last_result and last_result.get("ok"):
                return last_result
            time.sleep(0.3)
        return {"ok": False, "reason": str(last_result or "关闭小窗失败")}

    def collect_recent_chat_cards(self, start_minute: int, max_count: int) -> list[dict]:
        return self.page.run_js(
            """
            const startMinute = Number(arguments[0] || 0);
            const maxCount = Number(arguments[1] || 1);
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
            const minuteOf = text => {
              const m = String(text || '').match(/(^|[^0-9])([01]?[0-9]|2[0-3]):([0-5][0-9])([^0-9]|$)/);
              if (!m) return null;
              return Number(m[2]) * 60 + Number(m[3]);
            };
            const ignoredTexts = ['求职者投递', '批量处理', '昨日您主动沟通', '全部职位', '消息筛选'];
            const badCardText = text => ignoredTexts.some(item => text.includes(item));
            const cards = [];
            const seen = new Set();
            const readVisibleCards = () => {
              for (const ele of Array.from(document.querySelectorAll('.im-ui-contact-list-item')).filter(visible)) {
                const text = clean(ele.innerText || ele.textContent);
                const minute = minuteOf(text);
                if (minute === null || minute < startMinute || badCardText(text)) continue;
                const time = (text.match(/([01]?[0-9]|2[0-3]):([0-5][0-9])/) || [''])[0];
                const title = clean(text.split(time)[0] || text).slice(0, 40);
                const signature = `${time}|${title}`;
                if (seen.has(signature)) continue;
                seen.add(signature);
                cards.push({ok: true, signature, time, title, text: text.slice(0, 160), minute});
              }
            };
            readVisibleCards();
            const scrollBox = Array.from(document.querySelectorAll('.im-ui-contacts-wrap, aside *, *'))
              .filter(visible)
              .filter(ele => {
                const rect = ele.getBoundingClientRect();
                const style = getComputedStyle(ele);
                return rect.left < Math.min(520, window.innerWidth * 0.45)
                  && rect.width >= 250
                  && rect.width <= 360
                  && rect.height > 240
                  && ele.scrollHeight > ele.clientHeight + 20
                  && /(auto|scroll)/.test(style.overflowY);
              })
              .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight))[0];
            let guard = 0;
            while (scrollBox && cards.length < maxCount && guard < 12) {
              const before = scrollBox.scrollTop;
              scrollBox.scrollTop += Math.max(160, scrollBox.clientHeight * 0.75);
              readVisibleCards();
              guard += 1;
              if (scrollBox.scrollTop === before || scrollBox.scrollTop + scrollBox.clientHeight >= scrollBox.scrollHeight - 8) break;
            }
            return cards.sort((a, b) => b.minute - a.minute);
            """,
            start_minute,
            max_count,
        ) or []

    def open_chat_card_by_signature(self, signature: str) -> dict:
        return self.page.run_js(
            """
            const signature = String(arguments[0] || '');
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
            const signatureOf = ele => {
              const text = clean(ele.innerText || ele.textContent);
              const time = (text.match(/([01]?[0-9]|2[0-3]):([0-5][0-9])/) || [''])[0];
              const title = clean(text.split(time)[0] || text).slice(0, 40);
              return `${time}|${title}`;
            };
            const findCard = () => Array.from(document.querySelectorAll('.im-ui-contact-list-item'))
              .filter(visible)
              .find(ele => signatureOf(ele) === signature);
            let card = findCard();
            if (!card) {
              const scrollBox = Array.from(document.querySelectorAll('.im-ui-contacts-wrap, aside *, *'))
                .filter(visible)
                .filter(ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.left < Math.min(520, window.innerWidth * 0.45)
                    && rect.width >= 250
                    && rect.width <= 360
                    && rect.height > 240
                    && ele.scrollHeight > ele.clientHeight + 20
                    && /(auto|scroll)/.test(style.overflowY);
                })
                .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight))[0];
              if (scrollBox) {
                scrollBox.scrollTop = 0;
                for (let i = 0; i < 18 && !card; i += 1) {
                  card = findCard();
                  if (card) break;
                  scrollBox.scrollTop += Math.max(160, scrollBox.clientHeight * 0.75);
                }
              }
            }
            if (!card) return {ok: false, reason: 'card not found', signature};
            card.scrollIntoView({block: 'center', inline: 'nearest'});
            for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
              card.dispatchEvent(new MouseEvent(name, {bubbles: true, cancelable: true, composed: true, view: window}));
            }
            return {ok: true, signature};
            """,
            signature,
        ) or {"ok": False, "reason": "open card script failed"}

    def open_next_recent_chat_card(self, start_minute: int, processed_signatures: set[str]) -> dict:
        processed_json = json.dumps(list(processed_signatures), ensure_ascii=False)
        return self.page.run_js(
            """
            const startMinute = Number(arguments[0] || 0);
            let processedItems = [];
            try {
              processedItems = JSON.parse(arguments[1] || '[]');
            } catch {
              processedItems = [];
            }
            const processed = new Set(processedItems);
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
            const minuteOf = text => {
              const m = String(text || '').match(/(^|[^0-9])([01]?[0-9]|2[0-3]):([0-5][0-9])([^0-9]|$)/);
              if (!m) return null;
              return Number(m[2]) * 60 + Number(m[3]);
            };
            const inLeftArea = ele => {
              const rect = ele.getBoundingClientRect();
              const cls = String(ele.className || '');
              if (cls.includes('im-ui-contact-list-item') || cls.includes('im-ui-contact-info')) {
                return rect.left >= 150 && rect.left < Math.min(520, window.innerWidth * 0.45)
                  && rect.width >= 220
                  && rect.width <= 340
                  && rect.height >= 55
                  && rect.height <= 95;
              }
              return rect.left < Math.min(620, window.innerWidth * 0.48)
                && rect.width >= 180
                && rect.width <= Math.max(680, window.innerWidth * 0.55)
                && rect.height >= 36
                && rect.height <= 180;
            };
            const ignoredTexts = ['求职者投递', '批量处理', '昨日您主动沟通', '全部职位', '消息筛选'];
            const badCardText = text => ignoredTexts.some(item => text.includes(item));
            const primaryCards = Array.from(document.querySelectorAll('.im-ui-contact-list-item'))
              .filter(visible)
              .map(ele => {
                const text = clean(ele.innerText || ele.textContent);
                const minute = minuteOf(text);
                const rect = ele.getBoundingClientRect();
                const time = (text.match(/([01]?[0-9]|2[0-3]):([0-5][0-9])/) || [''])[0];
                const title = clean(text.split(time)[0] || text).slice(0, 40);
                const signature = `${time}|${title}`;
                return {ele, text, minute, time, title, signature, top: rect.top};
              })
              .filter(item => item.minute !== null)
              .filter(item => item.minute >= startMinute)
              .filter(item => !badCardText(item.text))
              .filter(item => !processed.has(item.signature))
              .sort((a, b) => b.minute - a.minute || a.top - b.top);
            const primaryCard = primaryCards[0];
            if (primaryCard) {
              primaryCard.ele.scrollIntoView({block: 'center', inline: 'nearest'});
              for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                primaryCard.ele.dispatchEvent(new MouseEvent(name, {
                  bubbles: true,
                  cancelable: true,
                  composed: true,
                  view: window,
                }));
              }
              return {ok: true, signature: primaryCard.signature, time: primaryCard.time, title: primaryCard.title, text: primaryCard.text.slice(0, 160)};
            }
            if (document.querySelectorAll('.im-ui-contact-list-item').length > 0) {
              return {
                ok: false,
                reason: 'no recent chat card found',
                debug: {
                  startMinute,
                  processed: Array.from(processed),
                  total: document.querySelectorAll('.im-ui-contact-list-item').length,
                  samples: Array.from(document.querySelectorAll('.im-ui-contact-list-item')).slice(0, 8).map(ele => {
                    const text = clean(ele.innerText || ele.textContent);
                    return {text: text.slice(0, 120), minute: minuteOf(text), ignored: badCardText(text)};
                  }),
                },
              };
            }
            const resolveCard = ele => {
              const direct = ele.closest('.im-ui-contact-list-item, .im-ui-contact-info');
              if (direct) return direct;
              for (let node = ele; node && node !== document.body; node = node.parentElement) {
                const rect = node.getBoundingClientRect();
                const cls = String(node.className || '');
                if ((cls.includes('contact') || cls.includes('item') || cls.includes('card') || cls.includes('session') || cls.includes('conversation'))
                  && rect.left >= 150 && rect.left < Math.min(520, window.innerWidth * 0.45)
                  && rect.width >= 220 && rect.width <= 360 && rect.height >= 55 && rect.height <= 120) {
                  return node;
                }
              }
              return ele;
            };
            const primaryNodes = Array.from(document.querySelectorAll('.im-ui-contact-list-item'));
            const fallbackNodes = Array.from(document.querySelectorAll('.im-ui-contact-info, li, [class*=item], [class*=card], [class*=session], [class*=conversation], [class*=list] > div'));
            const rawNodes = primaryNodes.length ? primaryNodes : fallbackNodes;
            const uniqueNodes = Array.from(new Set(rawNodes.map(resolveCard)));
            const cardNodes = uniqueNodes
              .filter(visible)
              .filter(inLeftArea)
              .map(ele => {
                const text = clean(ele.innerText || ele.textContent);
                const minute = minuteOf(text);
                const rect = ele.getBoundingClientRect();
                const time = (text.match(/([01]?[0-9]|2[0-3]):([0-5][0-9])/) || [''])[0];
                const title = clean(text.split(time)[0] || text).slice(0, 40);
                const signature = `${time}|${title}`;
                return {ele, text, minute, time, title, signature, top: rect.top};
              })
              .filter(item => item.minute !== null)
              .filter(item => item.minute >= startMinute)
              .filter(item => !badCardText(item.text))
              .filter(item => !processed.has(item.signature))
              .sort((a, b) => b.minute - a.minute || a.top - b.top);
            const card = cardNodes[0];
            if (card) {
              card.ele.scrollIntoView({block: 'center', inline: 'nearest'});
              for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                card.ele.dispatchEvent(new MouseEvent(name, {
                  bubbles: true,
                  cancelable: true,
                  composed: true,
                  view: window,
                }));
              }
              return {ok: true, signature: card.signature, time: card.time, title: card.title, text: card.text.slice(0, 160)};
            }
            const scrollBox = Array.from(document.querySelectorAll('*'))
              .filter(visible)
              .filter(ele => {
                const rect = ele.getBoundingClientRect();
                const style = getComputedStyle(ele);
                return rect.left < Math.min(620, window.innerWidth * 0.48)
                  && rect.width >= 220
                  && rect.width <= 520
                  && rect.height > 240
                  && ele.scrollHeight > ele.clientHeight + 20
                  && /(auto|scroll)/.test(style.overflowY);
              })
              .sort((a, b) => {
                const ar = a.getBoundingClientRect();
                const br = b.getBoundingClientRect();
                return (br.height * br.width) - (ar.height * ar.width);
              })[0];
            if (scrollBox && scrollBox.scrollTop + scrollBox.clientHeight < scrollBox.scrollHeight - 8) {
              scrollBox.scrollTop += Math.max(180, scrollBox.clientHeight * 0.75);
              return {ok: false, scrolled: true};
            }
            return {ok: false, reason: 'no recent chat card found'};
            """,
            start_minute,
            processed_json,
        ) or {"ok": False, "reason": "open chat card script failed"}

    def reset_chat_list_scroll(self) -> None:
        self.page.run_js(
            """
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const boxes = Array.from(document.querySelectorAll('.im-ui-contacts-wrap, aside *, *'))
              .filter(visible)
              .filter(ele => {
                const rect = ele.getBoundingClientRect();
                const style = getComputedStyle(ele);
                return rect.left < Math.min(520, window.innerWidth * 0.45)
                  && rect.width >= 250
                  && rect.width <= 360
                  && rect.height > 240
                  && ele.scrollHeight > ele.clientHeight + 20
                  && /(auto|scroll)/.test(style.overflowY);
              })
              .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight));
            for (const box of boxes.slice(0, 3)) {
              box.scrollTop = 0;
            }
            return boxes.length;
            """
        )
        time.sleep(0.8)

    def request_chat_action_in_current_chat(self, action: str, timeout: int = 14) -> dict:
        action = "phone" if action == "phone" else "resume"
        deadline = time.time() + timeout
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const action = arguments[0];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                const textOf = ele => clean(ele.innerText || ele.textContent);
                const compact = value => clean(value).replace(/\\s+/g, '');
                const actionName = action === 'phone' ? '电话' : '简历';
                const config = action === 'phone'
                  ? {askText: '索要手机', availableRe: /手机号|手机|电话|查看手机|查看电话|电话已获取|手机已获取/, modalRe: /确定向对方索要(手机|电话)吗|确定.*(索要|获取).*(手机|电话)/}
                  : {askRe: /索要简历/, availableRe: /看简历|查看简历|简历已获取/, modalRe: /确定向对方索要简历吗|确定.*索要.*简历/};
                if (action === 'resume') config.askText = '索要简历';
                const clickEle = ele => {
                  ele.scrollIntoView({block: 'center', inline: 'nearest'});
                  for (const name of ['mouseover', 'pointerover', 'pointerenter', 'mouseenter', 'pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                    ele.dispatchEvent(new MouseEvent(name, {bubbles: true, cancelable: true, composed: true, view: window}));
                  }
                };
                const dialog = Array.from(document.querySelectorAll('.ant-im-modal, .ant-lpt-modal, [role=dialog], [class*=modal]'))
                  .filter(visible)
                  .find(ele => config.modalRe.test(textOf(ele)) || (textOf(ele).includes('确定向对方索要') && textOf(ele).includes(actionName)));
                const confirmScope = dialog || Array.from(document.querySelectorAll('.ant-im-popover, .ant-popover, [class*=popover], [class*=Popconfirm]'))
                  .filter(visible)
                  .find(ele => /确定|确认/.test(textOf(ele)) && (textOf(ele).includes(actionName) || textOf(ele).includes('索要') || textOf(ele).length <= 120));
                if (confirmScope) {
                  const isConfirmButton = ele => {
                    const text = compact(textOf(ele));
                    if (!text || text.length > 12) return false;
                    if (/^(确定|确认|确定索要|确认索要|确定获取|确认获取)$/.test(text)) return true;
                    return text.includes('确定') || text.includes('确认');
                  };
                  const button = Array.from(confirmScope.querySelectorAll('button, [role=button], a, span, div'))
                    .filter(visible)
                    .filter(isConfirmButton)
                    .sort((a, b) => {
                      const ar = a.getBoundingClientRect();
                      const br = b.getBoundingClientRect();
                      const rank = ele => {
                        const tag = ele.tagName;
                        const cls = String(ele.className || '');
                        if (tag === 'BUTTON') return 0;
                        if (ele.getAttribute('role') === 'button') return 1;
                        if (cls.includes('btn') || cls.includes('Btn') || cls.includes('button') || cls.includes('Button')) return 2;
                        return 3;
                      };
                      const rankDiff = rank(a) - rank(b);
                      if (rankDiff) return rankDiff;
                      const aPrimary = /primary|danger|confirm|ok/i.test(String(a.className || '')) ? 0 : 1;
                      const bPrimary = /primary|danger|confirm|ok/i.test(String(b.className || '')) ? 0 : 1;
                      if (aPrimary !== bPrimary) return aPrimary - bPrimary;
                      return (ar.width * ar.height) - (br.width * br.height);
                    })[0];
                  if (!button) {
                    const debugButtons = Array.from(confirmScope.querySelectorAll('button, [role=button], a, span, div'))
                      .filter(visible)
                      .map(ele => ({tag: ele.tagName, text: textOf(ele).slice(0, 40), cls: String(ele.className || '').slice(0, 80)}))
                      .filter(item => item.text)
                      .slice(0, 20);
                    return {ok: false, reason: `${actionName}确认按钮未找到`, scope_text: textOf(confirmScope).slice(0, 160), buttons: debugButtons};
                  }
                  clickEle(button);
                  return {ok: false, clicked: true, confirming: true, reason: `已点击${actionName}确认`};
                }
                const toolbar = Array.from(document.querySelectorAll('.chatwin-action'))
                  .filter(visible)
                  .sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width)[0];
                if (!toolbar) return {ok: false, status: 'not_found', reason: '底部工具栏未找到'};
                const all = Array.from(toolbar.querySelectorAll('*')).filter(visible);
                const isAskText = ele => compact(textOf(ele)) === config.askText;
                const actionButtonSelector = action === 'phone' ? '.im-ui-action-button.action-phone' : '.im-ui-action-button.action-resume';
                const actionButtons = Array.from(toolbar.querySelectorAll(actionButtonSelector)).filter(visible);
                const actionButton = actionButtons
                  .find(ele => textOf(ele).includes(config.askText))
                  || actionButtons.find(ele => compact(textOf(ele)) === config.askText);
                const pendingButton = actionButtons.find(ele => /索要中|已索要|等待/.test(textOf(ele)));
                if (pendingButton) {
                  return {ok: true, status: 'already_requested', message: `${actionName}已索要，当前为：${textOf(pendingButton).slice(0, 24)}`};
                }
                const isAvailableText = ele => {
                  const text = textOf(ele);
                  const compactText = compact(text);
                  if (action === 'phone' && /^(手机号|手机|电话|查看手机|查看电话)$/.test(compactText)) return true;
                  if (action === 'resume' && /^(看简历|查看简历)$/.test(compactText)) return true;
                  if (action === 'phone') return false;
                  if (action === 'resume') return false;
                  if (!config.availableRe.test(text)) return false;
                  return !isAskText(ele);
                };
                const available = all
                  .filter(isAvailableText)
                  .filter(ele => textOf(ele).length <= 30)
                  .sort((a, b) => textOf(a).length - textOf(b).length)[0];
                if (available) return {ok: true, status: 'already_available', message: `${actionName}已可查看：${textOf(available).slice(0, 24)}`};
                const exactAction = actionButton;
                const ask = exactAction || all
                  .filter(isAskText)
                  .filter(ele => textOf(ele).length <= 30)
                  .sort((a, b) => {
                    return textOf(a).length - textOf(b).length;
                  })[0];
                if (!ask) return {ok: true, status: 'not_needed', message: `底部不是${config.askText}，跳过`};
                let button = ask;
                for (let node = ask, depth = 0; node && depth < 6; node = node.parentElement, depth += 1) {
                  const style = getComputedStyle(node);
                  const tag = node.tagName;
                  if (tag === 'BUTTON' || tag === 'A' || node.getAttribute('role') === 'button' || style.cursor === 'pointer') {
                    button = node;
                    break;
                  }
                }
                clickEle(button);
                return {ok: false, clicked: true, reason: `已点击${actionName}按钮，等待确认或状态变化`, clicked_text: textOf(button), clicked_class: String(button.className || '')};
                """,
                action,
            )
            if last_result and last_result.get("ok"):
                return last_result
            if last_result and last_result.get("confirming"):
                time.sleep(1.0)
                return {"status": "requested", "message": str(last_result.get("reason") or "已确认")}
            if last_result and last_result.get("clicked"):
                time.sleep(0.9)
                continue
            time.sleep(0.35)
        return {"status": "failed", "message": str(last_result or "按钮未找到")}

    @staticmethod
    def contact_status_text(status: str) -> str:
        return {
            "requested": "已索要",
            "already_requested": "已索要",
            "already_available": "已可查看",
            "not_found": "未找到会话",
            "failed": "失败",
            "unknown": "未知",
        }.get(status, status or "未知")

    def request_resumes_after_batch(self, targets: list[dict], results: list[dict]) -> None:
        self.progress.emit("resume_request_start", f"开始进入消息页索要简历：{len(targets)} 人")
        self.page.get(CHAT_URL)
        self.wait_for_chat_page()
        for target in targets:
            index = target.get("index")
            name = target.get("name", "")
            self.progress.emit("resume_request", f"正在处理第 {index} 个候选人的消息会话：{name}")
            status = ""
            note = ""
            try:
                opened = self.open_candidate_chat(target)
                if not opened.get("ok"):
                    status = "not_found"
                    note = opened.get("reason", "未找到消息卡片")
                else:
                    action = self.request_resume_in_current_chat(target)
                    status = action.get("status", "unknown")
                    note = action.get("message", "")
            except Exception as exc:
                status = "failed"
                note = str(exc)
            for item in results:
                if item.get("index") == index:
                    item["resume_request_status"] = status
                    item["resume_request_note"] = note
                    break
            self.progress.emit(
                "resume_request_done",
                f"第 {index} 个索要简历结果：{self.resume_status_text(status)}，{note}",
                {"index": index, "status": status, "note": note},
            )
            self.save_batch_summary(results)

    @staticmethod
    def resume_status_text(status: str) -> str:
        return {
            "requested": "已索要",
            "already_requested": "已索要",
            "already_available": "已可查看",
            "not_found": "未找到会话",
            "failed": "失败",
            "unknown": "未知",
        }.get(status, status or "未知")

    def wait_for_chat_page(self, timeout: int = 18) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            ok = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const body = document.body.innerText || '';
                const hasChatShell = body.includes('消息') && body.includes('全部职位');
                const hasChatList = Array.from(document.querySelectorAll('*'))
                  .some(ele => visible(ele) && (ele.innerText || '').includes('求职者投递'));
                const hasContactCards = document.querySelectorAll('.im-ui-contact-list-item').length > 0;
                return hasContactCards || hasChatList || (hasChatShell && body.includes('暂无'));
                """
            )
            if ok:
                return
            time.sleep(0.4)
        raise RuntimeError("消息页未加载完成。")

    def open_candidate_chat(self, target: dict, timeout: int = 18) -> dict:
        deadline = time.time() + timeout
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const target = arguments[0] || {};
                const rawName = String(target.name || '').trim();
                const normalizeName = value => String(value || '')
                  .replace(/（.*?）/g, '')
                  .replace(/\\(.*?\\)/g, '')
                  .replace(/先生|女士|TA设置了姓名保护|（|）|\\(|\\)/g, '')
                  .replace(/\\s+/g, '')
                  .trim();
                const nameTokens = Array.from(new Set([
                  rawName,
                  rawName.replace(/（.*?）/g, '').trim(),
                  rawName.replace(/\\(.*?\\)/g, '').trim(),
                  rawName.replace(/先生|女士|TA设置了姓名保护|（|）|\\(|\\)/g, '').trim(),
                  normalizeName(rawName),
                ].map(item => String(item || '').trim()).filter(Boolean)));
                const strictNameTokens = nameTokens.filter(token => token.length >= 2 && !/TA设置|姓名保护/.test(token));
                const chatJob = target.chat_job || {};
                const jobTitle = String(chatJob.title || '').trim();
                const jobTokens = jobTitle.split('-').filter(part => part && part.length >= 2);
                const position = String(target.job_position || '').trim();
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                const compact = value => clean(value).replace(/\\s+/g, '');
                if (!strictNameTokens.length) {
                  return {ok: false, reason: 'candidate name is empty or protected; refuse to request resume blindly'};
                }
                const leftArea = ele => {
                  const rect = ele.getBoundingClientRect();
                  return rect.left < Math.min(620, window.innerWidth * 0.45);
                };
                const likelyChatCard = ele => {
                  const rect = ele.getBoundingClientRect();
                  const text = clean(ele.innerText || ele.textContent);
                  if (!leftArea(ele) || rect.height < 36 || rect.height > 180 || rect.width < 180 || rect.width > 620) return false;
                  if (!text || text.length > 320) return false;
                  return strictNameTokens.some(token => compact(text).includes(compact(token)));
                };
                const scoreCard = ele => {
                  const text = clean(ele.innerText || ele.textContent);
                  if (!likelyChatCard(ele)) return -1;
                  let score = 0;
                  if (strictNameTokens.some(token => compact(text).includes(compact(token)))) score += 100;
                  if (jobTitle && text.includes(jobTitle)) score += 8;
                  for (const token of jobTokens) {
                    if (text.includes(token)) score += 2;
                  }
                  if (position && text.includes(position)) score += 2;
                  if (/\\d{1,2}:\\d{2}/.test(text)) score += 1;
                  if (text.includes('求职者投递')) score -= 5;
                  return score;
                };
                const cards = Array.from(document.querySelectorAll('li, [class*=item], [class*=card], [class*=session], [class*=conversation]'))
                  .filter(visible)
                  .map(ele => ({ele, score: scoreCard(ele), text: clean(ele.innerText || ele.textContent)}))
                  .filter(item => item.score >= 100)
                  .sort((a, b) => b.score - a.score || a.ele.getBoundingClientRect().top - b.ele.getBoundingClientRect().top);
                const best = cards[0];
                if (!best) {
                  const scrollBox = Array.from(document.querySelectorAll('*'))
                    .filter(ele => {
                      const rect = ele.getBoundingClientRect();
                      const style = getComputedStyle(ele);
                      return rect.width >= 250 && rect.width <= 460 && rect.height > 250
                        && ele.scrollHeight > ele.clientHeight + 20 && /(auto|scroll)/.test(style.overflowY);
                    })
                    .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight))[0];
                  if (scrollBox && scrollBox.scrollTop + scrollBox.clientHeight < scrollBox.scrollHeight - 5) {
                    scrollBox.scrollTop += Math.max(180, scrollBox.clientHeight * 0.8);
                    return {ok: false, waiting: true, reason: 'scrolling chat list'};
                  }
                  return {ok: false, reason: 'candidate chat card not found by name', tokens: strictNameTokens, job_tokens: jobTokens};
                }
                best.ele.scrollIntoView({block: 'center', inline: 'nearest'});
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  best.ele.dispatchEvent(new MouseEvent(name, {
                    bubbles: true,
                    cancelable: true,
                    composed: true,
                    view: window,
                  }));
                }
                return {ok: true, score: best.score, text: best.text.slice(0, 160), tokens: strictNameTokens};
                """,
                target,
            )
            if last_result and last_result.get("ok"):
                time.sleep(0.8)
                verified = self.current_chat_matches_target(target)
                if verified.get("ok"):
                    last_result["verified"] = verified
                    return last_result
                last_result = {
                    "ok": False,
                    "reason": "clicked chat card but active chat did not match target",
                    "clicked": last_result,
                    "verified": verified,
                }
            time.sleep(0.4)
        return last_result or {"ok": False, "reason": "candidate chat card not found"}

    def current_chat_matches_target(self, target: dict) -> dict:
        return self.page.run_js(
            """
            const target = arguments[0] || {};
            const rawName = String(target.name || '').trim();
            const normalizeName = value => String(value || '')
              .replace(/（.*?）/g, '')
              .replace(/\\(.*?\\)/g, '')
              .replace(/先生|女士|TA设置了姓名保护|（|）|\\(|\\)/g, '')
              .replace(/\\s+/g, '')
              .trim();
            const tokens = Array.from(new Set([
              rawName,
              normalizeName(rawName),
            ].map(item => String(item || '').trim()).filter(item => item.length >= 2 && !/TA设置|姓名保护/.test(item))));
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
            const compact = value => clean(value).replace(/\\s+/g, '');
            if (!tokens.length) return {ok: false, reason: 'candidate name is empty or protected'};
            const rightPanes = Array.from(document.querySelectorAll('[class*=chat], [class*=im], [class*=message], [class*=conversation], main, section, body'))
              .filter(visible)
              .filter(ele => {
                const rect = ele.getBoundingClientRect();
                return rect.left > Math.min(260, window.innerWidth * 0.18) && rect.width > 360 && rect.height > 240;
              })
              .sort((a, b) => {
                const ar = a.getBoundingClientRect();
                const br = b.getBoundingClientRect();
                return (br.width * br.height) - (ar.width * ar.height);
              });
            const scope = rightPanes[0] || document.body;
            const text = compact(scope.innerText || scope.textContent);
            return {
              ok: tokens.some(token => text.includes(compact(token))),
              tokens,
              sample: clean(scope.innerText || scope.textContent).slice(0, 180),
            };
            """,
            target,
        ) or {"ok": False, "reason": "chat verify failed"}

    def request_resume_in_current_chat(self, target: dict, timeout: int = 12) -> dict:
        matched = self.current_chat_matches_target(target)
        if not matched.get("ok"):
            return {
                "status": "failed",
                "message": f"当前聊天窗口不是目标候选人，停止索要简历：{matched}",
            }
        deadline = time.time() + timeout
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                const textOf = ele => clean(ele.innerText || ele.textContent);
                const compactTextOf = ele => textOf(ele).replace(/\\s+/g, '');
                const rightPanes = Array.from(document.querySelectorAll('[class*=chat], [class*=im], [class*=message], [class*=conversation], main, section, body'))
                  .filter(visible)
                  .filter(ele => {
                    const rect = ele.getBoundingClientRect();
                    return rect.left > Math.min(260, window.innerWidth * 0.18) && rect.width > 360 && rect.height > 240;
                  })
                  .sort((a, b) => {
                    const ar = a.getBoundingClientRect();
                    const br = b.getBoundingClientRect();
                    return (br.width * br.height) - (ar.width * ar.height);
                  });
                const chatScope = rightPanes[0] || document.body;
                const all = Array.from(chatScope.querySelectorAll('*')).filter(visible);
                const lowerToolbar = ele => ele.getBoundingClientRect().top > window.innerHeight * 0.55;
                const confirmResumeRequest = () => {
                  const dialogs = Array.from(document.querySelectorAll('.ant-im-modal, .ant-lpt-modal, [role=dialog], [class*=modal]'))
                    .filter(visible)
                    .filter(ele => textOf(ele).includes('确定向对方索要简历吗'));
                  const dialog = dialogs[0];
                  if (!dialog) return null;
                  const button = Array.from(dialog.querySelectorAll('button, [role=button], a, span, div'))
                    .filter(visible)
                    .filter(ele => compactTextOf(ele) === '确定')
                    .sort((a, b) => {
                      const ar = a.getBoundingClientRect();
                      const br = b.getBoundingClientRect();
                      return (ar.width * ar.height) - (br.width * br.height);
                    })[0];
                  if (!button) return {ok: false, reason: '索要简历确认按钮未找到'};
                  for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                    button.dispatchEvent(new MouseEvent(name, {
                      bubbles: true,
                      cancelable: true,
                      composed: true,
                      view: window,
                    }));
                  }
                  return {ok: false, clicked: true, confirming: true, reason: '已点击索要简历确认'};
                };
                const confirmResult = confirmResumeRequest();
                if (confirmResult) return confirmResult;
                const resumeAvailable = () => all
                  .filter(ele => lowerToolbar(ele) && /看简历|查看简历/.test(textOf(ele)))
                  .sort((a, b) => textOf(a).length - textOf(b).length)[0];
                const resumeView = resumeAvailable();
                if (resumeView) {
                  return {ok: true, status: 'already_available', message: `按钮已是${textOf(resumeView).slice(0, 20)}`};
                }
                const ask = all
                  .filter(lowerToolbar)
                  .filter(ele =>
                    String(ele.className || '').includes('action-resume')
                    || textOf(ele) === '索要简历'
                    || textOf(ele).includes('索要简历')
                  )
                  .filter(ele => textOf(ele).length <= 40)
                  .sort((a, b) => {
                    const aClass = String(a.className || '').includes('action-resume') ? 0 : 1;
                    const bClass = String(b.className || '').includes('action-resume') ? 0 : 1;
                    if (aClass !== bClass) return aClass - bClass;
                    const ar = a.getBoundingClientRect();
                    const br = b.getBoundingClientRect();
                    const aBottom = window.innerHeight - ar.bottom;
                    const bBottom = window.innerHeight - br.bottom;
                    return aBottom - bBottom || textOf(a).length - textOf(b).length;
                  })[0];
                if (!ask) return {ok: false, reason: '索要简历按钮未找到'};
                let button = ask;
                for (let depth = 0; button && depth < 6; depth += 1, button = button.parentElement) {
                  const style = getComputedStyle(button);
                  const tag = button.tagName;
                  const cls = String(button.className || '');
                  if (cls.includes('action-resume')) break;
                  if (tag === 'BUTTON' || tag === 'A' || button.getAttribute('role') === 'button' || style.cursor === 'pointer') {
                    break;
                  }
                }
                if (!button) button = ask;
                button.scrollIntoView({block: 'center', inline: 'nearest'});
                for (const name of ['mouseover', 'pointerover', 'pointerenter', 'mouseenter', 'pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  button.dispatchEvent(new MouseEvent(name, {
                    bubbles: true,
                    cancelable: true,
                    composed: true,
                    view: window,
                  }));
                }
                return {ok: false, clicked: true, reason: '已点击索要简历，等待状态变化', clicked_text: textOf(button), clicked_class: String(button.className || '')};
                """
            )
            if last_result and last_result.get("ok"):
                time.sleep(0.8)
                return last_result
            if last_result and last_result.get("clicked"):
                time.sleep(1.0)
                verify = self.page.run_js(
                    """
                    const visible = ele => {
                      const rect = ele.getBoundingClientRect();
                      const style = getComputedStyle(ele);
                      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                    };
                    const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                    const lowerToolbar = ele => ele.getBoundingClientRect().top > window.innerHeight * 0.55;
                    const all = Array.from(document.querySelectorAll('*')).filter(visible);
                    const view = all
                      .filter(ele => lowerToolbar(ele) && /看简历|查看简历/.test(clean(ele.innerText || ele.textContent)))
                      .sort((a, b) => clean(a.innerText || a.textContent).length - clean(b.innerText || b.textContent).length)[0];
                    if (view) return {ok: true, status: 'requested', message: `已索要简历，当前为${clean(view.innerText || view.textContent).slice(0, 20)}`};
                    return {ok: false};
                    """
                )
                if verify and verify.get("ok"):
                    return verify
                confirm_after_click = self.page.run_js(
                    """
                    const visible = ele => {
                      const rect = ele.getBoundingClientRect();
                      const style = getComputedStyle(ele);
                      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                    };
                    const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                    const textOf = ele => clean(ele.innerText || ele.textContent);
                    const compactTextOf = ele => textOf(ele).replace(/\\s+/g, '');
                    const dialog = Array.from(document.querySelectorAll('.ant-im-modal, .ant-lpt-modal, [role=dialog], [class*=modal]'))
                      .filter(visible)
                      .find(ele => textOf(ele).includes('确定向对方索要简历吗'));
                    if (!dialog) return {ok: false};
                    const button = Array.from(dialog.querySelectorAll('button, [role=button], a, span, div'))
                      .filter(visible)
                      .filter(ele => compactTextOf(ele) === '确定')
                      .sort((a, b) => {
                        const ar = a.getBoundingClientRect();
                        const br = b.getBoundingClientRect();
                        return (ar.width * ar.height) - (br.width * br.height);
                      })[0];
                    if (!button) return {ok: false, reason: 'confirm button not found'};
                    for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                      button.dispatchEvent(new MouseEvent(name, {
                        bubbles: true,
                        cancelable: true,
                        composed: true,
                        view: window,
                      }));
                    }
                    return {ok: true, status: 'requested', message: '已确认索要简历'};
                    """
                )
                if confirm_after_click and confirm_after_click.get("ok"):
                    time.sleep(1.0)
                    return confirm_after_click
            if last_result and last_result.get("confirming"):
                time.sleep(1.0)
                return {"ok": True, "status": "requested", "message": "已确认索要简历"}
            time.sleep(0.4)
        return {"status": "failed", "message": str(last_result or "索要简历按钮未找到")}

    def auto_open_communicate(self, selected_job: dict | None) -> dict:
        button_result = self.click_immediate_communicate()
        if button_result.get("status") == "already_communicated":
            return button_result
        self.select_chat_job(selected_job)
        self.confirm_chat_job()
        return {"status": "done"}

    def click_immediate_communicate(self) -> dict:
        target_text = "立即沟通"
        already_text = "继续沟通"
        deadline = time.time() + 12
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const targetText = arguments[0];
                const alreadyText = arguments[1];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && !ele.disabled;
                };
                const clean = ele => (ele.innerText || ele.textContent || '')
                  .trim()
                  .replace(/\\s+/g, ' ');
                const cleanText = value => String(value || '').trim().replace(/\\s+/g, ' ');
                const drawerLeft = () => {
                  const drawer = Array.from(document.querySelectorAll('.ant-im-drawer-content-wrapper, .ant-im-drawer'))
                    .filter(visible)
                    .map(ele => ele.getBoundingClientRect())
                    .filter(rect => rect.width > 0 && rect.left > window.innerWidth * 0.55)
                    .sort((a, b) => a.left - b.left)[0];
                  return drawer ? drawer.left : window.innerWidth + 1;
                };
                const currentResumeActionPanel = () => {
                  const rightLimit = drawerLeft();
                  const candidates = Array.from(document.querySelectorAll('.xpath-wrap-resume-download, [class*=operation]'))
                    .filter(visible)
                    .filter(ele => {
                      const rect = ele.getBoundingClientRect();
                      const text = cleanText(ele.innerText || ele.textContent);
                      return text.includes('觉得TA还不错')
                        && rect.left > window.innerWidth * 0.45
                        && rect.right < rightLimit - 8
                        && rect.width >= 180
                        && rect.width <= 420
                        && rect.height >= 80;
                    })
                    .sort((a, b) => {
                      const ac = String(a.className || '');
                      const bc = String(b.className || '');
                      const aDirect = ac.includes('xpath-wrap-resume-download') ? 0 : 1;
                      const bDirect = bc.includes('xpath-wrap-resume-download') ? 0 : 1;
                      if (aDirect !== bDirect) return aDirect - bDirect;
                      return (a.getBoundingClientRect().height * a.getBoundingClientRect().width)
                        - (b.getBoundingClientRect().height * b.getBoundingClientRect().width);
                    });
                  return candidates[0] || null;
                };
                const clickableOf = ele => {
                  let node = ele;
                  for (let depth = 0; node && depth < 8; depth += 1, node = node.parentElement) {
                    const tag = node.tagName;
                    const role = node.getAttribute('role') || '';
                    const cls = String(node.className || '');
                    const style = getComputedStyle(node);
                    if (
                      tag === 'BUTTON'
                      || tag === 'A'
                      || role === 'button'
                      || cls.includes('btn')
                      || cls.includes('Btn')
                      || cls.includes('button')
                      || cls.includes('Button')
                      || style.cursor === 'pointer'
                    ) {
                      return node;
                    }
                  }
                  return ele;
                };
                const ancestorText = (ele, depthLimit = 8) => {
                  let node = ele;
                  const texts = [];
                  for (let depth = 0; node && depth < depthLimit; depth += 1, node = node.parentElement) {
                    texts.push(clean(node));
                  }
                  return texts.join(' ');
                };
                const inCardList = ele => {
                  let node = ele;
                  for (let depth = 0; node && depth < 8; depth += 1, node = node.parentElement) {
                    const cls = String(node.className || '');
                    if (cls.includes('xpath-resume-card') || cls.includes('resumeCard')) return true;
                  }
                  return false;
                };
                const panel = currentResumeActionPanel();
                if (!panel) return {ok: false, reason: '当前简历右侧操作卡片未找到'};
                const candidates = Array.from(panel.querySelectorAll('*'))
                  .filter(visible)
                  .map(ele => ({source: ele, text: clean(ele), target: clickableOf(ele)}))
                  .filter(item => item.text === targetText || item.text.includes(targetText) || item.text === alreadyText || item.text.includes(alreadyText))
                  .filter(item => item.text.length <= 200 || clean(item.target).length <= 200)
                  .filter(item => visible(item.target));
                const alreadyCandidates = candidates
                  .filter(item => item.text === alreadyText || item.text.includes(alreadyText))
                  .filter(item => panel.contains(item.target));
                if (alreadyCandidates.length) {
                  alreadyCandidates.sort((a, b) => {
                    const aExact = a.text === alreadyText ? 0 : 1;
                    const bExact = b.text === alreadyText ? 0 : 1;
                    if (aExact !== bExact) return aExact - bExact;
                    return b.target.getBoundingClientRect().left - a.target.getBoundingClientRect().left;
                  });
                  return {ok: true, status: 'already_communicated', text: clean(alreadyCandidates[0].target)};
                }
                candidates.sort((a, b) => {
                  const aExact = a.text === targetText ? 0 : 1;
                  const bExact = b.text === targetText ? 0 : 1;
                  if (aExact !== bExact) return aExact - bExact;
                  const aOpenIm = String(a.target.className || '').includes('xpath-open-im-btn') ? 0 : 1;
                  const bOpenIm = String(b.target.className || '').includes('xpath-open-im-btn') ? 0 : 1;
                  if (aOpenIm !== bOpenIm) return aOpenIm - bOpenIm;
                  const clickableRank = ele => {
                    if (ele.tagName === 'BUTTON') return 0;
                    if (ele.tagName === 'A' || ele.getAttribute('role') === 'button') return 1;
                    return 2;
                  };
                  const aClickable = clickableRank(a.target);
                  const bClickable = clickableRank(b.target);
                  if (aClickable !== bClickable) return aClickable - bClickable;
                  const aList = inCardList(a.target) ? 1 : 0;
                  const bList = inCardList(b.target) ? 1 : 0;
                  if (aList !== bList) return aList - bList;
                  const aRect = a.target.getBoundingClientRect();
                  const bRect = b.target.getBoundingClientRect();
                  const aArea = aRect.width * aRect.height;
                  const bArea = bRect.width * bRect.height;
                  if (Math.abs(aArea - bArea) > 1) return aArea - bArea;
                  return bRect.left - aRect.left;
                });
                const button = candidates[0] && candidates[0].target;
                if (!button) return {ok: false, reason: `${targetText}按钮未找到`};
                button.scrollIntoView({block: 'center', inline: 'nearest'});
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  button.dispatchEvent(new MouseEvent(name, {
                    bubbles: true,
                    cancelable: true,
                    composed: true,
                    view: window,
                  }));
                }
                return {ok: true, text: clean(button)};
                """,
                target_text,
                already_text,
            )
            if last_result and last_result.get("ok"):
                if last_result.get("status") == "already_communicated":
                    return last_result
                self.wait_for_chat_job_modal()
                return {"status": "done", "button_text": last_result.get("text", "")}
            time.sleep(0.4)
        raise RuntimeError(f"无法点击立即沟通：{last_result}")

    def wait_for_chat_job_modal(self, timeout: int = 12) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            opened = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                return Array.from(document.querySelectorAll('.ant-lpt-modal,[role=dialog],.ant-lpt-drawer'))
                  .some(ele => visible(ele) && (ele.innerText || '').includes('请选择开聊职位'));
                """
            )
            if opened:
                return
            time.sleep(0.3)
        raise RuntimeError("开聊职位弹窗未出现。")

    def select_chat_job(self, selected_job: dict | None) -> None:
        if not selected_job or not selected_job.get("title"):
            raise RuntimeError("没有选中的开聊职位，无法继续沟通。")
        title = selected_job.get("title", "")
        salary = selected_job.get("salary", "")
        city = selected_job.get("city", "")
        self.progress.emit("chat_job_select", f"选择开聊职位：{title}")
        city_token = city.split("-")[0] if city else ""
        title_parts = [part for part in title.split("-") if part and part != city_token]
        search_queries = []
        for query in [
            title,
            "-".join(title_parts),
            " ".join(part for part in title_parts if len(part) >= 2),
            city_token,
            "",
        ]:
            query = str(query or "").strip()
            if query not in search_queries:
                search_queries.append(query)

        deadline = time.time() + 30
        result = None
        input_done = False
        scroll_round = 0
        query_index = 0
        while time.time() < deadline:
            search_query = search_queries[min(query_index, len(search_queries) - 1)]
            result = self.page.run_js(
                """
                const [title, salary, city, inputDone, scrollRound, searchQuery] = arguments;
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                const modal = Array.from(document.querySelectorAll('.ant-lpt-modal,[role=dialog],.ant-lpt-drawer'))
                  .filter(visible)
                  .find(ele => (ele.innerText || '').includes('请选择开聊职位'));
                if (!modal) return {ok: false, reason: 'modal not found'};

                const input = Array.from(modal.querySelectorAll('input'))
                  .find(ele => visible(ele) && (ele.placeholder || '').includes('搜索职位'));
                if (input && !inputDone) {
                  input.focus();
                  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                  setter.call(input, '');
                  input.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'deleteContentBackward', data: null}));
                  setter.call(input, searchQuery);
                  input.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: searchQuery}));
                  input.dispatchEvent(new Event('change', {bubbles: true}));
                  input.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true, key: 'Enter'}));
                  Array.from(modal.querySelectorAll('*')).forEach(ele => {
                    if (ele.scrollHeight > ele.clientHeight + 20) ele.scrollTop = 0;
                  });
                  return {ok: false, waiting: true, reason: 'waiting for filtered jobs'};
                }

                const scrollBox = Array.from(modal.querySelectorAll('*'))
                  .filter(ele => {
                    const style = getComputedStyle(ele);
                    const rect = ele.getBoundingClientRect();
                    return rect.height > 120
                      && rect.width > 200
                      && ele.scrollHeight > ele.clientHeight + 20
                      && /(auto|scroll)/.test(style.overflowY);
                  })
                  .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight))[0];
                if (scrollBox && scrollRound > 0) {
                  scrollBox.scrollTop = Math.min(
                    scrollBox.scrollHeight,
                    scrollBox.scrollTop + Math.max(120, scrollBox.clientHeight * 0.8)
                  );
                }

                const listItems = Array.from(modal.querySelectorAll('li, [class*=jobName], [class*=jobItem], [class*=jobCard], [class*=item]'))
                  .filter(visible)
                  .map(ele => {
                    const card = ele.closest('li') || ele;
                    return {ele: card, text: clean(card.innerText || card.textContent)};
                  })
                  .filter((item, index, arr) =>
                    item.text
                    && item.text.length >= 4
                    && arr.findIndex(other => other.ele === item.ele) === index
                  );
                const visibleJobs = listItems.map(item => item.text.slice(0, 120));
                let matched = listItems.find(item => item.text.includes(title));
                if (!matched) {
                  const cityToken = city ? city.split('-')[0] : '';
                  matched = listItems.find(item =>
                    (!salary || salary === '薪资面议' || item.text.includes(salary))
                    && (!cityToken || item.text.includes(cityToken))
                    && title.split('-').some(part => part.length >= 2 && item.text.includes(part))
                  );
                }
                if (!matched) {
                  const atBottom = !scrollBox || scrollBox.scrollTop + scrollBox.clientHeight >= scrollBox.scrollHeight - 5;
                  return {
                    ok: false,
                    waiting: !atBottom,
                    reason: atBottom ? 'job title not found in modal' : 'scrolling job list',
                    title,
                    search_query: searchQuery,
                    visible_jobs: visibleJobs,
                    scroll_top: scrollBox ? scrollBox.scrollTop : null,
                    scroll_height: scrollBox ? scrollBox.scrollHeight : null,
                  };
                }

                let card = matched.ele.closest('li') || matched.ele;
                for (let depth = 0; card && depth < 6; depth += 1, card = card.parentElement) {
                  const text = clean(card.innerText || card.textContent);
                  const rect = card.getBoundingClientRect();
                  if (
                    (text.includes(title) || text.includes(matched.text))
                    && (!salary || salary === '薪资面议' || text.includes(salary))
                    && (!city || text.includes(city.split('-')[0]))
                    && rect.height >= 50
                  ) {
                    break;
                  }
                }
                if (!card) card = matched.ele;
                card.scrollIntoView({block: 'center', inline: 'nearest'});
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  card.dispatchEvent(new MouseEvent(name, {
                    bubbles: true,
                    cancelable: true,
                    composed: true,
                    view: window,
                  }));
                }
                return {ok: true, text: clean(card.innerText || card.textContent).slice(0, 160)};
                """,
                title,
                salary,
                city,
                input_done,
                scroll_round,
                search_query,
            )
            if result and result.get("ok"):
                break
            input_done = True
            if result:
                if result.get("waiting") and result.get("reason") == "waiting for filtered jobs":
                    self.progress.emit(
                        "chat_job_search",
                        f"已在开聊职位弹窗输入职位关键词：{search_query or '全部职位'}",
                        result,
                    )
                    scroll_round += 1
                    time.sleep(1.1)
                    continue
                if result.get("reason") == "job title not found in modal" and query_index < len(search_queries) - 1:
                    self.progress.emit(
                        "chat_job_search",
                        f"开聊职位未命中，切换关键词再试：{search_queries[query_index + 1] or '全部职位'}",
                        result,
                    )
                    query_index += 1
                    input_done = False
                    scroll_round = 0
                    time.sleep(0.6)
                    continue
                self.progress.emit(
                    "chat_job_search",
                    f"查找开聊职位中：{result.get('reason', '')}；可见职位：{'; '.join(result.get('visible_jobs', [])[:3])}",
                    result,
                )
            if result and result.get("waiting") is False:
                break
            scroll_round += 1
            time.sleep(0.4)
        if not result or not result.get("ok"):
            raise RuntimeError(f"无法选择开聊职位：{result}")
        time.sleep(0.4)

    def confirm_chat_job(self) -> None:
        deadline = time.time() + 10
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
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
                const modal = Array.from(document.querySelectorAll('.ant-lpt-modal,[role=dialog],.ant-lpt-drawer'))
                  .filter(visible)
                  .find(ele => (ele.innerText || '').includes('请选择开聊职位'));
                if (!modal) return {ok: true, alreadyClosed: true};
                const button = Array.from(modal.querySelectorAll('button'))
                  .filter(visible)
                  .find(ele => clean(ele) === '确认');
                if (!button) return {ok: false, reason: 'confirm button not found'};
                if (button.disabled || button.className.includes('disabled')) {
                  return {ok: false, reason: 'confirm button disabled'};
                }
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  button.dispatchEvent(new MouseEvent(name, {
                    bubbles: true,
                    cancelable: true,
                    composed: true,
                    view: window,
                  }));
                }
                return {ok: true};
                """
            )
            if last_result and last_result.get("ok"):
                return
            time.sleep(0.4)
        raise RuntimeError(f"无法确认开聊职位：{last_result}")
