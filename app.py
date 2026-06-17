# -*- coding: utf-8 -*-
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

import os

from liepin_search import LiepinSearchPage, SearchFilters


ACTIVE_STATUS_OPTIONS = (
    "",
    "不限",
    "今天活跃",
    "3天内活跃",
    "7天内活跃",
    "30天内活跃",
    "最近三个月活跃",
    "最近半年活跃",
)
JOB_STATUS_OPTIONS = (
    "",
    "不限",
    "离职，正在找工作",
    "在职，急寻新工作",
    "在职，看看新机会",
    "在职，暂无跳槽打算",
)
JOB_HOP_OPTIONS = (
    "",
    "不限",
    "近5年不超过3段",
    "近3年不超过2段",
    "近2段均不低于2年",
)
AGE_OPTIONS = (
    "",
    "不限",
    "20-25岁",
    "25-30岁",
    "30-35岁",
    "35-40岁",
    "40岁以上",
)
GENDER_OPTIONS = ("", "不限", "男", "女")
LANGUAGE_OPTIONS = ("", "不限", "英语", "日语", "粤语")
GRADUATION_YEAR_OPTIONS = (
    "",
    "不限",
    "2025年毕业",
    "2026年毕业",
    "2027年毕业",
    "2028年毕业",
    "2029年毕业",
    "2030年毕业",
)


