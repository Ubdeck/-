from __future__ import annotations

import json
import time
from pathlib import Path

from .constants import JOB_MANAGER_URL, RUNTIME_DIR


class JobManagerMixin:
    def fetch_job_list(self) -> list[dict]:
        self.check_stopped()
        self.page.get(JOB_MANAGER_URL)
        self.wait_for_job_cards()
        max_page = self.get_job_manager_page_count()
        jobs: list[dict] = []
        seen: set[str] = set()

        for page_number in range(1, max_page + 1):
            self.check_stopped()
            self.go_to_job_manager_page(page_number)
            for job in self.extract_current_job_cards():
                key = job.get("job_id") or job.get("href") or job.get("title")
                if key and key not in seen:
                    seen.add(key)
                    jobs.append(job)
        self.save_job_list(jobs)
        return jobs

    def wait_for_job_cards(self, timeout: int = 15) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            loaded = self.page.run_js(
                """
                const body = document.body ? document.body.innerText || '' : '';
                return document.querySelectorAll('a[class*=jobTitle]').length > 0
                  || body.includes('暂无数据')
                  || body.includes('暂无职位');
                """
            )
            if loaded:
                return
            time.sleep(0.4)
        raise RuntimeError("Job manager list did not load.")

    def get_job_manager_page_count(self) -> int:
        count = self.page.run_js(
            """
            const nums = Array.from(document.querySelectorAll('li[class*=pagination-item]'))
              .map(ele => Number(ele.getAttribute('title') || ele.innerText || ''))
              .filter(Number.isFinite);
            return nums.length ? Math.max(...nums) : 1;
            """
        )
        return max(int(count or 1), 1)

    def go_to_job_manager_page(self, page_number: int) -> None:
        result = self.page.run_js(
            """
            const pageNumber = String(arguments[0]);
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0
                && rect.height > 0
                && style.display !== 'none'
                && style.visibility !== 'hidden';
            };
            const current = document.querySelector('li[class*=pagination-item-active]');
            if (current && (current.getAttribute('title') || current.innerText || '').trim() === pageNumber) {
              return {ok: true, already: true};
            }
            const item = Array.from(document.querySelectorAll('li[class*=pagination-item]'))
              .find(ele => visible(ele) && (ele.getAttribute('title') || ele.innerText || '').trim() === pageNumber);
            if (!item) return {ok: false, reason: 'pagination item not found'};
            item.scrollIntoView({block: 'center', inline: 'nearest'});
            for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
              item.dispatchEvent(new MouseEvent(name, {
                bubbles: true,
                cancelable: true,
                composed: true,
                view: window,
              }));
            }
            return {ok: true};
            """,
            page_number,
        )
        if not result or not result.get("ok"):
            raise RuntimeError(f"Could not open job manager page {page_number}: {result}")

        deadline = time.time() + 10
        while time.time() < deadline:
            active = self.page.run_js(
                """
                const item = document.querySelector('li[class*=pagination-item-active]');
                return item ? (item.getAttribute('title') || item.innerText || '').trim() : '';
                """
            )
            if str(active) == str(page_number):
                self.wait_for_job_cards()
                return
            time.sleep(0.3)
        raise RuntimeError(f"Job manager page {page_number} did not become active.")

    def extract_current_job_cards(self) -> list[dict]:
        jobs = self.page.run_js(
            """
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0
                && rect.height > 0
                && style.display !== 'none'
                && style.visibility !== 'hidden';
            };
            const cleanText = value => String(value || '')
              .replace(/\\u00a0/g, ' ')
              .replace(/[ \\t]+/g, ' ')
              .trim();
            const getJobId = href => {
              try {
                const url = new URL(href, location.href);
                return url.searchParams.get('ejob_id') || url.searchParams.get('job_id') || '';
              } catch {
                return '';
              }
            };
            const findCard = link => {
              let node = link;
              for (let depth = 0; node && depth < 8; depth += 1, node = node.parentElement) {
                const cls = String(node.className || '');
                const text = node.innerText || '';
                if (cls.includes('jobCardWrap') || (text.includes('沟通中') && text.includes('待看/收到简历'))) {
                  return node;
                }
              }
              return link.parentElement;
            };
            return Array.from(document.querySelectorAll('a[class*=jobTitle]'))
              .filter(visible)
              .map(link => {
                const card = findCard(link);
                const titleInfo = card && card.querySelector('[class*=jobTitleInfo]');
                const infoLines = ((titleInfo || card || link).innerText || '')
                  .split('\\n')
                  .map(cleanText)
                  .filter(Boolean);
                const cardLines = ((card || link).innerText || '')
                  .split('\\n')
                  .map(cleanText)
                  .filter(Boolean);
                const title = cleanText(link.getAttribute('title') || link.innerText || link.textContent);
                const titleIndex = infoLines.indexOf(title);
                const afterTitle = titleIndex >= 0 ? infoLines.slice(titleIndex + 1) : infoLines.slice(1);
                const href = link.href || '';
                const communicateIndex = cardLines.indexOf('沟通中');
                const receivedIndex = cardLines.indexOf('待看/收到简历');
                const label = [title, afterTitle[0], afterTitle[1]]
                  .filter(Boolean)
                  .join(' | ');
                return {
                  title,
                  label,
                  city: afterTitle[0] || '',
                  salary: afterTitle[1] || '',
                  refreshed_at: afterTitle[2] || '',
                  communicate_count: communicateIndex > 0 ? cardLines[communicateIndex - 1] : '',
                  resume_count: receivedIndex > 1 ? `${cardLines[receivedIndex - 2] || ''}${cardLines[receivedIndex - 1] || ''}` : '',
                  job_id: getJobId(href),
                  href,
                  raw_lines: cardLines,
                };
              })
              .filter(job => job.title);
            """
        )
        return jobs or []

    def save_job_list(self, jobs: list[dict], path: str = "liepin_jobs.json") -> None:
        output_path = Path(path)
        if not output_path.is_absolute():
            output_path = RUNTIME_DIR / output_path
        with open(output_path, "w", encoding="utf-8") as file:
            json.dump(jobs, file, ensure_ascii=False, indent=2)
