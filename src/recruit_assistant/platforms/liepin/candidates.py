from __future__ import annotations

import json
import time
from pathlib import Path

from .constants import RUNTIME_DIR


class CandidateMixin:
    def ensure_candidate_detail_open(self) -> None:
        has_detail = self.page.run_js(
            """
            const resume = document.querySelector('.xpath-resume-body')
              || document.querySelector('.resume-detail-content-body');
            const text = resume ? (resume.innerText || resume.textContent || '').trim() : '';
            return text.length > 0 && (
              text.includes('查看大图')
              || text.includes('Resume update time')
              || text.includes('求职意向')
              || text.includes('Job Seeking Intention')
            );
            """
        )
        if not has_detail:
            self.progress.emit("candidate_open", "当前不在候选人详情页，自动打开第一个候选人")
            self.open_first_candidate()

    def append_batch_candidate(self, profile: dict, path: str = "candidate_batch_profiles.json") -> None:
        output_path = Path(path)
        if not output_path.is_absolute():
            output_path = RUNTIME_DIR / output_path
        profiles = []
        if output_path.exists():
            try:
                profiles = json.loads(output_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                profiles = []
        profiles.append(profile)
        with open(output_path, "w", encoding="utf-8") as file:
            json.dump(profiles, file, ensure_ascii=False, indent=2)

    def go_to_next_candidate_for_batch(self) -> None:
        if self.has_candidate_turn_next():
            self.click_next_candidate()
            return
        self.open_next_unread_candidate_from_list()

    def fill_filter_input(self, placeholder: str, value: str) -> None:
        input_ele = self.wait_for_input(placeholder)
        input_ele.click()
        input_ele.clear()
        input_ele.input(value)
        self.wait_until_input_value(placeholder, value)

    def click_search_button(self) -> None:
        result = self.page.run_js(
            """
            const searchText = arguments[0];
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
            const searchBars = Array.from(document.querySelectorAll('[class*=searchBar]'))
              .filter(visible)
              .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
            const scopes = searchBars.length ? searchBars : [document.body];
            for (const scope of scopes) {
              const button = Array.from(scope.querySelectorAll('button'))
                .find(ele => visible(ele) && clean(ele) === searchText);
              if (button) {
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
              }
            }
            return {ok: false, reason: 'search button not found'};
            """,
            "\u641c\u7d22",
        )
        if not result or not result.get("ok"):
            raise RuntimeError(f"Could not click Search button: {result}")
        time.sleep(1.2)

    def open_first_candidate(self) -> None:
        self.wait_for_search_results()
        deadline = time.time() + 12
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
                const hasAnyResult = Array.from(document.querySelectorAll('.xpath-resume-card'))
                  .some(visible);
                if (!hasAnyResult) {
                  const body = (document.body && document.body.innerText) || '';
                  if (/暂无|没有找到|请设置搜索条件进行搜索/.test(body)) {
                    return {ok: false, reason: 'no candidate results'};
                  }
                  return {ok: false, reason: 'candidate cards not rendered yet'};
                }
                const cards = Array.from(document.querySelectorAll('.xpath-resume-card'))
                  .filter(visible)
                  .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
                const card = cards[0];
                if (!card) return {ok: false, reason: 'candidate card not found'};
                card.scrollIntoView({block: 'center', inline: 'nearest'});
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  card.dispatchEvent(new MouseEvent(name, {
                    bubbles: true,
                    cancelable: true,
                    composed: true,
                    view: window,
                  }));
                }
                return {ok: true, text: clean(card).slice(0, 120)};
                """
            )
            if last_result and last_result.get("ok"):
                self.wait_for_candidate_preview()
                return
            if last_result and last_result.get("reason") == "no candidate results":
                raise RuntimeError("没有搜索到候选人，请放宽筛选条件后再试。")
            time.sleep(0.4)
        raise RuntimeError(f"Could not open first candidate: {last_result}")

    def click_next_candidate(self) -> None:
        before_signature = self.get_candidate_signature()
        result = self.page.run_js(
            """
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0
                && rect.height > 0
                && style.display !== 'none'
                && style.visibility !== 'hidden';
            };
            const modal = Array.from(document.querySelectorAll('.ant-lpt-modal,[role=dialog],.ant-lpt-drawer'))
              .filter(visible)
              .find(ele => (ele.innerText || '').includes('查看大图') || (ele.innerText || '').includes('Resume update time'));
            const scope = modal || document.body;
            const directNext = Array.from(scope.querySelectorAll('.button-turn-next'))
              .filter(visible)
              .sort((a, b) => b.getBoundingClientRect().left - a.getBoundingClientRect().left)[0];
            const candidates = directNext ? [directNext] : Array.from(scope.querySelectorAll('[aria-label=right], .antlpticon-right'))
              .filter(visible)
              .map(ele => {
                let target = ele;
                for (let depth = 0; target && depth < 6; depth += 1, target = target.parentElement) {
                  const style = getComputedStyle(target);
                  if (
                    target.tagName === 'BUTTON'
                    || target.getAttribute('role') === 'button'
                    || String(target.className || '').includes('arrow')
                    || style.cursor === 'pointer'
                  ) {
                    return target;
                  }
                }
                return ele;
              })
              .filter(visible)
              .sort((a, b) => b.getBoundingClientRect().left - a.getBoundingClientRect().left);
            const button = candidates[0];
            if (!button) return {ok: false, reason: 'next candidate button not found'};
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
            """
        )
        if not result or not result.get("ok"):
            raise RuntimeError(f"Could not click next candidate: {result}")
        self.wait_for_candidate_change(before_signature)

    def has_candidate_turn_next(self) -> bool:
        return bool(
            self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                return Array.from(document.querySelectorAll('.button-turn-next')).some(visible);
                """
            )
        )

    def open_next_unread_candidate_from_list(self) -> None:
        result = self.page.run_js(
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
            const cards = Array.from(document.querySelectorAll('.xpath-resume-card'))
              .filter(visible)
              .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
            const unread = cards.find(card => !String(card.className || '').includes('read') && !clean(card).includes('已查看'));
            const card = unread || cards[0];
            if (!card) return {ok: false, reason: 'candidate card not found'};
            card.scrollIntoView({block: 'center', inline: 'nearest'});
            for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
              card.dispatchEvent(new MouseEvent(name, {
                bubbles: true,
                cancelable: true,
                composed: true,
                view: window,
              }));
            }
            return {ok: true, text: clean(card).slice(0, 120)};
            """
        )
        if not result or not result.get("ok"):
            raise RuntimeError(f"Could not open next candidate from list: {result}")
        self.wait_for_candidate_preview()

    def get_candidate_signature(self) -> str:
        signature = self.page.run_js(
            """
            const resume = document.querySelector('.xpath-resume-body')
              || document.querySelector('.resume-detail-content-body')
              || Array.from(document.querySelectorAll('.ant-lpt-modal,[role=dialog]'))
                .find(ele => (ele.innerText || '').includes('查看大图') || (ele.innerText || '').includes('Resume update time'));
            const text = resume ? (resume.innerText || resume.textContent || '').trim() : '';
            return text.slice(0, 800);
            """
        )
        return str(signature or "")

    def wait_for_candidate_change(self, before_signature: str, timeout: int = 15) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                self.wait_for_candidate_profile_text(timeout=2)
            except RuntimeError:
                time.sleep(0.3)
                continue
            current = self.get_candidate_signature()
            if current and current != before_signature:
                return
            time.sleep(0.4)
        raise RuntimeError("Next candidate did not load.")

    def wait_for_search_results(self, timeout: int = 15) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            found = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const body = document.body.innerText || '';
                const hasResumeCard = Array.from(document.querySelectorAll('.xpath-resume-card'))
                  .some(visible);
                const hasCardAction = Array.from(document.querySelectorAll('button'))
                  .some(ele => visible(ele) && (ele.innerText || '').trim().replace(/\\s+/g, ' ') === '立即沟通');
                return hasResumeCard || hasCardAction || body.includes('共有') && body.includes('份简历');
                """
            )
            if found:
                return
            time.sleep(0.4)
        raise RuntimeError("Search results did not load.")

    def wait_for_candidate_preview(self, timeout: int = 12) -> None:
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
                const body = document.body.innerText || '';
                const hasPreviewUrl = location.hash.includes('preview');
                const hasPreviewPanel = body.includes('觉得TA还不错')
                  || Array.from(document.querySelectorAll('[class*=drawer], [class*=modal]'))
                    .some(ele => visible(ele) && (ele.innerText || '').includes('觉得TA还不错'));
                return hasPreviewUrl || hasPreviewPanel;
                """
            )
            if opened:
                return
            time.sleep(0.3)
        raise RuntimeError("Candidate preview did not open.")

    def wait_for_candidate_profile_text(self, timeout: int = 15) -> None:
        deadline = time.time() + timeout
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const resume = document.querySelector('.xpath-resume-body')
                  || document.querySelector('.resume-detail-content-body');
                const text = resume ? (resume.innerText || resume.textContent || '').trim() : '';
                const hasCoreResume = (
                  text.includes('求职意向')
                  || text.includes('工作经历')
                  || text.includes('教育经历')
                  || text.includes('简历编号')
                  || text.includes('Job intention')
                  || text.includes('Work Experience')
                  || text.includes('Education Experience')
                  || text.includes('Resume update time')
                  || text.includes('Years old')
                  || text.includes('Years of work experience')
                );
                return {
                  ok: text.length >= 200 && hasCoreResume,
                  has_resume: !!resume,
                  text_length: text.length,
                  url: window.location.href,
                  sample: text.slice(0, 120),
                };
                """
            )
            if last_result and last_result.get("ok"):
                return
            time.sleep(0.4)
        raise RuntimeError(f"Candidate profile text did not load: {last_result}")

    def extract_candidate_profile(self) -> dict:
        self.wait_for_candidate_profile_text()
        profile = self.page.run_js(
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
            const resume = document.querySelector('.xpath-resume-body')
              || document.querySelector('.resume-detail-content-body');
            if (!resume) return {ok: false, reason: 'resume detail body not found'};

            let rawLines = (resume.innerText || '')
              .split('\\n')
              .map(cleanText)
              .filter(Boolean);
            const profileStart = rawLines.findIndex(line => line === '查看大图');
            if (profileStart > 0) {
              rawLines = rawLines.slice(profileStart);
            }
            if (!rawLines.length) {
              return {
                ok: false,
                reason: 'resume detail text is empty',
                url: window.location.href,
              };
            }
            const text = rawLines.join('\\n');
            const indexOf = label => rawLines.findIndex(line => line === label);
            const firstIndexOfAny = (labels, start = 0) => {
              const indexes = labels
                .map(label => rawLines.findIndex((line, index) => index >= start && line === label))
                .filter(index => index >= 0);
              return indexes.length ? Math.min(...indexes) : -1;
            };
            const sliceSection = (title, nextTitles) => {
              const start = indexOf(title);
              if (start < 0) return [];
              const end = firstIndexOfAny(nextTitles, start + 1);
              return rawLines.slice(start + 1, end >= 0 ? end : rawLines.length)
                .filter(line => !/^查看全部\\d+个$/.test(line));
            };
            const first = predicate => rawLines.find(predicate) || '';
            const last = array => array.length ? array[array.length - 1] : '';
            const intentStart = firstIndexOfAny(['求职意向', 'Job Seeking Intention']);
            const beforeIntent = rawLines.slice(0, intentStart >= 0 ? intentStart : rawLines.length);
            const isHeaderNoise = line => ['查看大图', '展开'].includes(line)
              || /活跃$/.test(line)
              || line === '在线'
              || /^Online$/i.test(line)
              || line === '发起意向沟通'
              || line.includes('高端人才')
              || line.startsWith('附件简历');
            const cleanHeader = beforeIntent.filter(line => !isHeaderNoise(line));
            const name = cleanHeader.find(line => !line.startsWith('更新简历时间：')) || '';
            const updatedAt = (first(line => line.startsWith('更新简历时间：') || line.startsWith('Resume update time：')) || '')
              .replace('更新简历时间：', '')
              .replace('Resume update time：', '');
            const age = first(line => /\\d+\\s*岁/.test(line) || /\\d+\\s*Years? old/i.test(line));
            const workYears = first(line =>
              /^工作/.test(line)
                || /^\\d+年$/.test(line)
                || /^\\d+年\\d+个月$/.test(line)
                || /Years? of work experience/i.test(line)
            );
            const currentSalary = first(line => /\\d+\\s*[kK].*薪|\\d+\\s*-\\s*\\d+\\s*[kK]|\\d+\\s*[kK].*Months/i.test(line));
            const educationSummary = first(line => line.includes('·'));
            const jobStatus = first(line => /在职|离职|新机会|找工作|暂无跳槽|On job|seeking|new job/i.test(line));
            const residence = cleanHeader.find(line =>
              line
                && line !== name
                && line !== age
                && line !== workYears
                && line !== currentSalary
                && line !== educationSummary
                && line !== jobStatus
                && !isHeaderNoise(line)
                && !line.startsWith('更新简历时间：')
                && !line.startsWith('Resume update time：')
                && !line.startsWith('查看全部')
                && line.length <= 40
            ) || '';
            const selfEvaluation = last(beforeIntent.filter(line =>
              line.length > 20
                && line !== educationSummary
                && !line.startsWith('更新简历时间：')
            ));

            const sectionTitles = [
              '求职意向', 'Job Seeking Intention',
              '工作经历', 'Work Experience',
              '项目经历', 'Project Experience',
              '教育经历', 'Education Experience',
              '资格证书', '语言能力', '附件简历', '简历编号', 'Resume No.', '觉得TA还不错：'
            ];
            const sliceFirstSection = (titles, nextTitles) => {
              for (const title of titles) {
                const lines = sliceSection(title, nextTitles.filter(item => !titles.includes(item)));
                if (lines.length) return lines;
              }
              return [];
            };
            const jobIntentLines = sliceFirstSection(['求职意向', 'Job Seeking Intention'], sectionTitles);
            const workLines = sliceFirstSection(['工作经历', 'Work Experience'], sectionTitles);
            const projectLines = sliceFirstSection(['项目经历', 'Project Experience'], sectionTitles);
            const educationLines = sliceFirstSection(['教育经历', 'Education Experience'], sectionTitles);
            const certificateLines = sliceSection('资格证书', sectionTitles.filter(title => title !== '资格证书'));
            const languageLines = sliceSection('语言能力', sectionTitles.filter(title => title !== '语言能力'));
            const resumeNoIndex = indexOf('简历编号');
            const resumeNo = resumeNoIndex >= 0 ? (rawLines[resumeNoIndex + 1] || '').replace(/^:\\s*/, '') : '';

            const splitJobIntent = lines => ({
              position: lines[0] || '',
              salary: lines[1] || '',
              cities: lines[2] || '',
              industry: lines[3] || '',
              raw_lines: lines,
            });

            return {
              ok: true,
              url: window.location.href,
              extracted_at: new Date().toISOString(),
              basic: {
                name,
                updated_at: updatedAt,
                location: residence,
                work_years: workYears,
                age,
                job_status: jobStatus,
                current_salary: currentSalary,
                education_summary: educationSummary,
                self_evaluation: selfEvaluation,
              },
              job_intention: splitJobIntent(jobIntentLines),
              work_experience: {
                raw_lines: workLines,
                raw_text: workLines.join('\\n'),
              },
              project_experience: {
                raw_lines: projectLines,
                raw_text: projectLines.join('\\n'),
              },
              education_experience: {
                raw_lines: educationLines,
                raw_text: educationLines.join('\\n'),
              },
              certificates: certificateLines,
              languages: languageLines,
              resume_no: resumeNo,
              raw_lines: rawLines,
              raw_text: text,
            };
            """
        )
        if not profile or not profile.get("ok"):
            raise RuntimeError(f"Could not extract candidate profile: {profile}")
        return profile

    def save_candidate_profile(self, metadata: dict | None = None, path: str = "candidate_profile.json") -> dict:
        if isinstance(metadata, str):
            path = metadata
            metadata = None
        profile = self.extract_candidate_profile()
        if metadata:
            profile.update(metadata)
        output_path = Path(path)
        if not output_path.is_absolute():
            output_path = RUNTIME_DIR / output_path
        with open(output_path, "w", encoding="utf-8") as file:
            json.dump(profile, file, ensure_ascii=False, indent=2)
        return profile