class LiepinApp(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master)
        self.master = master
        self.messages: queue.Queue[object] = queue.Queue()

        self.port_var = tk.StringVar(value="9223")
        self.chat_job_var = tk.StringVar()
        self.chat_job_options: list[dict] = []
        self.chat_job_by_label: dict[str, dict] = {}
        self.keywords_var = tk.StringVar()
        self.job_var = tk.StringVar()
        self.company_var = tk.StringVar()
        self.current_city_var = tk.StringVar()
        self.expected_city_var = tk.StringVar()
        self.experience_var = tk.StringVar()
        self.education_var = tk.StringVar()
        self.recruitment_type_var = tk.StringVar()
        self.school_type_vars = {
            "211": tk.BooleanVar(value=False),
            "985": tk.BooleanVar(value=False),
            "双一流": tk.BooleanVar(value=False),
            "海外留学": tk.BooleanVar(value=False),
        }
        self.active_status_var = tk.StringVar()
        self.job_status_var = tk.StringVar()
        self.job_hop_frequency_var = tk.StringVar()
        self.age_requirement_var = tk.StringVar()
        self.gender_requirement_var = tk.StringVar()
        self.language_requirement_var = tk.StringVar()
        self.graduation_year_var = tk.StringVar()
        self.keywords_ai_var = tk.BooleanVar(value=False)
        self.job_ai_var = tk.BooleanVar(value=False)
        self.company_ai_var = tk.BooleanVar(value=False)
        self.deepseek_api_key_var = tk.StringVar(value=os.environ.get("DEEPSEEK_API_KEY", ""))
        self.deepseek_model_var = tk.StringVar(value="deepseek-chat")
        self.auto_communicate_var = tk.BooleanVar(value=True)
        self.candidate_limit_var = tk.StringVar(value="1")
        self.status_var = tk.StringVar(value="准备就绪")
        self.result_rows: list[dict] = []

        self.build()
        self.poll_messages()
        self.after(500, self.run_refresh_jobs)

    def build(self) -> None:
        self.master.title("猎聘筛选助手")
        self.master.geometry("720x760")
        self.master.minsize(620, 520)

        self.pack(fill="both", expand=True)
        self.rowconfigure(0, weight=1)
        self.rowconfigure(1, weight=0)
        self.columnconfigure(0, weight=1)

        canvas = tk.Canvas(self, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        content = ttk.Frame(canvas, padding=16)

        content.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas_window = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        def on_mousewheel(event: tk.Event) -> str:
            delta = int(-1 * (event.delta / 120)) if getattr(event, "delta", 0) else 0
            if delta == 0 and getattr(event, "delta", 0):
                delta = -1 if event.delta > 0 else 1
            if delta == 0:
                delta = -1 if getattr(event, "num", None) == 5 else 1 if getattr(event, "num", None) == 4 else 0
            if delta:
                canvas.yview_scroll(delta, "units")
                return "break"
            return "break"

        canvas.bind("<MouseWheel>", on_mousewheel)
        canvas.bind("<Button-4>", on_mousewheel)
        canvas.bind("<Button-5>", on_mousewheel)

        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(canvas_window, width=event.width),
        )

        content.columnconfigure(0, weight=1)
        self.add_basic_section(content).grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.add_education_section(content).grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.add_other_section(content).grid(row=2, column=0, sticky="ew", pady=(0, 10))
        self.add_ai_section(content).grid(row=3, column=0, sticky="ew", pady=(0, 10))
        self.add_result_section(content).grid(row=4, column=0, sticky="nsew", pady=(0, 10))
        self.add_log_section(content).grid(row=5, column=0, sticky="nsew", pady=(0, 10))
        self.bind_mousewheel(content, on_mousewheel)

        action = ttk.Frame(self, padding=(16, 10, 16, 12))
        action.grid(row=1, column=0, columnspan=2, sticky="ew")
        action.columnconfigure(0, weight=1)
        ttk.Button(action, text="填入猎聘页面", command=self.run_apply).grid(
            row=0, column=0, sticky="ew", pady=(0, 8)
        )
        ttk.Label(action, textvariable=self.status_var, foreground="#4f46e5").grid(
            row=1, column=0, sticky="w", pady=6
        )
        ttk.Label(
            action,
            text="说明：连接 9223 已登录浏览器；全流程按 DOM 定位，不使用坐标点击。",
            foreground="#666666",
        ).grid(row=2, column=0, sticky="w", pady=(4, 0))

    def bind_mousewheel(self, widget: tk.Widget, callback) -> None:
        widget.bind("<MouseWheel>", callback, add="+")
        widget.bind("<Button-4>", callback, add="+")
        widget.bind("<Button-5>", callback, add="+")
        for child in widget.winfo_children():
            self.bind_mousewheel(child, callback)

    def add_basic_section(self, parent: ttk.Frame) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent, text="基础条件", padding=12)
        frame.columnconfigure(1, weight=1)

        self.add_entry(frame, 0, "浏览器端口", self.port_var, width=12, sticky_value="w")
        ttk.Label(frame, text="开聊职位").grid(row=1, column=0, sticky="w", pady=6)
        self.chat_job_combo = ttk.Combobox(frame, textvariable=self.chat_job_var, values=(), state="readonly")
        self.chat_job_combo.grid(row=1, column=1, sticky="ew", pady=6)
        ttk.Button(frame, text="刷新职位", command=self.run_refresh_jobs).grid(row=1, column=2, padx=(10, 0), pady=6)
        self.add_entry(frame, 2, "顶部关键词", self.keywords_var)
        ttk.Checkbutton(frame, text="AI填词", variable=self.keywords_ai_var).grid(row=2, column=2, padx=(10, 0))
        self.add_entry(frame, 3, "职位名称", self.job_var)
        ttk.Checkbutton(frame, text="AI填词", variable=self.job_ai_var).grid(row=3, column=2, padx=(10, 0))
        self.add_entry(frame, 4, "公司名称", self.company_var)
        ttk.Checkbutton(frame, text="AI填词", variable=self.company_ai_var).grid(row=4, column=2, padx=(10, 0))
        self.add_entry(frame, 5, "目前城市", self.current_city_var, colspan=2)
        self.add_entry(frame, 6, "期望城市", self.expected_city_var, colspan=2)
        self.add_combo(frame, 7, "经验", self.experience_var, ("", "不限", "在校/应届", "1-3年", "3-5年", "5-10年"))
        return frame

    def add_education_section(self, parent: ttk.Frame) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent, text="学历条件", padding=12)
        frame.columnconfigure(1, weight=1)

        self.add_combo(
            frame,
            0,
            "教育经历",
            self.education_var,
            ("", "不限", "本科", "硕士", "博士/博士后", "大专", "中专/中技", "高中及以下"),
        )
        self.add_combo(
            frame,
            1,
            "统招要求",
            self.recruitment_type_var,
            ("", "不限", "统招本科", "统招硕士", "统招博士", "统招大专"),
        )

        ttk.Label(frame, text="院校要求").grid(row=2, column=0, sticky="w", pady=6)
        school_frame = ttk.Frame(frame)
        school_frame.grid(row=2, column=1, columnspan=2, sticky="ew", pady=6)
        for index, (label, var) in enumerate(self.school_type_vars.items()):
            ttk.Checkbutton(school_frame, text=label, variable=var).grid(
                row=0, column=index, sticky="w", padx=(0, 12)
            )
        return frame

    def add_other_section(self, parent: ttk.Frame) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent, text="其他筛选", padding=12)
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)

        rows = [
            ("活跃状态", self.active_status_var, ACTIVE_STATUS_OPTIONS),
            ("求职状态", self.job_status_var, JOB_STATUS_OPTIONS),
            ("跳槽频率", self.job_hop_frequency_var, JOB_HOP_OPTIONS),
            ("年龄要求", self.age_requirement_var, AGE_OPTIONS),
            ("性别要求", self.gender_requirement_var, GENDER_OPTIONS),
            ("语言要求", self.language_requirement_var, LANGUAGE_OPTIONS),
            ("毕业年份", self.graduation_year_var, GRADUATION_YEAR_OPTIONS),
        ]
        for index, (label, variable, values) in enumerate(rows):
            row = index // 2
            col = (index % 2) * 2
            ttk.Label(frame, text=label).grid(row=row, column=col, sticky="w", padx=(0, 8), pady=6)
            ttk.Combobox(frame, textvariable=variable, values=values, state="readonly").grid(
                row=row, column=col + 1, sticky="ew", padx=(0, 18), pady=6
            )
        return frame

    def add_ai_section(self, parent: ttk.Frame) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent, text="AI匹配与自动沟通", padding=12)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="DeepSeek Key").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=self.deepseek_api_key_var, show="*").grid(
            row=0, column=1, sticky="ew", pady=6
        )
        ttk.Label(frame, text="模型").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=self.deepseek_model_var).grid(row=1, column=1, sticky="ew", pady=6)
        ttk.Checkbutton(
            frame,
            text="AI判断通过后自动点击立即沟通",
            variable=self.auto_communicate_var,
        ).grid(row=2, column=1, sticky="w", pady=6)
        self.add_entry(frame, 3, "处理人数", self.candidate_limit_var, width=12, sticky_value="w")

        ttk.Label(frame, text="匹配要求").grid(row=4, column=0, sticky="nw", pady=6)
        self.ai_requirements_text = tk.Text(frame, height=5, wrap="word", undo=True)
        self.ai_requirements_text.grid(row=4, column=1, sticky="ew", pady=6)
        return frame

    def add_result_section(self, parent: ttk.Frame) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent, text="候选人AI评价", padding=12)
        frame.columnconfigure(0, weight=1)

        columns = ("index", "name", "target", "result", "score", "comm", "reason")
        self.result_tree = ttk.Treeview(frame, columns=columns, show="headings", height=8)
        headings = {
            "index": "#",
            "name": "候选人",
            "target": "求职/城市",
            "result": "AI结果",
            "score": "分数",
            "comm": "沟通",
            "reason": "理由",
        }
        widths = {
            "index": 42,
            "name": 88,
            "target": 180,
            "result": 72,
            "score": 52,
            "comm": 72,
            "reason": 300,
        }
        for column in columns:
            self.result_tree.heading(column, text=headings[column])
            self.result_tree.column(column, width=widths[column], anchor="w", stretch=column == "reason")
        self.result_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.result_tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.result_tree.configure(yscrollcommand=scrollbar.set)
        return frame

    def add_log_section(self, parent: ttk.Frame) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent, text="运行日志", padding=12)
        frame.columnconfigure(0, weight=1)
        self.log_text = tk.Text(frame, height=10, wrap="word", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)
        return frame

    def append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_logs(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def clear_candidate_results(self) -> None:
        self.result_rows = []
        for item in self.result_tree.get_children():
            self.result_tree.delete(item)

    def update_candidate_results(self, results: list[dict]) -> None:
        self.clear_candidate_results()
        for item in results:
            self.add_candidate_result(item)

    def add_candidate_result(self, item: dict) -> None:
        self.result_rows.append(item)
        target = item.get("target") or item.get("job_position") or ""
        city = item.get("location") or item.get("job_cities") or ""
        target_text = " / ".join(part for part in (target, city) if part)
        result_text = "匹配" if item.get("match") else "不匹配"
        communicate_text = {
            "done": "已确认",
            "already_communicated": "已沟通",
            "failed": "失败",
            "": "",
        }.get(item.get("communicate_status", ""), item.get("communicate_status", ""))
        resume_text = {
            "requested": "已索要",
            "already_available": "已可看",
            "not_found": "未找到会话",
            "failed": "索要失败",
            "": "",
        }.get(item.get("resume_request_status", ""), item.get("resume_request_status", ""))
        communicate_text = " / ".join(part for part in (communicate_text, resume_text) if part)
        self.result_tree.insert(
            "",
            "end",
            values=(
                item.get("index", ""),
                item.get("name", ""),
                target_text,
                result_text,
                item.get("score", 0),
                communicate_text,
                item.get("reason", ""),
            ),
        )

    @staticmethod
    def add_entry(
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        width: int | None = None,
        colspan: int = 1,
        sticky_value: str = "ew",
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=6)
        entry = ttk.Entry(parent, textvariable=variable, width=width)
        entry.grid(row=row, column=1, columnspan=colspan, sticky=sticky_value, pady=6)

    @staticmethod
    def add_combo(parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, values: tuple[str, ...]) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=6)
        ttk.Combobox(parent, textvariable=variable, values=values, state="readonly").grid(
            row=row, column=1, columnspan=2, sticky="ew", pady=6
        )

    def run_apply(self) -> None:
        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            self.status_var.set("端口必须是数字")
            return
        try:
            candidate_limit = max(int(self.candidate_limit_var.get().strip() or "1"), 1)
        except ValueError:
            self.status_var.set("处理人数必须是数字")
            return

        selected_chat_job = self.chat_job_by_label.get(self.chat_job_var.get().strip())
        filters = SearchFilters(
            selected_chat_job=selected_chat_job,
            match_requirements=self.ai_requirements_text.get("1.0", "end").strip(),
            deepseek_api_key=self.deepseek_api_key_var.get().strip(),
            deepseek_model=self.deepseek_model_var.get().strip(),
            auto_communicate=self.auto_communicate_var.get(),
            candidate_limit=candidate_limit,
            keywords=self.keywords_var.get().strip(),
            job_name=self.job_var.get().strip(),
            company_name=self.company_var.get().strip(),
            current_city=self.current_city_var.get().strip(),
            expected_city=self.expected_city_var.get().strip(),
            experience=self.experience_var.get().strip(),
            education=self.education_var.get().strip(),
            recruitment_type=self.recruitment_type_var.get().strip(),
            school_types=",".join(label for label, var in self.school_type_vars.items() if var.get()),
            active_status=self.active_status_var.get().strip(),
            job_status=self.job_status_var.get().strip(),
            job_hop_frequency=self.job_hop_frequency_var.get().strip(),
            age_requirement=self.age_requirement_var.get().strip(),
            gender_requirement=self.gender_requirement_var.get().strip(),
            language_requirement=self.language_requirement_var.get().strip(),
            graduation_year=self.graduation_year_var.get().strip(),
            use_keywords_ai_words=self.keywords_ai_var.get(),
            use_job_ai_words=self.job_ai_var.get(),
            use_company_ai_words=self.company_ai_var.get(),
        )
        self.status_var.set("正在操作浏览器...")
        self.clear_candidate_results()
        self.clear_logs()
        self.append_log("开始运行：获取职位、填筛选、搜索候选人")
        threading.Thread(target=self.apply_filters, args=(port, filters), daemon=True).start()

    def run_refresh_jobs(self) -> None:
        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            self.status_var.set("端口必须是数字")
            return

        self.status_var.set("正在获取职位列表...")
        threading.Thread(target=self.refresh_jobs, args=(port,), daemon=True).start()

    def refresh_jobs(self, port: int) -> None:
        try:
            page = LiepinSearchPage(port=port)
            jobs = page.fetch_job_list()
            self.messages.put(("jobs", jobs))
        except Exception as exc:
            self.messages.put(f"获取职位失败：{exc}")

    def update_job_options(self, jobs: list[dict]) -> None:
        current = self.chat_job_var.get().strip()
        self.chat_job_options = jobs
        self.chat_job_by_label = {self.format_job_label(job): job for job in jobs}
        labels = tuple(self.chat_job_by_label.keys())
        self.chat_job_combo.configure(values=labels)
        if current in self.chat_job_by_label:
            self.chat_job_var.set(current)
        elif labels:
            self.chat_job_var.set(labels[0])
        else:
            self.chat_job_var.set("")
        self.status_var.set(f"已获取 {len(jobs)} 个职位")

    @staticmethod
    def format_job_label(job: dict) -> str:
        return job.get("label") or " | ".join(
            part for part in (job.get("title"), job.get("city"), job.get("salary")) if part
        )

    def apply_filters(self, port: int, filters: SearchFilters) -> None:
        try:
            page = LiepinSearchPage(
                port=port,
                progress_callback=lambda event: self.messages.put(("log", event)),
            )
            self.messages.put(("log", {"message": "正在获取职位列表..."}))
            jobs = page.fetch_job_list()
            self.messages.put(("jobs", jobs))
            if not filters.selected_chat_job and jobs:
                filters.selected_chat_job = jobs[0]
            self.messages.put(("log", {"message": f"已获取 {len(jobs)} 个职位，准备进入搜索页"}))
            page.open()
            self.messages.put(("log", {"message": "已进入搜索页，开始填入筛选条件并搜索"}))
            decision = page.apply_filters(filters)
            if decision and "processed" in decision:
                self.messages.put(("results", decision.get("results", [])))
                self.messages.put(
                    f"批量完成：处理 {decision.get('processed', 0)} 人，匹配 {decision.get('matched', 0)} 人"
                )
            elif decision and decision.get("match"):
                self.messages.put(f"AI判断通过：{decision.get('score', 0)}分，已点击立即沟通")
            elif decision:
                self.messages.put(f"AI判断未通过：{decision.get('score', 0)}分，{decision.get('reason', '')}")
            else:
                self.messages.put("已完成筛选并提取候选人信息")
        except Exception as exc:
            self.messages.put(("log", {"message": f"失败：{exc}"}))
            self.messages.put(f"失败：{exc}")

    def poll_messages(self) -> None:
        try:
            while True:
                message = self.messages.get_nowait()
                if isinstance(message, tuple) and message[0] == "jobs":
                    self.update_job_options(message[1])
                elif isinstance(message, tuple) and message[0] == "results":
                    if not self.result_rows:
                        self.update_candidate_results(message[1])
                elif isinstance(message, tuple) and message[0] == "log":
                    payload = message[1]
                    if isinstance(payload, dict) and payload.get("event") == "candidate_result":
                        self.add_candidate_result(payload.get("data", {}))
                    self.append_log(payload.get("message", str(payload)) if isinstance(payload, dict) else str(payload))
                else:
                    self.status_var.set(message)
        except queue.Empty:
            pass
        self.after(200, self.poll_messages)


def main() -> None:
    root = tk.Tk()
    LiepinApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
